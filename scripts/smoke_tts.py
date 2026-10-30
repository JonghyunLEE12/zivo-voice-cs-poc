#!/usr/bin/env python3
"""Check a running TTS engine and save a non-silent 24 kHz PCM16 WAV."""
import argparse
import json
from pathlib import Path
import time
import urllib.request
import wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PORTS = {"voxcpm2": 8101, "cosyvoice3": 8102, "qwen3tts": 8103, "kokoro": 8104}
TEXTS = {
    "en": "Hello, how can I help you today?",
    "zh-CN": "您好，请问有什么可以帮您？",
    "zh-TW": "您好，請問有什麼可以幫您的嗎？",
    "ja": "お問い合わせありがとうございます。",
    "yue": "你好，有咩可以幫到你？",
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=PORTS, required=True)
    parser.add_argument("--locale", choices=TEXTS, required=True)
    parser.add_argument("--url")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--text")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    base = args.url or f"http://127.0.0.1:{PORTS[args.engine]}"
    with urllib.request.urlopen(base + "/v1/info", timeout=10) as response:
        info = json.load(response)
    required = {"engine", "model_id", "locales", "supports_streaming", "native_sample_rate", "load_ms", "peak_mem_bytes"}
    assert required <= info.keys(), info
    assert info["engine"] == args.engine and args.locale in info["locales"], info
    if args.warmup:
        with urllib.request.urlopen(urllib.request.Request(base + "/v1/warmup", data=b"", method="POST"), timeout=args.timeout) as response:
            print(json.dumps({"warmup": json.load(response)}, ensure_ascii=False))
    # Explicit shared voice also checks compatibility with engines that require voice.
    if args.engine == "kokoro":
        voice = {"preset": {"en": "af_heart", "zh-CN": "zf_xiaobei", "zh-TW": "zf_xiaobei", "ja": "jf_alpha"}[args.locale]}
    else:
        manifest = json.loads((ROOT / "data/refs/MANIFEST.json").read_text())
        locale = manifest["locale_reference"][args.locale]
        ref = next(r for r in manifest["references"] if r["locale"] == locale)
        voice = {"ref_audio_path": str(ROOT / ref["audio_path"]), "ref_text": (ROOT / ref["text_path"]).read_text().strip()}
    payload = {"text": args.text or TEXTS[args.locale], "locale": args.locale, "voice": voice, "seed": 42, "stream": args.stream}
    request = urllib.request.Request(base + "/v1/synthesize", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=args.timeout) as response:
        assert response.headers["Content-Type"].lower() == "audio/l16; rate=24000; channels=1", response.headers
        first = response.read(2)
        ttfa_ms = (time.perf_counter() - start) * 1000
        pcm = first + response.read()
        synth_ms = float(response.headers["X-Synth-Ms"]) if "X-Synth-Ms" in response.headers else None
    wall_ms = (time.perf_counter() - start) * 1000
    assert len(pcm) > 0 and len(pcm) % 2 == 0, "Empty or malformed PCM"
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
    rms = float(np.sqrt(np.mean(audio ** 2)))
    assert rms > 1e-5 and float(np.max(np.abs(audio))) > 1e-4, f"Silent audio: RMS={rms}"
    output = args.output or ROOT / f"engines/mlx_engines/artifacts/{args.engine}-{args.locale}{'-stream' if args.stream else ''}.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)
    with urllib.request.urlopen(base + "/v1/info", timeout=10) as response:
        info = json.load(response)
    result = {"engine": args.engine, "locale": args.locale, "stream": args.stream, "file": str(output.relative_to(ROOT)),
              "sample_rate": 24000, "duration_s": len(audio) / 24000, "rms": rms,
              "peak": float(np.max(np.abs(audio))), "synth_ms": synth_ms, "ttfa_ms": ttfa_ms, "wall_ms": wall_ms, "info": info}
    output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
