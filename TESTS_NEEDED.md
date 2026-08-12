# Tests Needed: Coverage Checklist

## Status: ✅ Checklist prepared

---

## Format

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| ... | ... | ... | ... | ... | ... |

**Legend:**
- **Component**: Module/function to test
- **Scenario**: Scenario / edge case
- **Expected**: Expected behavior
- **Mocks**: What to mock
- **Priority**: P0 (critical), P1 (important), P2 (nice-to-have), P3 (optional)
- **Status**: `TODO`, `In Progress`, `Done`

---

## Unit Tests

### `config.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `load_config()` | Valid config with relative paths | Paths normalized to absolute | None | P1 | TODO |
| `load_config()` | Valid config with `~` in paths | Paths expanded via `expanduser()` | None | P1 | TODO |
| `load_config()` | Missing config file | Raises `FileNotFoundError` with helpful message | None | P1 | TODO |
| `load_config()` | Invalid YAML | Raises `ValidationError` | None | P1 | TODO |
| `load_config()` | Invalid config structure | Raises `ValidationError` with field errors | None | P1 | TODO |
| `_normalize_paths()` | Relative paths in `paths` section | Paths resolved relative to config dir | None | P2 | TODO |
| `_normalize_paths()` | Relative paths in `sources` section | Paths resolved relative to config dir | None | P2 | TODO |

### `state.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `compute_file_hash()` | Small file | Returns SHA256 hex string | None | P1 | TODO |
| `compute_file_hash()` | Large file (chunked) | Returns SHA256 hex string | None | P1 | TODO |
| `load_processed_hashes()` | Empty index file | Returns empty set | None | P1 | TODO |
| `load_processed_hashes()` | Index with valid entries | Returns set of hashes | None | P1 | TODO |
| `load_processed_hashes()` | Index with invalid JSON lines | Skips invalid lines, continues | None | P2 | TODO |
| `append_processed_entry()` | New entry | Appends to JSONL file | None | P1 | TODO |
| `append_processed_entry()` | Entry with same `original_hash` | Purges old entry, appends new | None | P1 | TODO |
| `find_processed_entry()` | Entry exists | Returns entry dict | None | P1 | TODO |
| `find_processed_entry()` | Entry not found | Returns `None` | None | P1 | TODO |
| `get_failed_transcription_text()` | Entry exists | Returns text string | None | P1 | TODO |
| `get_failed_transcription_text()` | Entry not found | Returns `None` | None | P1 | TODO |
| `purge_failed_transcription()` | Entry exists | Removes entry from JSONL | None | P1 | TODO |
| `purge_failed_transcription()` | Entry not found | No-op | None | P2 | TODO |
| `save_original_metadata()` | New metadata | Appends to JSONL | None | P1 | TODO |
| `save_original_metadata()` | Metadata with same `original_hash` | Purges old, appends new | None | P1 | TODO |
| `load_original_metadata()` | Metadata exists | Returns `AudioMetadata` | None | P1 | TODO |
| `load_original_metadata()` | Metadata not found | Returns `None` | None | P1 | TODO |

### `cache_paths.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `build_prepared_cache_path()` | Normal filename | Returns deterministic path with hash prefix | None | P1 | TODO |
| `build_prepared_cache_path()` | Filename with hash prefix | Strips prefix, uses rest | None | P2 | TODO |
| `build_trimmed_cache_path()` | Normal case | Returns deterministic path | None | P1 | TODO |
| `find_prepared_cache_path()` | Cache exists | Returns path to cached file | None | P1 | TODO |
| `find_prepared_cache_path()` | Cache not found | Returns `None` | None | P1 | TODO |
| `find_prepared_cache_path()` | Multiple cache files | Returns most recent (by mtime) | None | P2 | TODO |
| `strip_hash_prefix()` | Filename with hash prefix | Returns (hash, rest) | None | P1 | TODO |
| `strip_hash_prefix()` | Filename without prefix | Returns (None, filename) | None | P1 | TODO |

### `collect_plan.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `build_collect_source_plan()` | No CLI sources, config has sources | Uses config sources with config recursive | None | P1 | ✅ Done |
| `build_collect_source_plan()` | CLI sources match config | Uses config recursive setting | None | P1 | ✅ Done |
| `build_collect_source_plan()` | CLI sources not in config, `auto` mode | Uses `recursive_default` | None | P1 | ✅ Done |
| `build_collect_source_plan()` | `recursive_mode="on"` | All sources recursive=True | None | P1 | ✅ Done |
| `build_collect_source_plan()` | `recursive_mode="off"` | All sources recursive=False | None | P1 | ✅ Done |

