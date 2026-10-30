# STT benchmark

UTC: 2026-10-30T01:43:37.015813+00:00

Times include file decoding and STT; first inference includes lazy model loading.
Concurrent TTS/LLM workers may affect timing; these are functional smoke measurements, not isolated performance claims.
zh-TW FLEURS uses the mainland Mandarin recording: 대만 억양 참조 없음.
Specified-language mode does not measure LID probability (null); auto mode does.
Qwen3-ASR optional cross-check not installed/run; no additional download.

| Source / WAV | Locale | Mode | Seconds | STT ms | RTF | Detected / probability | Raw | Display |
|---|---|---|---:|---:|---:|---|---|---|
| reports/stt/say/en.wav | en | specified | 5.757 | 2537.5 | 0.441 | None / None | Please check my reservation at Myeongdong Station. My booking number is ZV4821. | Please check my reservation at Myeongdong Station. My booking number is ZV4821. |
| reports/stt/say/zh-CN.wav | zh-CN | specified | 7.250 | 617.9 | 0.085 | None / None | 请帮我确认预定, 司机会在仁川国际机场等我, 然后送我到明洞酒店 | 请帮我确认预定, 司机会在仁川国际机场等我, 然后送我到明洞酒店 |
| reports/stt/say/zh-TW.wav | zh-TW | specified | 7.764 | 934.9 | 0.120 | None / None | 請幫我確認訂房, 從仁川國際機場搭計程車到飯店, 我已經用信用卡付款了 | 請幫我確認訂房, 從仁川國際機場搭計程車到飯店, 我已經用信用卡付款了 |
| reports/stt/say/ja.wav | ja | specified | 6.183 | 589.6 | 0.095 | None / None | 予約を確認してください。 明洞駅から、 地下鉄で、 ソウル駅まで行きます。 | 予約を確認してください。 明洞駅から、 地下鉄で、 ソウル駅まで行きます。 |
