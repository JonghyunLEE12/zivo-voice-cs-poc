# T10 브라우저 상담 데모

상태: **구현과 T10 완료 기준 검증 완료** (2026-10-30). Chrome headless에서 실제 Ollama + Whisper + Kokoro 4개 언어 대화 1회씩 완료했다. stub에서는 4개 언어마다 정상 재생 → barge-in → 다음 턴 복구, 별도로 실제 Silero VAD fake-microphone 입력을 검증했다. 실제 첫 재생 2.97–5.36초로 설계 4.5의 **1.5초 목표는 미달**이다.

## 구현

- Vite + TypeScript, 프레임워크 없이 기존 blind 화면 색상/타이포그래피를 재사용. `/` 상담, `/blind`, `/blind/results`, `/report`, `/ws/call`을 함께 유지한다. 공유 `server/app.py`는 정적 라우터 import/등록 **1줄**만 추가했다.
- en / zh-CN / zh-TW / ja, Kokoro 기본·Qwen3-TTS 선택, VoxCPM2/CosyVoice3는 기본 미선택 `느림(체험용)` 표기 후 선택 가능. yue는 이번 실시간 데모 지시에 따라 `지원 엔진 없음`으로 비활성 표시. raw/lex/full 선택과 통화 중 설정 적용.
- `@ricky0123/vad-web` 0.0.30, Silero v5, `redemptionMs` 500ms 기본(200–1500ms), `minSpeechMs` 250ms. 최소 길이 필터를 통과한 `onSpeechRealStart`에서 barge-in. 재생 중 임계값 0.65/0.5, 일반 0.3/0.25; 옵션을 끄면 일반 임계값 유지. 마이크 echoCancellation/noiseSuppression/autoGainControl 모두 true.
- VAD worklet·ONNX·ORT WASM/MJS는 설치된 npm 패키지에서 빌드에 포함하므로 외부 CDN 요청 없음. 실제 Chrome VAD 실행 때 필요한 자산 전부 HTTP 200.
- 16kHz PCM16LE 발화 및 8바이트 헤더 전송, 24kHz `AudioBufferSourceNode` 연결 재생. AudioContext 재생 시각에 따라 청크별 `playback.started` 전송. 서버 생성 완료와 마지막 재생 종료를 모두 확인한 뒤에만 `playback.finished` 전송.
- barge-in은 모든 예약/재생 노드 stop+disconnect, 타이머·큐 비우기를 먼저 수행한 뒤 서버에 played_ms 전송. 취소된 턴의 후속 PCM 무시, 오류/연결 종료/설정 변경에서도 큐 정리. 실제 tool의 handoff 플래그가 있는 응답 재생 완료 후 마이크 중지.
- STT/LLM 자막, 클라이언트 첫 음성·답변 음성 지연, 서버 STT/LLM/문장/TTS 지연 HUD, 끊기 횟수, JSON 로그 다운로드. filler 포함 첫 음성과 reply 첫 음성을 따로 표시한다.
- `/?dev=1`에만 WAV 입력 및 말 끊기 테스트 버튼 노출. WAV는 브라우저에서 mono/16kHz로 리샘플하고 VAD를 우회한다. 로그에 `source=wav`를 기록하며 `c_speech_end_est=c_vad_end`로 두므로 이 경로를 실제 마이크 VAD 지연으로 해석하지 않는다.

