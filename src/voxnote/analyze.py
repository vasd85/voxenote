from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from typing import Any, Dict

from datetime import datetime
from pathlib import Path

import requests

from .config import DEFAULT_CONFIG_PATH
from .models import AppConfig, NoteAnalysis
from .runtime import build_runtime

USER_PROMPT_PREFIX = "Analyze the following note and respond ONLY with JSON. Note text:\n"

DEFAULT_NUM_CTX = 16384  # 16384, 32768

logger = logging.getLogger(__name__)

# Tokenization ratios for Qwen2.5:
# - English: ~3-4 chars/token (ASCII: ~3-4 bytes/token)
# - Russian: ~2.5 chars/token (UTF-8: ~5 bytes/token)
# We use conservative estimates to avoid underestimating
BYTES_PER_TOKEN_ENGLISH = 3.5
BYTES_PER_TOKEN_RUSSIAN = 5.0
TOKEN_SAFETY_MULTIPLIER = 1.1


def _estimate_tokens_conservative(text: str) -> int:
    """
    Conservative token estimate without a tokenizer.
    """
    if not text:
        return 0

    n_bytes = len(text.encode("utf-8"))
    n_chars = len(text)

    # Estimate ratio of Cyrillic vs Latin characters
    cyrillic_chars = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    cyrillic_ratio = cyrillic_chars / n_chars if n_chars > 0 else 0.0

    # Weighted average bytes per token based on language mix
    # Pure English: ~3.5 bytes/token, Pure Russian: ~5 bytes/token
    avg_bytes_per_token = (
        BYTES_PER_TOKEN_ENGLISH * (1 - cyrillic_ratio)
        + BYTES_PER_TOKEN_RUSSIAN * cyrillic_ratio
    )

    approx = math.ceil(n_bytes / avg_bytes_per_token)
    return math.ceil(approx * TOKEN_SAFETY_MULTIPLIER)


def _try_count_tokens_via_ollama(config: AppConfig, *, prompt: str) -> int | None:
    """
    Try to get an exact token count via Ollama `/api/tokenize`.

    If the endpoint is unavailable (older Ollama) or returns an unexpected payload,
    return None and let the caller fall back to a heuristic.
    """
    url = config.llm.base_url.rstrip("/") + "/api/tokenize"
    try:
        resp = requests.post(
            url,
            json={"model": config.llm.model, "prompt": prompt},
            timeout=float(getattr(config.llm, "tokenize_timeout_s", 60)),
        )
    except requests.RequestException:
        logger.debug(f"Failed to call Ollama /api/tokenize, falling back to heuristic")
        return None

    if resp.status_code == 404:
        logger.debug(f"Ollama /api/tokenize is not available, falling back to heuristic")
        return None

    if resp.status_code != 200:
        logger.debug(f"Ollama /api/tokenize returned {resp.status_code}, falling back to heuristic")
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.debug(f"Failed to parse JSON from Ollama /api/tokenize response, falling back to heuristic")
        return None

    tokens = data.get("tokens")
    if isinstance(tokens, list):
        return len(tokens)

    token_count = data.get("token_count") or data.get("count")
    if isinstance(token_count, int):
        return token_count

    logger.debug(f"Failed to get token count from Ollama /api/tokenize response, falling back to heuristic")
    return None


def _count_tokens_with_fallback(config: AppConfig, *, prompt: str) -> int:
    tokens = _try_count_tokens_via_ollama(config, prompt=prompt)
    if tokens is None:
        tokens = _estimate_tokens_conservative(prompt)
    return tokens


def _needs_truncation(*, prompt_tokens_est: int) -> bool:
    """
    Check if note text needs truncation to fit within DEFAULT_NUM_CTX.
    """
    return prompt_tokens_est > DEFAULT_NUM_CTX


def _debug_log_llm(
    config: AppConfig,
    *,
    note_text: str,
    payload: Dict[str, Any],
    raw_content: str,
    error: str,
    state_dir: Path | None = None,
) -> None:
    """Append raw LLM exchange to debug log when debug mode is enabled."""
    if not getattr(config.llm, "debug", False):
        return

    try:
        state_root = (state_dir or (DEFAULT_CONFIG_PATH.parent / ".voxnote")).expanduser().resolve()
        state_root.mkdir(parents=True, exist_ok=True)
        path = state_root / "llm_debug.jsonl"

        note_hash = hashlib.sha256(note_text.encode("utf-8")).hexdigest()

        entry = {
            "ts": datetime.now().isoformat(),
            "model": config.llm.model,
            "error": error,
            "note_len": len(note_text),
            "note_sha256": note_hash,
            "payload": {
                "model": payload.get("model"),
                "format": payload.get("format"),
                "options": payload.get("options"),
            },
            "response_content": raw_content,
        }

        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort debug logging; never break main flow
        pass