### `organize.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `_slugify()` | Normal text | Returns slugified string | None | P1 | TODO |
| `_slugify()` | Unicode text | Normalizes to NFKC | None | P2 | TODO |
| `_slugify()` | Empty string | Returns "note" | None | P2 | TODO |
| `_slugify()` | Very long text | Truncates to max_length | None | P2 | TODO |
| `organize_note()` | File in `input/` | Moves to `archive/`, creates note | Mock file ops | P1 | ✅ Done |
| `organize_note()` | External file | Moves to `archive/`, creates note | Mock file ops | P1 | ✅ Done |
| `organize_note()` | Note filename collision | Appends ID fragment | Mock file ops | P2 | TODO |
| `organize_note()` | Error during note write | Rollback: returns audio to input/ | Mock file ops | P0 | TODO |
| `_build_markdown()` | Normal case | Returns formatted Markdown | None | P1 | TODO |
| `_build_markdown()` | With metadata dump | Includes metadata section | None | P2 | TODO |

### `analyze.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `_estimate_tokens_conservative()` | English text | Returns conservative estimate | None | P1 | TODO |
| `_estimate_tokens_conservative()` | Russian text | Returns conservative estimate | None | P1 | TODO |
| `_estimate_tokens_conservative()` | Mixed language | Returns weighted estimate | None | P2 | TODO |
| `_try_count_tokens_via_ollama()` | Successful call | Returns token count | Mock `requests.post` | P1 | TODO |
| `_try_count_tokens_via_ollama()` | 404 response | Returns `None` (fallback) | Mock `requests.post` | P1 | TODO |
| `_try_count_tokens_via_ollama()` | Network error | Returns `None` (fallback) | Mock `requests.post` | P1 | TODO |
| `_truncate_note_text()` | Text fits | Returns original text | Mock token counting | P1 | ✅ Done |
| `_truncate_note_text()` | Text too large | Truncates from end, adds marker | Mock token counting | P1 | ✅ Done |
| `_truncate_note_text()` | Context window too small | Raises `ValueError` | Mock token counting | P1 | ✅ Done |
| `_extract_streamed_chat_content()` | Valid stream | Concatenates content chunks | Mock `requests.Response` | P1 | TODO |
| `_extract_streamed_chat_content()` | Stream with error | Raises `RuntimeError` | Mock `requests.Response` | P1 | TODO |
| `_post_ollama_chat_with_retries()` | Successful call | Returns content string | Mock `requests.post` | P1 | TODO |
| `_post_ollama_chat_with_retries()` | Timeout, then success | Retries and succeeds | Mock `requests.post` | P1 | TODO |
| `_post_ollama_chat_with_retries()` | All retries fail | Raises last exception | Mock `requests.post` | P1 | TODO |
| `analyze_text()` | Valid JSON response | Returns `NoteAnalysis` | Mock `_post_ollama_chat_with_retries` | P1 | TODO |
| `analyze_text()` | JSON with extra text | Extracts JSON block | Mock `_post_ollama_chat_with_retries` | P2 | TODO |
| `analyze_text()` | Missing required keys | Raises `RuntimeError` | Mock `_post_ollama_chat_with_retries` | P1 | TODO |
| `analyze_text()` | Invalid JSON | Raises `RuntimeError` | Mock `_post_ollama_chat_with_retries` | P1 | TODO |

