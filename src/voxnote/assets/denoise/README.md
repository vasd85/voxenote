# Denoise model (ffmpeg `arnndn`)

This project uses ffmpeg's `arnndn` filter during audio preparation for VAD.

## Notes

- The file is a binary model used by ffmpeg. Do not rename it unless you also update `src/voxnote/audio_prepare.py`.
- If the file is missing, `collect` will fail when preparing audio.