def _build_payload(config: AppConfig, text: str) -> Dict[str, Any]:
    user_content = USER_PROMPT_PREFIX + text
    return {
        "model": config.llm.model,
        "format": "json",
        "messages": [
            {"role": "system", "content": config.prompts.system_prompt},
            {
                "role": "user",
                "content": user_content,
            },
        ],
        "stream": bool(getattr(config.llm, "stream", True)),
    }


def _extract_streamed_chat_content(resp: requests.Response) -> str:
    """
    Extract `message.content` from Ollama streamed `/api/chat` response.

    Ollama returns JSON objects line-by-line. We concatenate incremental `message.content` chunks.
    """
    parts: list[str] = []
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(obj, dict) and obj.get("error"):
            error_msg = obj.get('error', 'Unknown error')
            raise RuntimeError(
                f"Ollama stream error: {error_msg}. "
                f"Check that Ollama is running: `ollama serve` or verify the model exists: `ollama list`"
            )

        msg = (obj.get("message") or {}) if isinstance(obj, dict) else {}
        chunk = msg.get("content")
        if isinstance(chunk, str) and chunk:
            parts.append(chunk)

        if isinstance(obj, dict) and obj.get("done") is True:
            break

    return "".join(parts).strip()


def _post_ollama_chat_with_retries(
    config: AppConfig,
    *,
    url: str,
    payload: Dict[str, Any],
) -> str:
    max_retries = int(getattr(config.llm, "max_retries", 2))
    backoff_s = float(getattr(config.llm, "retry_backoff_s", 2.0))
    timeout_s = float(getattr(config.llm, "chat_timeout_s", 120))

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            if payload.get("stream") is True:
                with requests.post(url, json=payload, timeout=(10, timeout_s), stream=True) as resp:
                    if resp.status_code != 200:
                        error_preview = resp.text[:200] if resp.text else "No error details"
                        raise RuntimeError(
                            f"Ollama returned {resp.status_code}: {error_preview}. "
                            f"Check that Ollama is running at {url} and the model '{config.llm.model}' is available: `ollama list`"
                        )
                    return _extract_streamed_chat_content(resp)

            resp = requests.post(url, json=payload, timeout=(10, timeout_s))
            if resp.status_code != 200:
                error_preview = resp.text[:200] if resp.text else "No error details"
                raise RuntimeError(
                    f"Ollama returned {resp.status_code}: {error_preview}. "
                    f"Check that Ollama is running at {url} and the model '{config.llm.model}' is available: `ollama list`"
                )
            data = resp.json()
            message = data.get("message") or {}
            content = message.get("content", "")
            if not isinstance(content, str):
                raise RuntimeError(
                    "Ollama returned invalid response content type. "
                    f"Check that the model '{config.llm.model}' supports JSON format. "
                    "Try a different model or check Ollama logs."
                )
            return content.strip()
        except (requests.Timeout, requests.ConnectionError, requests.RequestException, RuntimeError) as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            time.sleep(backoff_s * (2**attempt))

    assert last_exc is not None
    raise last_exc


def _truncate_note_text(
    config: AppConfig,
    *,
    note_text: str,
    max_tokens: int,
) -> str:
    """
    Truncate note text from the end to fit within max_tokens.

    Preserves system prompt and user instruction prefix, only truncates
    the actual note content from the end.
    """
    system_tokens = _count_tokens_with_fallback(config, prompt=config.prompts.system_prompt)

    prefix_tokens = _count_tokens_with_fallback(config, prompt=USER_PROMPT_PREFIX)

    reserved_tokens = system_tokens + prefix_tokens + 512
    available_tokens = max_tokens - reserved_tokens

    if available_tokens <= 0:
        raise ValueError(
            "Context window too small: "
            f"max_tokens={max_tokens}, reserved_tokens={reserved_tokens} "
            f"(system={system_tokens}, prefix={prefix_tokens}, completion_reserve=512)."
        )

    note_tokens = _count_tokens_with_fallback(config, prompt=note_text)

    if note_tokens <= available_tokens:
        return note_text

    truncation_marker = "\n[... text truncated due to context limit ...]"
    marker_tokens = _count_tokens_with_fallback(config, prompt=truncation_marker)

    available_for_note = available_tokens - marker_tokens
    if available_for_note <= 0:
        raise ValueError(
            "Context window too small to include truncation marker: "
            f"max_tokens={max_tokens}, reserved_tokens={reserved_tokens}, available_tokens={available_tokens}, "
            f"marker_tokens={marker_tokens}."
        )

    ratio = available_for_note / note_tokens
    target_bytes = int(ratio * len(note_text.encode("utf-8")) * 0.95)

    truncated = note_text.encode("utf-8")[:target_bytes].decode("utf-8", errors="ignore")

    for _ in range(5):
        test_tokens = _count_tokens_with_fallback(config, prompt=truncated)

        if test_tokens <= available_for_note:
            break

        target_bytes = int(target_bytes * 0.85)
        truncated = note_text.encode("utf-8")[:target_bytes].decode("utf-8", errors="ignore")

    return truncated.rstrip() + truncation_marker