### `transcribe.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `_find_mlx_whisper()` | Found in PATH | Returns path | Mock `shutil.which` | P1 | TODO |
| `_find_mlx_whisper()` | Found in venv | Returns venv path | Mock `shutil.which`, `sys.executable` | P1 | TODO |
| `_find_mlx_whisper()` | Not found | Raises `RuntimeError` | Mock `shutil.which` | P1 | TODO |
| `_remove_repetitions()` | No repetitions | Returns original text | None | P1 | TODO |
| `_remove_repetitions()` | Excessive repetitions | Removes excess, keeps max_repeats | None | P1 | TODO |
| `_run_mlx_whisper()` | Successful transcription | Returns text | Mock `subprocess.run` | P1 | TODO |
| `_run_mlx_whisper()` | Process fails | Raises `RuntimeError` with context | Mock `subprocess.run` | P1 | TODO |
| `_run_mlx_whisper()` | No output file created | Raises `RuntimeError` | Mock `subprocess.run` | P1 | TODO |
| `_run_mlx_whisper()` | Empty output file | Raises `RuntimeError` | Mock `subprocess.run` | P1 | TODO |
| `transcribe_file()` | Valid audio file | Returns `TranscriptionResult` | Mock `_run_mlx_whisper` | P1 | TODO |
| `transcribe_file()` | Unsupported format | Raises `ValueError` | None | P1 | TODO |
| `transcribe_file()` | File not found | Raises `FileNotFoundError` | None | P1 | TODO |
| `_run_mlx_whisper()` | `word_timestamps=True` | Requests `--output-format json --word-timestamps True`, never `--clip-timestamps` | Mock `subprocess.run` | P0 | Done |
| `_run_mlx_whisper()` | `word_timestamps=False` | Keeps the plain `txt` path, no segments | Mock `subprocess.run` | P0 | Done |
| `_run_mlx_whisper()` | Unreadable JSON output | `RuntimeError` naming the diarization opt-out | Mock `subprocess.run` | P1 | Done |
| `_parse_whisper_json()` | Segments with words | Segment/word timings parsed | None | P0 | Done |
| `_parse_whisper_json()` | Missing timings, junk entries | Coerced to segment bounds, junk skipped | None | P1 | Done |
| `_drop_repeated_segments()` | Run longer than max_repeats | Keeps two copies with their own timings | None | P1 | Done |
| `_drop_repeated_segments()` | Short run | Keeps every segment | None | P1 | Done |

### `vad_trim.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `_find_ffmpeg()` | Found in PATH | Returns path | Mock `shutil.which` | P1 | TODO |
| `_find_ffmpeg()` | Not found | Raises `RuntimeError` | Mock `shutil.which` | P1 | TODO |
| `_load_silero_vad_model()` | Successful load | Returns (model, utils) | Mock `torch.hub.load` | P1 | TODO |
| `_load_silero_vad_model()` | torchaudio missing | Raises `RuntimeError` | Mock `torch.hub.load` | P1 | TODO |
| `_detect_speech_segments()` | Speech detected | Returns list of segments | Mock model, utils | P1 | TODO |
| `_detect_speech_segments()` | No speech | Returns empty list | Mock model, utils | P1 | TODO |
| `_build_ffmpeg_filter()` | Single segment | Returns filter string | None | P1 | TODO |
| `_build_ffmpeg_filter()` | Overlapping segments | Merges intervals | None | P1 | TODO |
| `_build_ffmpeg_filter()` | No segments | Returns empty string | None | P1 | TODO |
| `trim_audio_file()` | Successful trim | Returns `True`, creates cache | Mock VAD, ffmpeg | P1 | TODO |
| `trim_audio_file()` | No speech detected | Returns `False` | Mock VAD | P1 | TODO |
| `trim_audio_file()` | `dry_run=True` | Returns `True`, no files created | Mock VAD | P1 | TODO |
| `trim_audio_file()` | EXDEV error | Falls back to `shutil.move()` | Mock `os.replace` | P2 | TODO |

### `speaker_merge.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `assign_speakers()` | Words spanning two turns | Each word goes to its max-overlap turn | None | P0 | Done |
| `assign_speakers()` | Word with no overlapping turn | Falls back to the nearest turn | None | P0 | Done |
| `assign_speakers()` | Non-contiguous engine cluster ids | Labels renumbered 1..N by first appearance | None | P0 | Done |
| `assign_speakers()` | Consecutive same-speaker words | Merged into one block with min/max timings | None | P0 | Done |
| `assign_speakers()` | Segment without word timings | Whole segment assigned as one token | None | P1 | Done |
| `assign_speakers()` | No turns / blank words | Returns `[]` / ignores blanks | None | P1 | Done |
| `join_token_texts()` | Words with and without leading spaces | Single-spaced text either way | None | P1 | Done |
| `render_labeled_transcript()` | Several blocks | `Speaker N: ...` separated by blank lines | None | P1 | Done |

