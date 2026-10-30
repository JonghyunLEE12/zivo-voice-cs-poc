# TTS engine HTTP contract (v1)

All services bind to `127.0.0.1`. Ports: VoxCPM2 `8101`, CosyVoice3 `8102`, Qwen3-TTS `8103`, Kokoro `8104`. One selected model per service. These endpoints are for local trusted clients; do not expose the services to the internet.

## `GET /v1/info`

Returns HTTP 200 and JSON with these required keys:

```json
{"engine":"kokoro","model_id":"mlx-community/Kokoro-82M-bf16","locales":["en","zh-CN","zh-TW","ja"],"supports_streaming":false,"native_sample_rate":24000,"load_ms":0.0,"peak_mem_bytes":0}
```

- `engine`: `voxcpm2 | cosyvoice3 | qwen3tts | kokoro`.
- `model_id`: upstream repository identifier, even when loaded from a pinned local snapshot.
- `locales`: supported app locales. All four engines accept `en`, `zh-CN`, `zh-TW`, `ja`; only VoxCPM2 and CosyVoice3 accept `yue`.
- `supports_streaming`: whether the backend emits incremental audio within a sentence. HTTP chunking alone does not qualify. The installed MLX implementation supports this for Qwen3-TTS; VoxCPM2 and Kokoro are reported false (Kokoro may produce multiple complete text segments).
- `native_sample_rate`: rate before the adapter resamples; VoxCPM2 48000, Qwen3-TTS/Kokoro 24000. Wire rate is always 24000.
- `load_ms`: latest inference-process initialization and model load time, including imports, excluding warmup synthesis; zero before a lazy load. HTTP supervisor startup is not included.
- `peak_mem_bytes`: maximum of MLX peak allocated memory and inference-process peak RSS, in bytes, retained across unload/reload. These overlapping measures are not summed. It is not a whole-machine or exact total unified-memory measure; HTTP supervisor RSS is excluded.

Info never triggers lazy loading and remains responsive during generation.

## `POST /v1/warmup`

No body required. Load the model and synthesize a short English utterance using the default reference/preset; discard the audio. MLX returns the info fields plus `warmed_up: true` and `warmup_ms` (synthesis/resampling elapsed time). This warms English; a new locale may still incur G2P initialization. Call once before benchmarks; report cold and warm timings separately.

## `POST /v1/synthesize`

Content-Type: `application/json`.

```json
{"text":"Hello, how can I help you?","locale":"en","voice":{"ref_audio_path":"data/refs/en/ref.wav","ref_text":"The exact reference transcript."},"seed":42,"stream":true}
```

Kokoro uses `"voice":{"preset":"af_heart"}` instead of a clone reference. Cloning engines require both nonempty `ref_audio_path` and `ref_text`, with no preset. Paths may be absolute or relative to the project root and must resolve to an existing file inside the project. No remote audio URLs. Voice can be omitted in the MLX adapter to select the manifest reference/preset automatically; portable clients should send voice explicitly.

- `text`: nonblank, up to 2000 characters; text normalization is the caller's responsibility.
- `locale`: exactly one of the locales in info (unsupported choices return 422).
- `seed`: integer in `[0, 4294967295]`, default 42. Seeds initialize Python, NumPy and MLX; bit-identical output across package/hardware versions is not promised.
- `stream`: boolean, default false. False buffers the full result. True sends available PCM immediately, even on a nonincremental backend (first audio then waits for full sentence generation).
- Unknown request/voice properties and malformed voice variants return 422.

### Success response

HTTP 200, `Content-Type: audio/L16; rate=24000; channels=1`.

**The payload is signed PCM16 little endian, mono, 24000 Hz, without a WAV header.** The historical `audio/L16` media label is preserved from DESIGN §8.3; standard RFC L16 normally means big endian, but this local protocol explicitly means little endian for browser/WebSocket compatibility. Clients must use `<i2` / little-endian `DataView` reads, not a standards-based L16 decoder.