def analyze_text(config: AppConfig, text: str, *, state_dir: Path | None = None) -> NoteAnalysis:
    url = config.llm.base_url.rstrip("/") + "/api/chat"
    payload = _build_payload(config, text)

    prompt_for_count = config.prompts.system_prompt + "\n" + USER_PROMPT_PREFIX + text
    prompt_tokens = _count_tokens_with_fallback(config, prompt=prompt_for_count)

    logger.info(f"Estimated prompt tokens: {prompt_tokens}, text length: {len(text)} chars")

    if _needs_truncation(prompt_tokens_est=prompt_tokens):
        logger.warning(
            f"Note text is too large ({prompt_tokens} tokens estimated). "
            f"Truncating from the end to fit within {DEFAULT_NUM_CTX} token context window."
        )
        text = _truncate_note_text(config, note_text=text, max_tokens=DEFAULT_NUM_CTX)
        payload = _build_payload(config, text)

    payload["options"] = {"num_ctx": DEFAULT_NUM_CTX}

    try:
        content = _post_ollama_chat_with_retries(config, url=url, payload=payload)
    except Exception as exc:
        _debug_log_llm(
            config,
            note_text=text,
            payload=payload,
            raw_content="",
            error=str(exc),
            state_dir=state_dir,
        )
        raise RuntimeError(
            f"Failed to call Ollama at {url}: {exc}. "
            f"Check that Ollama is running: `ollama serve` or verify the base_url in config.yaml (llm.base_url). "
            f"Test connection: `curl {url}/api/tags`"
        ) from exc
    if not content:
        raise RuntimeError(
            "Empty response content from Ollama. "
            f"The model '{config.llm.model}' may not be responding correctly. "
            "Try a different model or check Ollama logs: `ollama logs`"
        )

    try:
        obj = json.loads(content)
    except json.JSONDecodeError as exc:
        # Fallback: try to extract JSON block if model added extra text
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(content[start : end + 1])
            except json.JSONDecodeError as exc2:  # type: ignore[no-redef]
                msg = (
                    "Failed to parse JSON from Ollama response (fallback failed). "
                    f"The model '{config.llm.model}' may not be following the JSON format requirement. "
                    "Check the system prompt in config.yaml (prompts.system_prompt) or try a different model."
                )
                _debug_log_llm(
                    config, note_text=text, payload=payload, raw_content=content, error=msg, state_dir=state_dir
                )
                raise RuntimeError(msg) from exc2
        else:
            msg = (
                "Failed to parse JSON from Ollama response. "
                f"The model '{config.llm.model}' may not be following the JSON format requirement. "
                "Check the system prompt in config.yaml (prompts.system_prompt) or try a different model."
            )
            _debug_log_llm(
                config, note_text=text, payload=payload, raw_content=content, error=msg, state_dir=state_dir
            )
            raise RuntimeError(msg) from exc
    
    if not isinstance(obj, dict):
        msg = (
            "Ollama returned JSON that is not an object. "
            f"The model '{config.llm.model}' may not be following the expected format. "
            "Check the system prompt in config.yaml (prompts.system_prompt) or try a different model."
        )
        _debug_log_llm(
            config, note_text=text, payload=payload, raw_content=content, error=msg, state_dir=state_dir
        )
        raise RuntimeError(msg)

    missing_keys = [k for k in ("title", "category") if k not in obj]
    if missing_keys:
        msg = (
            f"Ollama JSON is missing required keys {missing_keys}. "
            f"The model '{config.llm.model}' may not be following the system prompt correctly. "
            "Check the system prompt in config.yaml (prompts.system_prompt) or try a different model."
        )
        _debug_log_llm(
            config, note_text=text, payload=payload, raw_content=content, error=msg, state_dir=state_dir
        )
        raise RuntimeError(msg)

    return NoteAnalysis(**obj)


def cli_analyze_text(text: str) -> None:
    """
    Helper function for manual testing/debugging of text analysis.

    This is a convenience wrapper that loads config, calls analyze_text(),
    and prints the result as JSON. Useful for:
    - Testing Ollama connection and model responses in isolation
    - Debugging failed transcriptions (paste text from failed_transcriptions.jsonl)
    - Tuning SYSTEM_PROMPT and seeing immediate JSON output

    Usage from Python REPL or script:
        from voxnote.analyze import cli_analyze_text
        cli_analyze_text("your note text here...")

    Note: This function prints to stdout and returns None. Do not use in production
    pipeline code; use analyze_text() directly instead.
    """
    runtime = build_runtime()
    analysis = analyze_text(runtime.config, text, state_dir=runtime.state_dir)
    print(json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2))
