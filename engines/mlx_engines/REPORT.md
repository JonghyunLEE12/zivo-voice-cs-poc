# T3 MLX engine verification — 2026-10-30

Status: **PASS**. All 13 required engine/locale combinations produced non-silent 24 kHz WAVs; two additional Qwen streaming WAVs passed. All 12 contract/lifecycle tests passed.

## Delivered

- `serve.py`: VoxCPM2 / Qwen3-TTS / Kokoro, local snapshots, shared cloning reference, 24 kHz PCM16LE, seeded serial inference, warmup/info, lazy loading and idle unload, killable inference worker.
- `../PROTOCOL.md`: full HTTP contract, media byte-order exception, streaming capability, error and cancellation details.
- `../../scripts/smoke_tts.py`: generic four-engine smoke client; writes WAV and measured JSON.
- `test_contract.py`: 12 passing tests including real TCP disconnect before headers and during chunked streaming, queued cancellation, reference/path validation, idle unload and disk guard.
- `check_disconnect.py`: actual GPU-process cancellation probe for an already-loaded running engine.
- `pyproject.toml`, `uv.lock`: OpenCC 0.1.7 and spaCy en_core_web_sm 3.8.0 (12.2 MiB downloaded). No TTS weights were downloaded.
- `../../docs/DEVIATIONS.md`: VoxCPM2 reference API mapping, extra G2P dependency, subprocess metric definition and resource guard.

## Acceptance: supported-language smoke output

Start one engine at a time (from project root):

```bash
uv run --project engines/mlx_engines python engines/mlx_engines/serve.py --engine ENGINE --lazy --idle-unload 600
uv run python scripts/smoke_tts.py --engine ENGINE --locale LOCALE
```

For Kokoro en, `--warmup` was included. For the other locales the command above was used unchanged. Server defaults: voxcpm2 8101, qwen3tts 8103, kokoro 8104.

| Engine | Locale | Duration s | Synthesis ms | TTFA ms | RMS | Peak memory bytes | Result |
|---|---|---:|---:|---:|---:|---:|---|
| kokoro | en | 2.325 | 108.292 | 130.469 | 0.046131 | 1030126887 | PASS |
| kokoro | zh-CN | 3.925 | 571.856 | 593.542 | 0.059699 | 1406617063 | PASS |
| kokoro | zh-TW | 4.275 | 172.001 | 193.470 | 0.059823 | 1501214285 | PASS |
| kokoro | ja | 2.975 | 276.308 | 297.783 | 0.060772 | 1501214285 | PASS |
| voxcpm2 | en | 2.560 | 5775.315 | 9772.937 | 0.000883 | 7197618228 | PASS |
| voxcpm2 | zh-CN | 2.720 | 4783.166 | 4802.207 | 0.008351 | 7530623400 | PASS |
| voxcpm2 | zh-TW | 2.720 | 4685.045 | 4706.012 | 0.010944 | 7530705344 | PASS |
| voxcpm2 | ja | 1.760 | 3291.451 | 3311.703 | 0.001164 | 7530705344 | PASS |
| voxcpm2 | yue | 3.200 | 5613.014 | 5633.337 | 0.009260 | 7530705344 | PASS |
| qwen3tts | en | 2.480 | 5028.467 | 8751.696 | 0.001407 | 8890351053 | PASS |
| qwen3tts | zh-CN | 2.960 | 1869.226 | 1886.272 | 0.008050 | 9126553745 | PASS |
| qwen3tts | zh-TW | 3.440 | 2034.934 | 2053.048 | 0.008376 | 9126553745 | PASS |
| qwen3tts | ja | 2.720 | 1888.635 | 1906.060 | 0.003268 | 9126553745 | PASS |

All 15 saved WAVs (13 table rows plus two streams below) are mono, 24000 Hz, signed PCM16. Silence check: RMS > 0.00001 and peak > 0.0001. These are single-run smoke measurements, not the warmed three-repeat T7 benchmark. Kokoro en followed an explicit warmup; other locales include their first G2P/reference use. Initial model loading is included in first-request TTFA and excluded from X-Synth-Ms.

Final load times: Kokoro 868.395 ms, VoxCPM2 3821.835 ms, Qwen3-TTS 3530.747 ms. Kokoro English warmup took 4656.992 ms. Earlier cold runs under severe machine memory pressure were much slower; they are not used in this final table.

## Acceptance: info and streaming

Each smoke reads `/v1/info` before and after synthesis, asserts all seven required fields, confirms engine identity and advertised locale, and embeds the response in its JSON sidecar. The exact seven-field schema and lazy-load behavior also pass the contract test.

| Engine | Native rate | Wire rate | Incremental streaming |
|---|---:|---:|---|
| VoxCPM2 | 48000 | 24000 | false |
| Qwen3-TTS | 24000 | 24000 | true |
| Kokoro | 24000 | 24000 | false (may yield complete text segments) |

Qwen3-TTS additional commands:

```bash
uv run python scripts/smoke_tts.py --engine qwen3tts --locale en --stream
uv run python scripts/smoke_tts.py --engine qwen3tts --locale ja --stream
```

| Locale | First PCM ms | Full response ms | Audio s | RMS |
|---|---:|---:|---:|---:|
| en | 380.565 | 1547.991 | 2.480 | 0.001967 |
| ja | 318.805 | 1591.817 | 2.720 | 0.003414 |

## Acceptance: cancellation and lifecycle

