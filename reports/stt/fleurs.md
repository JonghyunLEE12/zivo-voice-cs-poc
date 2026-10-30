# STT benchmark

UTC: 2026-10-30T01:42:56.312647+00:00

Times include file decoding and STT; first inference includes lazy model loading.
Concurrent TTS/LLM workers may affect timing; these are functional smoke measurements, not isolated performance claims.
zh-TW FLEURS uses the mainland Mandarin recording: 대만 억양 참조 없음.
Specified-language mode does not measure LID probability (null); auto mode does.
Qwen3-ASR optional cross-check not installed/run; no additional download.

| Source / WAV | Locale | Mode | Seconds | STT ms | RTF | Detected / probability | Raw | Display |
|---|---|---|---:|---:|---:|---|---|---|
| data/refs/en/ref.wav | en | specified | 11.460 | 4500.7 | 0.393 | None / None | To the north and within easy reach is the romantic and fascinating town of Sintra and which was made famous to foreigners after a glowing account of its splendorous recorded by Lord Byron. | To the north and within easy reach is the romantic and fascinating town of Sintra and which was made famous to foreigners after a glowing account of its splendorous recorded by Lord Byron. |
| data/refs/zh-CN/ref.wav | zh-CN | specified | 8.460 | 709.7 | 0.084 | None / None | pH值被测化学品中的氢, pH中的H离子数量表示 | pH值被测化学品中的氢, pH中的H离子数量表示 |
| data/refs/zh-CN/ref.wav | zh-TW | specified | 8.460 | 593.2 | 0.070 | None / None | pH值被測化學品中的氢, pH中的H, 粒子數量表示 | pH值被測化學品中的氫, pH中的H, 粒子數量表示 |
| data/refs/ja/ref.wav | ja | specified | 11.760 | 637.6 | 0.054 | None / None | 残念ながら運転手の行動100%の確率で予測することはできないので交通の流れを研究することは困難です | 残念ながら運転手の行動100%の確率で予測することはできないので交通の流れを研究することは困難です |
| data/refs/yue/ref.wav | yue | specified | 10.260 | 597.1 | 0.058 | None / None | 兩名攝影師後來與一名需要如刺的老婦人交換, 最後, 萬多薩被擊斃。 | 兩名攝影師後來與一名需要如刺的老婦人交換, 最後, 萬多薩被擊斃。 |
