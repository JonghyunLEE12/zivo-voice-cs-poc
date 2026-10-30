# Source provenance

CosyVoice is a source-only subset of https://github.com/jasagiri/CosyVoice at
`0cb2c1e3334c177b62685e8dbef687b971b85687` (PR #1869 head retrieved 2026-10-30).
Matcha-TTS is the submodule revision `dd9105b34bf2be2230f4aa1e4769fb586a3c824e`.
Both source copies retain their upstream license files. Archives were downloaded
by HTTPS, without git commands. No model weights are duplicated here.

Local changes to CosyVoice:

- `cosyvoice/utils/device.py`: `COSYVOICE_DEVICE=cpu|mps` overrides automatic device selection.
- `cosyvoice/cli/frontend.py`: speech-tokenizer ONNX uses CPUExecutionProvider explicitly.
  This keeps the CPU fallback genuine and avoids implicit CoreML compilation while the
  PyTorch LLM/flow/vocoder can still use MPS.

The PR's original CPU float64 f0-predictor workaround is preserved. `backend.py`
uses upstream LLM inference and token2wav directly with a bounded producer queue,
per-token cancellation and a finally block that removes the request HiFT cache.
It avoids the upstream detached LLM thread, whose errors can leave streaming waiting
forever. Chunk hop starts at 25 for every request and grows to 100, as upstream intends.

No text normalization is applied inside this engine; application raw/lex/full text
is passed through and shared reference transcription is supplied to zero-shot cloning.
The yue prompt additionally requests Guangdong speech, while retaining its shared
FLEURS audio and transcript.