```bash
.venv/bin/python -m unittest discover -s engines/mlx_engines -p 'test_*.py' -v
```

`Ran 12 tests in 2.614s — OK`; see `contract-tests.err`. Fake inference is used for deterministic lifecycle tests, including real TCP/ASGI connection teardown. Actual model cancellation was checked separately:

```bash
uv run python engines/mlx_engines/check_disconnect.py --engine voxcpm2 --port 8101 --server-pid PID --phase before_first_audio
uv run python engines/mlx_engines/check_disconnect.py --engine qwen3tts --port 8103 --server-pid PID --phase after_first_audio
```

Use the actual PID of the current serve.py process; the model must already be loaded. The VoxCPM2 measurement used the same socket/process probe inline before packaging it as check_disconnect.py; the Qwen measurement used the script directly. Both verify that the HTTP supervisor still responds after the inference child exits.

| Engine | Disconnect point | Inference child reaped ms | HTTP still responds |
|---|---|---:|---|
| voxcpm2 | before_first_audio | 82.754 | True |
| qwen3tts | after_first_audio | 61.869 | True |

## Constraints, failure history and limits

- No unfinished T3 acceptance item remains. Speech recognition accuracy, subjective quality, speaker similarity and repeated warmed benchmark metrics belong to T5/T7 and were not claimed here.
- VoxCPM2 uses prompt_audio + prompt_text because installed mlx-audio ignores ref_text in reference-only mode. No vendor library was edited.
- 대만 억양 참조 없음: zh-TW shares the mainland Mandarin reference. Kokoro uses its own preset speaker and t2s conversion; Taiwan-specific polyphone readings are not modeled.
- FLEURS English and Japanese references are quiet, and cloning preserves that tendency. VoxCPM2 English RMS is 0.000883; it is non-silent. Offline blind-set loudness normalization remains a separate task.
- Earlier resource pressure caused free disk to fall from about 12 GiB to 163 MiB; vm.swapusage used was 47413.56 MiB. Own GPU server, inference child and smoke loop were stopped immediately and escalation sent. The local Orca runtime later disconnected and was restored; the original question was resumed without duplication.
- Disk recovered to 21 GiB and no other active model runner was found, so already-authorized verification resumed sequentially with the new guard. All final smoke runs used the final 256 MiB allocator-cache limit and stayed above the 5 GB disk threshold. Final observed disk free: 122 GiB (external disk changes were not performed by T3).
- Runtime guard checks disk before load and once per second during generation; below 5 GB it aborts with 507 and kills inference. It cannot guarantee protection from a sudden allocation larger than available headroom between checks.
- Observed peak memory exceeds the design’s approximate 5–6 GB per-large-engine estimate: VoxCPM2 7.53 GB, Qwen3-TTS 9.13 GB. Values are max(MLX peak, inference-process peak RSS), not summed; HTTP-supervisor RSS is excluded. Run one TTS engine at a time.
- On disconnect the model is unloaded and next request reloads. Warmup/load timings must be accounted for after cancellation.
- No extra UniDic/espeak-ng installation was needed: pyopenjtalk dictionary was already present.
- Versions: mlx-audio 0.6.3, mlx 0.32.4, FastAPI 0.162.0, uvicorn 0.54.0, misaki 0.9.4, pyopenjtalk 0.4.1.
- All verification servers and inference subprocesses were stopped at handoff.

## Generated evidence files

- `artifacts/disconnect-qwen3tts.json`
- `artifacts/disconnect-voxcpm2.json`
- `artifacts/kokoro-en.json`
- `artifacts/kokoro-en.wav`
- `artifacts/kokoro-ja.json`
- `artifacts/kokoro-ja.wav`
- `artifacts/kokoro-zh-CN.json`
- `artifacts/kokoro-zh-CN.wav`
- `artifacts/kokoro-zh-TW.json`
- `artifacts/kokoro-zh-TW.wav`
- `artifacts/qwen3tts-en-stream.json`
- `artifacts/qwen3tts-en-stream.wav`
- `artifacts/qwen3tts-en.json`
- `artifacts/qwen3tts-en.wav`
- `artifacts/qwen3tts-ja-stream.json`
- `artifacts/qwen3tts-ja-stream.wav`
- `artifacts/qwen3tts-ja.json`
- `artifacts/qwen3tts-ja.wav`
- `artifacts/qwen3tts-zh-CN.json`
- `artifacts/qwen3tts-zh-CN.wav`
- `artifacts/qwen3tts-zh-TW.json`
- `artifacts/qwen3tts-zh-TW.wav`
- `artifacts/voxcpm2-en.json`
- `artifacts/voxcpm2-en.wav`
- `artifacts/voxcpm2-ja.json`
- `artifacts/voxcpm2-ja.wav`
- `artifacts/voxcpm2-yue.json`
- `artifacts/voxcpm2-yue.wav`
- `artifacts/voxcpm2-zh-CN.json`
- `artifacts/voxcpm2-zh-CN.wav`
- `artifacts/voxcpm2-zh-TW.json`
- `artifacts/voxcpm2-zh-TW.wav`

Console evidence: `contract-tests.err`, `kokoro-smoke.log`, `kokoro-multilingual.log`, `voxcpm2-smoke.log`, `qwen3tts-smoke.log`, `qwen3tts-stream.log`, and corresponding server logs. Empty `.err` files for the final smoke runs indicate no client exception.