### `diarize.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `diarization_fingerprint()` | Disabled | Returns `off` | None | P0 | Done |
| `diarization_fingerprint()` | Relevant setting changed | Fingerprint changes | None | P0 | Done |
| `diarization_fingerprint()` | Only threads/timeout changed | Fingerprint unchanged | None | P1 | Done |
| `resolve_diarization_models()` | No overrides | Paths under `<state_dir>/diarization/` | None | P1 | Done |
| `resolve_diarization_models()` | Absolute / relative overrides | Absolute kept, relative under models dir | None | P1 | Done |
| `ensure_diarization_models()` | Custom segmentation path missing | Actionable `RuntimeError`, no download | Mock `requests.get` | P0 | Done |
| `ensure_diarization_models()` | Custom embedding path missing | Actionable `RuntimeError`, no download | Mock `requests.get` | P0 | Done |
| `ensure_diarization_models()` | Download fails | Error names file and release page | Mock `requests.get` | P0 | Done |
| `_extract_archive_member()` | Corrupt (non-bz2) archive downloaded | Wrapped `RuntimeError` names member, release page and target | Mock `requests.get` | P0 | Done |
| `_extract_archive_member()` | Truncated archive (valid bz2 prefix) | Same wrapped `RuntimeError` (the `EOFError` path) | Mock `requests.get` | P1 | Done |
| `_extract_archive_member()` | Write fails mid-extraction | Actionable `RuntimeError`, `*.part` removed | Mock `shutil.copyfileobj` | P1 | Done |
| `_extract_archive_member()` | Removing the partial fails too | Actionable `RuntimeError`, not the cleanup's own error | `target.parent` is a file | P1 | Done |
| `_extract_archive_member()` | Archive without the expected member | Actionable `RuntimeError`, hint not duplicated | Mock `requests.get` | P1 | TODO |
| `diarize_audio()` | Unknown backend | Raises `RuntimeError` | None | P1 | Done |
| `diarize_audio()` | sherpa-onnx not importable | Install-hint `RuntimeError` before any model download | Mock `_import_sherpa_onnx`, `ensure_diarization_models` | P1 | Done |
| `_read_wav_mono_16k()` | Non-16k/stereo/non-WAV input | Returns `None` so ffmpeg decode runs | None | P2 | TODO |
| `_debug_log_diarization()` | `llm.debug: true` | Writes turn timings only, no text | None | P2 | TODO |

### `doctor.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `_check_diarization()` | Default models missing | Hint promises the `voxnote process` auto-download | Mock engine probe | P1 | Done |
| `_check_diarization()` | Overridden model path missing | Hint says place the file manually or clear the override; no download promise | Mock engine probe | P0 | Done |
| `_check_diarization()` | Bare-filename embedding override missing | Auto-download promise kept (release file) | Mock engine probe | P1 | Done |
| `_check_diarization()` | Models present (incl. overrides) | Model checks OK | Mock engine probe | P1 | Done |
| `_check_diarization()` | Diarization disabled | Single OK "Disabled" check | Mock engine probe | P1 | Done |

### `audio_prepare.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `_find_ffmpeg()` | Found in PATH | Returns path | Mock `shutil.which` | P1 | TODO |
| `_find_ffmpeg()` | Not found | Raises `RuntimeError` | Mock `shutil.which` | P1 | TODO |
| `prepare_wav_for_vad()` | Successful preparation | Creates WAV file | Mock `subprocess.run` | P1 | TODO |
| `prepare_wav_for_vad()` | FFmpeg fails | Raises `RuntimeError` with stderr | Mock `subprocess.run` | P1 | TODO |
| `prepare_wav_for_vad()` | Empty output file | Raises `RuntimeError` | Mock `subprocess.run` | P1 | TODO |
| `prepare_wav_for_vad()` | EXDEV error | Falls back to `shutil.move()` | Mock `os.replace` | P1 | TODO |

### `audio_metadata.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `collect_audio_metadata()` | All tools available | Returns `AudioMetadata` with all fields | Mock `mdls`, `ffprobe` | P1 | TODO |
| `collect_audio_metadata()` | mdls not available | Returns metadata without mdls | Mock `mdls` failure | P1 | TODO |
| `collect_audio_metadata()` | ffprobe not available | Returns metadata without ffprobe | Mock `ffprobe` failure | P1 | TODO |
| `collect_audio_metadata()` | recorded_at from ffprobe | Uses ffprobe timestamp | Mock `ffprobe` | P1 | TODO |
| `collect_audio_metadata()` | recorded_at from mdls | Uses mdls timestamp | Mock `mdls` | P1 | TODO |
| `collect_audio_metadata()` | recorded_at from stat | Uses stat.st_birthtime | None | P1 | TODO |
| `format_audio_metadata_for_console()` | Normal case | Returns compact JSON | None | P2 | TODO |