- `stream=false`: `X-Synth-Ms` is synthesis through PCM conversion/resampling elapsed time, excluding model load, HTTP queue wait and client network time. `Content-Length` is provided.
- `stream=true`: chunked transfer under HTTP/1.1. No `X-Synth-Ms` because final elapsed time is unknown when headers are sent. Measure TTFA and request wall time at the client. Transport chunks need not align with model chunks or sentence boundaries; buffer a trailing odd byte when decoding arbitrary network chunks.
- MLX adapter uses float-to-PCM clipping and polyphase resampling to 24 kHz. It does not apply blind-set silence trimming/loudness normalization; those are offline dataset operations.

### Failure and cancellation

Before headers: HTTP 422 for request errors, 503 for missing/unloadable models or dead workers, 507 when disk free falls below 5 GB, 500 for synthesis errors, JSON `{"detail":...}`. A disconnected request may be logged as 499; it cannot receive an error body. A generation failure after streaming headers aborts the stream; clients must treat a truncated/chunked-read failure as failure and discard partial audio.

Each MLX service serializes inference requests to preserve model state and seed ownership. A disconnected queued request is dropped. During generation the HTTP task polls disconnects every 20 ms when waiting for output. The adapter terminates its inference subprocess on cancellation/failure, including before the first model chunk; the next request reloads. This is deliberate because VoxCPM2 exposes no cancellation hook inside its full-sentence generator. Streaming cleanup also runs on ASGI cancellation. Kernel termination/reload time is not a 20 ms latency guarantee.

## References, presets and lifecycle

- `models/paths.json` resolves all local snapshots. Servers set Hugging Face/Transformers offline mode; they never automatically download model weights.
- Clone defaults come from `data/refs/MANIFEST.json`, including exact transcripts and the shared Google FLEURS CC-BY-4.0 provenance. All cloning engines use the same locale reference.
- **대만 억양 참조 없음**: zh-TW shares the Mandarin zh-CN reference; it does not imply a Taiwan accent capability.
- VoxCPM2 supplies the reference as `prompt_audio` + `prompt_text` (continuation conditioning), because the installed mlx-audio `ref_text` argument is unused by its reference-only mode.
- Kokoro presets are fixed by locale: `af_heart` (en), `zf_xiaobei` (zh-CN/zh-TW), `jf_alpha` (ja). Preset speaker identity differs from the clone reference. Kokoro zh-TW text is converted to simplified characters by OpenCC `t2s` before G2P; Taiwan-specific polyphone readings are not modeled.
- `--lazy` defers model loading to warmup/first synthesis. `--idle-unload 600` unloads after 600 idle seconds; zero disables idle unloading. Idle unloading never interrupts an active request.
- Disk free is checked before loading and at one-second intervals while waiting for inference; below 5 GB aborts the request and reaps the worker. MLX allocator cache is capped at 256 MiB and cleared after each synthesis.
- MLX runs in a persistent inference subprocess managed by the HTTP service. Shutdown reaps it; cancellation unloads it. Do not use multiple uvicorn workers for one engine/port.

```bash
cd engines/mlx_engines
uv sync
uv run python serve.py --engine voxcpm2 --port 8101 --lazy --idle-unload 600
# Alternatives: --engine qwen3tts --port 8103, --engine kokoro --port 8104
```

From project root with a matching server running:

```bash
uv run python scripts/smoke_tts.py --engine voxcpm2 --locale en --warmup
uv run python scripts/smoke_tts.py --engine qwen3tts --locale ja --stream
```

Smoke output defaults to `engines/mlx_engines/artifacts/{engine}-{locale}[-stream].wav` plus a JSON record containing duration, RMS, peak, synthesis time, TTFA, wall time and info. No non-silent WAV means nonzero exit. CosyVoice3 uses the same smoke client and shared wire contract; its backend implementation is maintained separately.
