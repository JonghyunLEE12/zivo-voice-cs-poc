# STT service

Use the root uv environment; the model is resolved locally from `models/paths.json`.
No model is downloaded by this component. `Qwen3-ASR` is optional and not implemented
or downloaded in T5 to preserve the remaining disk budget.

```python
from server.stt.mlx_whisper_stt import MLXWhisperSTT
from server.stt.base import LanguageState

stt = MLXWhisperSTT()  # reuse this service across calls
result = await stt.transcribe(pcm16le_bytes, locale="zh-TW", sample_rate=16000)
event = {"type": "stt.result", "turn_num": 1, **result.to_dict()}

# Create one state per session. Replace it when the user changes the UI locale.
state = LanguageState("zh-TW")
result = await stt.transcribe(pcm16le_bytes, auto=True, state=state)
```

Inputs: audio-file path (ffmpeg resamples), normalized mono float array at 16 kHz,
or headerless signed PCM16 little-endian bytes at 16 kHz. Remove the WebSocket's
8-byte framing header first. Empty, malformed, nonfinite, non-mono, and >120-second
utterances are rejected. The caller supplies an already VAD-segmented utterance.

`text_raw` preserves Whisper output; `text` applies `s2twp` for Taiwan Mandarin and
`t2s` for mainland Mandarin. The app's Chinese region preference survives language
switches, and never derives from voice. `reservation_code` uses T2's parser without
rewriting the transcript. The prompt reuses T2's `data/lexicon.yaml` display names.

Specified mode passes en/zh/ja/yue explicitly; `lang_detected` and `lang_prob` are
null because no detection is measured. Auto mode measures the strongest language
and its real probability, uses that language for transcription, and changes
`locale` (the response language) only after two consecutive supported detections
with probability >=0.8. Missing Chinese region preference defaults to zh-CN.
Unrelated or low-confidence turns reset the candidate. Unsupported detections do
not change the response language. A single session must await turns sequentially.

The async API runs GPU work on one thread to protect mlx-whisper's global model
cache. Cancellation returns promptly and cannot mutate language state afterward,
but cannot preempt GPU decoding already underway. The next queued call waits for
that decode to finish. `elapsed_ms` includes model loading on the first request,
audio loading and inference; measure total await latency separately to include
executor queue waiting. Use `transcribe_sync` for offline benchmarks only.

```sh
uv run python -m pytest tests/test_stt.py -q
uv run python scripts/bench_stt.py --refs --output reports/stt/fleurs
uv run python scripts/bench_stt.py --manifest reports/stt/say/manifest.json --output reports/stt/say-specified
uv run python scripts/bench_stt.py --manifest reports/stt/say/manifest.json --auto --output reports/stt/say-auto
uv run python scripts/bench_stt.py --wav en=path/to/en.wav --wav zh-CN=path/to/zh-CN.wav --wav zh-TW=path/to/zh-TW.wav --wav ja=path/to/ja.wav --output reports/stt/t3
```

Each benchmark writes Markdown and JSON incrementally, preserving failures, input
paths, prompts, original reference text (when provided), raw transcripts and
normalized transcripts. The first item is cold; later items reuse the loaded
model. Concurrent workers were active during T5 checks, so reported timings are
smoke measurements, not isolated performance benchmarks. FLEURS source/license
attribution is in `data/refs/MANIFEST.json`; its Taiwan test shares mainland audio
and provides no evidence of Taiwanese accent recognition.
