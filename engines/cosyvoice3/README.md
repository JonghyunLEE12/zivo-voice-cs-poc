# CosyVoice3 engine (T4)

Run from the project root:

```sh
cd engines/cosyvoice3
uv sync --locked
uv run python serve.py --port 8102 --device mps --lazy --idle-unload 600
```

CPU fallback: replace `--device mps` with `--device cpu`. Automatic device selection
prefers MPS. An MPS runtime failure returns HTTP 503; selecting CPU explicitly keeps
measurements attributable to one backend. TensorRT, vLLM, JIT and fp16 are disabled.
The tokenizer ONNX runs on CPU even when neural synthesis uses MPS.

The engine reads `models/paths.json` and `data/refs/MANIFEST.json` relative to the
project root; no model download is performed at startup. English, mainland Mandarin,
Taiwan Mandarin text, Japanese and Cantonese use the common FLEURS references.
**대만 억양 참조 없음:** zh-TW shares zh-CN reference audio. Non-silent output is only
an execution smoke check, not a pronunciation or accent-quality judgment.

Endpoints follow `engines/PROTOCOL.md`: `/v1/info`, `/v1/warmup`, `/v1/synthesize`.
Audio is signed PCM16 little endian, 24 kHz mono. Nonstreaming has `X-Synth-Ms`.
`stream:true` performs real incremental LLM-token/flow generation. HTTP disconnect
sets a cancellation event checked between tokens and chunks; an in-flight native
kernel must finish before the worker releases its lock. One inference runs at a time.
`load_ms` includes imports and model construction; `peak_mem_bytes` is max observed
RSS or MPS driver allocation, not their sum, because unified-memory values overlap.

Tests and live checks (from this directory):

```sh
uv run python -m pytest -q test_server.py
uv run python ../../scripts/smoke_tts.py --engine cosyvoice3 --locale en --warmup --output "$PWD/artifacts/cosyvoice3-en.wav"
uv run python ../../scripts/smoke_tts.py --engine cosyvoice3 --locale zh-CN --output "$PWD/artifacts/cosyvoice3-zh-CN.wav"
uv run python ../../scripts/smoke_tts.py --engine cosyvoice3 --locale zh-TW --output "$PWD/artifacts/cosyvoice3-zh-TW.wav"
uv run python ../../scripts/smoke_tts.py --engine cosyvoice3 --locale ja --output "$PWD/artifacts/cosyvoice3-ja.wav"
uv run python ../../scripts/smoke_tts.py --engine cosyvoice3 --locale yue --output "$PWD/artifacts/cosyvoice3-yue.wav"
```

Use absolute output paths or paths under the project when invoking the shared smoke
script. See `REPORT.md` for measured results and unresolved acceptance items.
Pinned vendor provenance and local modifications are in `vendor/README.md`.