VAD API 근거: [공식 API 문서](https://docs.vad.ricky0123.com/user-guide/api/) 및 설치본 `dist/real-time-vad.d.ts`, `frame-processor.d.ts`. 최신 온라인 문서의 v6 대신 설치본이 실제 지원하는 v5를 사용했다.

## 실행과 재현

프로젝트 루트에서:

```sh
pnpm --dir web build
# 선택한 엔진을 별도 실행한 뒤 실제 상담
.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 8000
# http://localhost:8000/ 또는 개발 WAV 입력 http://localhost:8000/?dev=1

# 모델 없이 수동 UI 확인
.venv/bin/uvicorn scripts.call_browser_fixture:app --host 127.0.0.1 --port 8019
# http://localhost:8019/?dev=1 (T9 stub STT/LLM/TTS)

# Chrome 자동 검증 의존성: 프로젝트 내부에만 설치, 브라우저 다운로드 없음
uv --cache-dir reports/t10/uv-cache pip install --target reports/t10/qa-deps playwright
.venv/bin/python scripts/check_call_browser.py

# 코디네이터 모델 창 해제 후에만 실행: 소유 서버/엔진 시작 및 종료, Ollama 언로드 포함
.venv/bin/python scripts/check_call_browser_live.py --model-window-released

.venv/bin/python -m pytest tests/test_voice_pipeline.py tests/test_blind.py -q --basetemp reports/t10/pytest-temp
```

Live 검증은 기존 `reports/stt/say/{locale}.wav`를 Chrome 파일 입력으로 보내고 실제 STT/LLM/TTS를 거쳐 Web Audio를 재생한다. Ollama 명시 warmup와 버리는 영어 WS 턴으로 같은 서버 Whisper 캐시를 먼저 준비했다. 워밍업 CLI ACK는 실제 브라우저 증거에서 제외하며 `live/warmup.json`에 분리했다. 실제 마이크 권한/사용자 헤드폰 환경의 청취 품질 평가는 별도이다.

## 완료 기준별 결과

| 기준 | 실제 결과 | 증거 |
|---|---|---|
| Vite/TS 빌드 | exit 0, 상담 43 모듈·blind 6 모듈 빌드 성공 | `t10/build.txt` |
| Chrome 4개 언어 대화 1회씩 | 실모델 4/4, 각 STT/LLM 자막·오디오 노드 재생 완료 | `t10/live/browser-events.json`, `t10/live/{locale}.png` |
| stub 브라우저 대화 | 최종 실행 5세션, 정상 9턴(4언어 × 2 + 실제 VAD 1), 취소 4턴 | `t10/browser-events.json`, `t10/summary.json` |
| barge-in 재생 정지 ≤100ms | 네 언어 모두 마지막 실행 0.0ms, 첫 실행 0.0–0.1ms; 실제 활성 AudioBufferSourceNode stop+disconnect, 이후 큐 active=false | `t10/browser-events.json`의 `barge_in` |
| 취소 후 정상 복구 | 네 언어 모두 턴 2 취소 후 턴 3 재생 완료, 취소 턴 playback.finished 없음 | 동일 클라이언트 로그·`t10/stub/turns-final.json` |
| c_vad_end/c_play_start 턴 로그 | 실모델 최종 4/4 및 stub 정상 9/9 기록 | `t10/live/turns-final.json`, `t10/stub/turns-final.json` |
| 실제 Silero VAD | Chrome fake getUserMedia에 영어 FLEURS+무음 입력, source=vad 발화 및 재생 완료 1회 | `t10/browser-events.json`의 en-microphone-vad |
| 화면 검수 | 1280px 데스크톱 4언어 및 390px 모바일 스크린샷 검수, 모바일 가로 넘침 수정 후 통과 | `t10/call-*.png` |
| 기존 라우트 보존 | /, /blind, /blind/results, /report 및 VAD 자산 200, /ws/call 연결 수락, 미존재/경로 이탈 자산 404 | `t10/routes.json` |
| 서버 회귀 | **59 passed**, 2.69s, 기존 Starlette/httpx deprecation warning 1건 | `t10/pytest.txt` |

0.0ms는 브라우저 performance.now 해상도 내의 **클라이언트 노드 중단 호출 시간**이다. 스피커 하드웨어의 물리적 잔향까지 0ms라는 주장은 아니다. 자동 barge-in 검증은 개발용 버튼에서 VAD와 동일한 중단 함수를 호출했으며, 실제 사람의 말 끊기·에코 오탐률은 측정하지 않았다. 실제 VAD의 정상 발화 경로는 별도로 통과했다.

## 실모델 브라우저 수치

Kokoro/full, 명시 언어, warmed 모델, 언어별 1회. 모든 시간 ms, 클라이언트와 서버 시계는 서로 빼지 않았다. 이번 요청은 추가 예약 정보를 묻는 답변이며 filler가 없어 첫 음성과 첫 reply 시각이 같다.

| 언어 | STT | LLM TTFT | 문장 확정 | TTS TTFA | c_play_start − c_vad_end | PCM bytes / 길이 |
|---|---:|---:|---:|---:|---:|---:|
| en | 708.1 | 1114.3 | 514.2 | 601.7 | **2971.9** | 326400 / 6.80s |
| zh-CN | 823.7 | 2613.9 | 375.8 | 841.3 | **4681.9** | 288000 / 6.00s |
| zh-TW | 780.3 | 3047.4 | 816.9 | 672.3 | **5361.6** | 240000 / 5.00s |
| ja | 753.6 | 2545.0 | 185.8 | 482.3 | **4001.3** | 157200 / 3.275s |

대만 응답은 번체, 일본어 응답은 일본어로 표시됐다. zh-TW 응답의 `訂房程式碼` 같은 어색한 어휘는 모델 출력 그대로 보존했으며 T10에서 상담/번역 품질을 교정하지 않았다. 대만 억양 참조 없음, Kokoro 중국어 음성의 대만 억양 한계는 유지된다. Qwen3-TTS/VoxCPM2/CosyVoice3는 UI 선택만 구현했으며 이번 live 브라우저 검증은 Kokoro만 사용했다.

## 자원·모델 창

- 시작 swap 21494.88 MiB → 27485.12 MiB로 +5990.24 MiB 증가 시 가드에 따라 중단·escalation했다. 당시 T10 모델 요청과 Chrome 실행은 0회였고 코디네이터가 T9 모델 검증 원인임을 확인했다.
- `msg_4ce07150b917`에서 T9 언로드 후 stub 재개 승인, stub 새 baseline 27421.12 MiB 및 swap은 관찰만으로 변경. `msg_1d0f90793e52`의 **모델 창 해제** 후에만 live 실행했다.
- live baseline **26531.75 MiB**, 최대 증가 **2518.31 MiB**, 최고 pressure **2**, 디스크 최소 **127.58 GiB**. +4GiB/pressure4/디스크10GiB 중단 조건을 넘지 않았다. `t10/live/resources.jsonl`에 5초 간격 기록.
- 완료 시 소유 uvicorn/엔진 프로세스 그룹 종료, Ollama unload. `/api/ps`의 `models=[]`를 `t10/live/ollama-after.json`에 보존했다. 모델 추가 다운로드 0건.

## 변경/생성 파일

- `web/src/main.ts`, `web/src/call/app.ts`, `playback.ts`, `style.css`, `web/src/shared/ws.ts`
- `web/vite.config.ts`, 빌드 산출물 `web/dist/` (기존 build 명령이 blind도 재빌드; blind 소스 변경 없음)
- `server/call_web.py`, `server/app.py` 정적 라우터 한 줄
- `scripts/call_browser_fixture.py`, `scripts/check_call_browser.py`, `scripts/check_call_browser_live.py`
- `docs/DEVIATIONS.md` 추가, `reports/T10-REPORT.md`, `reports/t10/`의 로그·스크린샷·검증 산출물

남은 항목은 1.5초 지연 목표 최적화, 실제 사용자 마이크/스피커 청취와 에코 평가, Kokoro 외 엔진의 live 브라우저 검증이다. T10의 기능 완료 기준과 이번 지시의 stub 검증은 충족했으며 Smart Turn/세션 집계는 T11 범위이다.