### `workflow.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `_assert_in_input()` | Path inside input/ | No exception | None | P1 | TODO |
| `_assert_in_input()` | Path outside input/ | Raises `ValueError` | None | P1 | TODO |
| `prepare_vad_files()` | File already prepared | Yields "skipped" event | Mock cache check | P1 | TODO |
| `prepare_vad_files()` | New file | Yields "processing" → "completed" | Mock `prepare_wav_for_vad` | P1 | TODO |
| `prepare_vad_files()` | Error during preparation | Yields "error" event | Mock `prepare_wav_for_vad` | P1 | TODO |
| `process_files()` | File already processed | Yields "skipped" event | Mock state | P1 | TODO |
| `process_files()` | Successful processing | Yields events: processing → transcribed → analyzed → completed | Mock all dependencies | P1 | TODO |
| `process_files()` | Analysis error | Saves transcription, yields "error" event | Mock `analyze_text` | P1 | TODO |
| `process_files()` | Reuse failed transcription | Uses saved text, skips transcription | Mock state | P1 | TODO |
| `process_files()` | Trimmed cache changed | Reprocesses file | Mock state, cache | P2 | TODO |
| `collect_files()` | File already collected | Skips file | Mock state | P1 | TODO |
| `collect_files()` | New file | Copies to input/, yields events | Mock file ops | P1 | TODO |
| `collect_files()` | PermissionError | Yields "error" event | Mock `shutil.copy2` | P2 | TODO |
| `vad_trim_files()` | File already trimmed | Yields "skipped" event | Mock cache | P1 | TODO |
| `vad_trim_files()` | Successful trim | Yields "completed" event | Mock `trim_audio_file` | P1 | TODO |
| `vad_trim_files()` | No speech detected | Yields "skipped" event | Mock `trim_audio_file` | P1 | TODO |
| `vad_trim_files()` | `dry_run=True` | Yields "completed" without creating files | Mock `trim_audio_file` | P1 | TODO |
| `process_files()` | Diarization disabled | No diarize call, plain text, no `diarized` event, no `Speakers` header | Mock transcribe/analyze | P0 | Done |
| `process_files()` | Diarization on, several speakers | `Speaker N:` note + labeled prompt + `diarized` event counts | Mock transcribe/diarize/analyze | P0 | Done |
| `process_files()` | Diarization on, one speaker | Output identical to disabled run | Mock transcribe/diarize/analyze | P0 | Done |
| `process_files()` | Trimmed cache present | Transcription and diarization use the same file | Mock transcribe/diarize | P0 | Done |
| `process_files()` | Diarization settings changed | File reprocessed with an explanatory `info` event | Mock transcribe/diarize/analyze | P0 | Done |
| `process_files()` | Legacy state entry (no fingerprint) | Still skipped while diarization stays off | Mock transcribe/analyze | P0 | Done |
| `process_files()` | Failed-analysis retry with labels | Reuses labeled text, no re-transcription | Mock transcribe/diarize/analyze | P0 | Done |
| `process_files()` | Failed-analysis retry after settings change | Re-transcribes instead of reusing text | Mock transcribe/diarize/analyze | P1 | Done |
| `process_files()` | Engine/model preflight fails | One error event, zero-count summary, no per-file work | Mock `probe_diarization_engine` | P0 | Done |
| `process_files()` | `diarize_audio` raises mid-file | Actionable error (doctor / `--no-diarize`), transcription never runs | Mock diarize | P0 | Done |
| `process_files()` | `diarize=False` override | Diarization skipped despite config | Mock transcribe/diarize | P1 | Done |

### `cli.py`

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| `_runtime_or_exit()` | Valid config | Returns `RuntimeContext` | Mock `build_runtime` | P1 | TODO |
| `_runtime_or_exit()` | Config not found | Prints error, exits with code 1 | Mock `build_runtime` | P1 | TODO |
| `_runtime_or_exit()` | Invalid config | Prints validation errors, exits with code 2 | Mock `build_runtime` | P1 | TODO |
| `process()` | `--file` outside input/ | Prints error, exits with code 2 | None | P1 | TODO |
| `process()` | Successful processing | Shows progress, prints summary | Mock `Workflow` | P1 | TODO |
| `init()` | Config exists, no `--force` | Prints warning, returns | None | P1 | TODO |
| `init()` | Config exists, `--force` | Overwrites config | None | P1 | TODO |
| `collect()` | No sources | Shows "No sources configured" | Mock `Workflow` | P2 | TODO |
| `status()` | Empty input/ | Shows 0 pending | None | P1 | TODO |
| `status()` | Files in input/ | Shows correct count | None | P1 | TODO |
| `doctor()` | All checks pass | Shows all OK | Mock `run_doctor` | P1 | TODO |
| `doctor()` | Some checks fail | Shows failures, exits with code 1 | Mock `run_doctor` | P1 | TODO |

