# STT benchmark

UTC: 2026-10-30T01:43:41.895904+00:00

Times include file decoding and STT; first inference includes lazy model loading.
Concurrent TTS/LLM workers may affect timing; these are functional smoke measurements, not isolated performance claims.
zh-TW FLEURS uses the mainland Mandarin recording: 대만 억양 참조 없음.
Specified-language mode does not measure LID probability (null); auto mode does.
Qwen3-ASR optional cross-check not installed/run; no additional download.

| Source / WAV | Locale | Mode | Seconds | STT ms | RTF | Detected / probability | Raw | Display |
|---|---|---|---:|---:|---:|---|---|---|
| reports/stt/say/en.wav | en | auto | 5.757 | 2333.3 | 0.405 | en / 0.9986103773117065 | Please check my reservation at Myeongdong Station. My booking number is ZV4821. | Please check my reservation at Myeongdong Station. My booking number is ZV4821. |
| reports/stt/say/zh-CN.wav | zh-CN | auto | 7.250 | 1105.0 | 0.152 | zh / 0.9981184005737305 | 请帮我确认预定, 司机会在仁川国际机场等我, 然后送我到明洞酒店 | 请帮我确认预定, 司机会在仁川国际机场等我, 然后送我到明洞酒店 |
| reports/stt/say/zh-TW.wav | zh-TW | auto | 7.764 | 1079.6 | 0.139 | zh / 0.9984434247016907 | 請幫我確認訂房, 從仁川國際機場搭計程車到飯店, 我已經用信用卡付款了 | 請幫我確認訂房, 從仁川國際機場搭計程車到飯店, 我已經用信用卡付款了 |
| reports/stt/say/ja.wav | ja | auto | 6.183 | 1056.8 | 0.171 | ja / 0.9991838335990906 | 予約を確認してください。 明洞駅から、 地下鉄で、 ソウル駅まで行きます。 | 予約を確認してください。 明洞駅から、 地下鉄で、 ソウル駅まで行きます。 |
