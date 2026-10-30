# T5 STT implementation and acceptance report

Implemented local Whisper service, async/sync contract, per-session automatic language switching, OpenCC normalization, shared lexicon prompts, reservation-code extraction, and benchmark CLI.

## Acceptance and actual results

| Check | Command / evidence | Result |
|---|---|---|
| Unit tests | `.venv/bin/python -m pytest tests/test_stt.py -q` | 5 passed in 0.14 s |
| FLEURS references | `.venv/bin/python scripts/bench_stt.py --refs --output reports/stt/fleurs` | 5/5 nonempty transcripts, exit 0 |
| Four-language say WAVs | `.venv/bin/python scripts/bench_stt.py --manifest reports/stt/say/manifest.json --output reports/stt/say-specified` | 4/4 nonempty transcripts, exit 0 |
| Automatic detection | `.venv/bin/python scripts/bench_stt.py --manifest reports/stt/say/manifest.json --auto --output reports/stt/say-auto` | 4/4 detected correctly; probability 0.9981–0.9992 |
| Async PCM16 / session switching | Python integration using `await stt.transcribe(pcm, auto=True, state=state)`; `async-switch.json` | 4 calls; response locales zh-TW → en → en → zh-TW; both English codes ZV-4821 |
| zh-TW script | `fleurs.json`, `say-specified.json`, OpenCC unit assertion | 繁體: 被測化學品中的氫; 請幫我確認訂房; 計程車 |
| T3-produced four-language WAVs | Final `rg --files data reports -g "*.wav"` | Pending: no T3 WAV existed at completion check; fallback verification authorized by dispatch |

## Measured latency

| Corpus / mode | en ms | zh-CN ms | zh-TW ms | ja ms | yue ms |
|---|---:|---:|---:|---:|---:|
| fleurs | 4500.7 | 709.7 | 593.2 | 637.6 | 597.1 |
| say-specified | 2537.5 | 617.9 | 934.9 | 589.6 | — |
| say-auto | 2333.3 | 1105.0 | 1079.6 | 1056.8 | — |

Each process first loads the model on en. Other workers were running; these numbers are functional smoke observations and cannot be treated as isolated warmed benchmarks.

## Files

- `server/stt/base.py`: result/protocol, language mapping, session state.
- `server/stt/mlx_whisper_stt.py`: local-only model resolution, serialized MLX execution, actual detection probability, shared dictionary prompt, async PCM/file API.
- `server/stt/script.py`: OpenCC conversion preserving caller raw text.
- `server/stt/README.md`: integration contract and reproduction commands.
- `scripts/bench_stt.py`: reference/manifest/explicit WAV bench; JSON + Markdown output.
- `tests/test_stt.py`: five tests covering state hysteresis, Chinese preference preservation, OpenCC, input validation, local paths/prompts and cancellation state isolation.
- `reports/stt/{fleurs,say-specified,say-auto}.{json,md}`, `reports/stt/async-switch.json`, and this report.
- `reports/stt/say/{en,zh-CN,zh-TW,ja}.wav` and `manifest.json`: macOS say inputs and exact reference text/voice metadata.
- `docs/DEVIATIONS.md`: appended one T5 fallback/concurrency/optional-model note.

## Input provenance and limitations

FLEURS originals are untouched: Google FLEURS contributors, CC-BY-4.0, source/revision/transcripts in `data/refs/MANIFEST.json`. Taiwan shares mainland Mandarin audio (대만 억양 참조 없음).
MacOS say generated each manifest display_text with `say -v VOICE -o locale.aiff TEXT`, then `ffmpeg -v error -y -i locale.aiff -ar 16000 -ac 1 locale.wav`; temporary AIFF files were removed. Voices: Samantha, Tingting, Meijia, Kyoko. These are functional STT inputs, not comparative TTS quality evidence.
Optional Qwen3-ASR was not downloaded or implemented. No new models, packages, or paid APIs were used. Disk: about 11 GiB before work, 7.3 GiB at final measurement (other workers active); T5 WAVs/reports are small.
Forced language mode reports null detection/probability honestly. Auto response-language switching requires caller-owned state; Chinese region preference never derives from audio. Cancellation cannot stop an already-running GPU decode, but a cancelled request cannot mutate the session state; later queued inference waits for the active decode.
Transcription errors remain visible in raw/display output (for example FLEURS English splendorous and Cantonese proper-name errors). No CER/WER or accent-quality claims are made from these smoke samples.

## Remaining T3 acceptance command

Run once T3 writes WAVs (replace paths with its actual outputs):

```sh
.venv/bin/python scripts/bench_stt.py --wav en=PATH_EN --wav zh-CN=PATH_CN --wav zh-TW=PATH_TW --wav ja=PATH_JA --output reports/stt/t3
```
