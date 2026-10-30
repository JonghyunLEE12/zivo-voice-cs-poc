# T7/T8 offline evaluation

All commands run at the project root. No model downloads are performed.
The benchmark and generator start one owned TTS server, warm it, run its batch,
and terminate/reap it before starting the next. Stop other Ollama/TTS/STT work
first; never overlap these commands with another worker's model window.

```sh
.venv/bin/python scripts/bench_tts.py --engines kokoro,qwen3tts,voxcpm2 --locales all --repeats 3
.venv/bin/python scripts/bench_tts.py --engines cosyvoice3 --locales all --levels full --repeats 1 --resume
.venv/bin/python scripts/bench_tts.py --score --resume
engines/mlx_engines/.venv/bin/python scripts/score_kana.py --input reports/bench-20261030.json
.venv/bin/python scripts/report.py
.venv/bin/python scripts/gen_blind_set.py --locales en,zh-CN,zh-TW,ja,yue
.venv/bin/python scripts/finalize_blind_audio.py
pnpm --dir web build
.venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

`bench_tts --resume` preserves completed measurements (including recorded failures).
`gen_blind_set --resume` preserves attempted items, including failures: it does not
retry a bad clip. Without resume, generation creates a new full set; archive the
old manifest/audio/results together first if human votes already exist.
A changed normalization-source hash forbids resuming an old blind generation.
`score_kana` uses pyopenjtalk already installed in the MLX engine environment;
it loads no TTS model. Adjust the dated report input for the current run.

- `/blind`: rater alias, native language, device, locale, two complete listens, vote.
- `/blind/results`: anonymous voice labels, language/native/device filters, JSON/CSV.
- `/report`: latest benchmark Markdown rendered as escaped text.
- `scripts/aggregate_blind.py`: private operator aggregation, `--reveal` explicitly
  uses engine names in terminal output; the public routes never reveal them.

The coordinator's final resource rule is critical OS pressure (level 4), >4 GiB
swap growth from process start, or <10 GB disk free. The TTS owner monitors every
two seconds and checks each request; `memory_pressure` free percentage and warning
level 2 are recorded but do not abort. An Ollama process appearing mid-batch also
stops the owned TTS process because isolated benchmark conditions no longer hold.

The browser receives only sentence text, opaque assignment/audio tokens, durations,
and anonymous aggregate labels. All four voices use the same WAV format and fixed
150 ms edge silence after two-pass -20 LUFS normalization. The server retains engine
names, file paths, labels, and token mappings. Do not mount `data`, `reports`, or
this repository as public static assets. A later real-time UI must keep its engine
selector assets separate from the blind page's explicit asset allowlist.

Votes are append-only `data/blind/results/<hashed-alias>.jsonl`; raw aliases never
become paths. Audio tokens are tied to a server session and revoked after voting.
Refreshing preserves a pending pair. A new session with the same alias does not
repeat rated pairs. This is a local PoC, not identity authentication: users can
choose a different alias. Browser `ended` gating and server elapsed-time checks
prevent accidental early votes; they cannot prove that a human listened.

Production votes are never populated by test automation. Browser acceptance uses
`BLIND_DATA_DIR=$PWD/reports/blind-browser-fixture` and port 8018, with synthetic
0.35-second tones. These ten fixture votes validate persistence/UI/aggregation and
are **not human preference evidence**. Tests use an isolated project-local temp dir:

```sh
.venv/bin/python -m pytest tests/test_blind.py tests/test_tts_offline.py --basetemp reports/test-temp -q
```

Taiwan accent reference is unavailable; mainland Mandarin is shared by all cloning
engines for zh-TW. Kokoro presets differ in speaker identity, and its zh-TW path uses
simplified Chinese G2P. Judge Taiwan accent only from native zh-TW raters; no such
human judgment is inferred from automatic ASR or the synthetic acceptance votes.

Current recorded run: CosyVoice3 is full-only, one sample per sentence by user decision (RTF about 2.6); its archived raw/repeat>0 rows do not enter the primary comparison. The blind set uses separate nonstreaming synthesis; T7 uses incremental mode when the engine supports it. Final integrated loudness is aligned after silence trimming with common gain and -1 dBTP headroom; this does not resynthesize any clip.