---

## Integration Tests

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| End-to-end: `collect` → `prepare-vad` → `vad-trim` → `process` | Full workflow | All files processed, notes created | Mock Ollama, mlx_whisper | P1 | TODO |
| End-to-end: Idempotency | Run `process` twice | Second run skips all files | Mock Ollama, mlx_whisper | P1 | TODO |
| End-to-end: Failed analysis retry | Analysis fails, then retry | Reuses saved transcription | Mock Ollama (fail then succeed) | P1 | TODO |
| End-to-end: Cache invalidation | Change VAD params, run `vad-trim --force` | Rebuilds cache | Mock VAD | P2 | TODO |

---

## Regression Tests

| Component | Scenario | Expected | Mocks | Priority | Status |
|-----------|----------|----------|-------|----------|--------|
| Skip "already processed" | File with same hash | Skips processing | Mock state | P1 | TODO |
| Reuse failed transcriptions | Analysis error, then retry | Uses saved text | Mock state, Ollama | P1 | TODO |
| Determinism: trimmed cache paths | Same hash, multiple calls | Same path returned | None | P1 | ✅ Done |
| Determinism: archive filenames | Same inputs | Same UUID generated | Mock `uuid4` | P2 | TODO |
| Path traversal protection | `--file` with `../` | Rejects path | None | P1 | TODO |
| Atomic operations | Error during note write | Rollback: audio not moved | Mock file ops | P0 | TODO |

---

## Priority summary

- **P0 (critical)**: 2 tests
  - Atomic operations rollback
  - Error handling in `organize_note()`

- **P1 (important)**: ~80 tests
  - All core functions and edge cases
  - Regression tests

- **P2 (nice-to-have)**: ~20 tests
  - Unicode handling
  - Cache invalidation
  - Additional edge cases

- **P3 (nice-to-have)**: ~5 tests
  - Performance tests
  - Stress tests

---

## Recommendations

### Immediate actions (P0)

1. **Atomicity test for `organize_note()`:**
   - Verify rollback when note writing fails

### Short-term actions (P1)

1. **Review and align existing tests** (run `pytest` and fix API drifts)
2. **Add regression tests:**
   - Skip "already processed"
   - Reuse failed transcriptions
3. **Add tests for `workflow.py`:**
   - Idempotency
   - Event-driven API
   - Error handling

### Longer-term improvements (P2–P3)

1. **Integration tests** with mocks for external dependencies
2. **Performance tests** for large files
3. **Stress tests** for multiple concurrent operations

---

## Tools and mocking

### Recommended libraries

- **pytest** - already used ✅
- **pytest-mock** - convenient mocking helpers
- **freezegun** - freeze time in tests ✅
- **responses** - mock HTTP calls (Ollama)
- **unittest.mock** - mock subprocess and file ops

### Mocking patterns

1. **External CLIs:**
   ```python
   @pytest.fixture
   def mock_subprocess_run(mocker):
       return mocker.patch('subprocess.run')
   ```

2. **HTTP requests:**
   ```python
   @pytest.fixture
   def mock_requests_post(mocker):
       return mocker.patch('requests.post')
   ```

3. **File operations:**
   ```python
   @pytest.fixture
   def mock_shutil_move(mocker):
       return mocker.patch('shutil.move')
   ```

4. **Time:**
   ```python
   from freezegun import freeze_time
   
   @freeze_time("2024-01-01 12:00:00")
   def test_with_frozen_time():
       ...
   ```

---

## Coverage targets

**Target coverage:**
- Unit tests: 80%+ for all modules
- Integration tests: key end-to-end scenarios
- Regression tests: all known regressions

**Current coverage:** ~10% (estimate)

**Coverage priorities:**
1. P0/P1 tests (critical functions)
2. Regression tests
3. Edge cases
4. Integration tests
