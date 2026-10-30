# T11 턴 결정·통화 지표 및 T12 완료 보고

작성일: 2026-10-30. 기능 구현, 실제 ON/OFF 비교, README와 최종 보고서 갱신을 완료했다. 자연 고객 발화·억양·인게이지먼트 개선은 검증하지 않았다.

## Smart Turn 적용

T1의 고정 pipecat-ai/smart-turn-v3 revision f766f81d3cfdf7737ac64aad813d91bbfd56bf93에서 기존 smart-turn-v3.2-cpu.onnx를 재사용했다. 파일 크기는 **8,679,182 bytes**, 200MB 이하이며 추가 모델 다운로드 0건이다. ONNX Runtime CPU와 transformers 4.57.6 WhisperFeatureExtractor를 사용했다. 적용 실패를 숨기는 VAD fallback은 없고 파일 미존재/크기 초과 시 미적용 오류를 반환한다.

전처리는 마지막 8초·앞쪽 zero padding·16kHz·do_normalize, 완료 확률 >0.5다. 공식 근거: [inference.py](https://github.com/pipecat-ai/smart-turn/blob/main/inference.py), [audio_utils.py](https://github.com/pipecat-ai/smart-turn/blob/main/audio_utils.py), [모델 카드](https://huggingface.co/pipecat-ai/smart-turn-v3). 로컬 파일·직접 추론 증거는 `t11/smart-probe.json`이다.

미완료 시 최초 수신부터 1.5초 대기창에서 다음 발화와 병합한다. 원래 deadline은 연장하지 않으며 기존 턴은 merged로 로그에 남기고 최신 번호로 응답한다. 설정 변경/명시 취소/연결 종료 시 대기를 취소한다. 추론/초기 로딩 자체가 1.5초 내 완료된다는 보장은 아니며 첫 ON cold 비용도 그대로 기록했다. CPU 추론 취소 후 새 추론이 겹치지 않도록 lock을 유지한다.

## 실제 ON/OFF 비교

명령: `source scripts/env.sh` 후 `.venv/bin/python -u scripts/validate_smart_turn.py --model-window-released`. Ollama + Whisper + Kokoro 실제 모델, `reports/t9/inputs/{locale}.wav` 재사용, 8011/8104 소유 서버를 사용했다. 4언어 × ON/OFF 8턴과 ON 영어 취소 1턴, 총 9건 non-silent, exit 0. 워밍업 en 1턴은 측정에서 제외했다.

**아래 첫 재생은 CLI 수신 시 가상 playback ACK이며 스피커 재생이 아니다.** OFF 다음 ON 고정 순서라 ON의 LLM cache 이점이 크다. Whisper/Ollama는 warmed지만 첫 ON은 Smart Turn lazy 초기화다. 단일 표본을 토글 정확도나 전체 속도의 개선으로 해석하지 않는다.

| 언어 | 모드 | 완료 확률 | 판정/해제 | 턴 결정 ms | STT ms | 첫 오디오 ACK ms | 답변 ACK ms |
|---|---|---:|---|---:|---:|---:|---:|
| en | OFF | 해당 없음 | VAD | 0.0 | 624.4 | 634.0 | 2443.6 |
| en | ON | 0.9660 | complete | 2454.8 | 809.9 | 3281.7 | 5800.2 |
| zh-CN | OFF | 해당 없음 | VAD | 0.0 | 652.7 | 4347.4 | 4347.4 |
| zh-CN | ON | 0.2076 | timeout | 1500.6 | 680.7 | 3348.7 | 3348.7 |
| zh-TW | OFF | 해당 없음 | VAD | 0.0 | 592.1 | 603.7 | 5374.5 |
| zh-TW | ON | 0.4219 | timeout | 1499.9 | 703.5 | 2214.2 | 4482.4 |
| ja | OFF | 해당 없음 | VAD | 0.0 | 691.1 | 3986.9 | 3986.9 |
| ja | ON | 0.9870 | complete | 47.5 | 613.0 | 1499.4 | 1499.4 |

중국어 두 입력은 요청 전체가 담긴 합성 WAV인데도 미완료로 판정됐다. 자연 발화 정답 라벨이 없는 이 결과로 정확도를 산출하지 않았다. 일본어 ON의 1499.4ms도 캐시를 포함한 가상 ACK 단일 관측이며 T10의 1.5초 사용자 체감 목표 달성으로 세지 않는다. 첫 ON en의 턴 결정 2454.8ms에는 Python/feature 초기화가 포함되며 smart_turn.inference_ms 374.4ms는 import 이후 구간이므로 서로 같지 않다.

ON 영어 추가 barge-in ACK는 **1.224ms**, 200ms 기준 통과다. 첫 안내음 1.9초분을 수신한 직후 중단했고 재생된 답변 없음으로 기록했다. JSONL 최신 revision과 비교 JSON은 `t11/live/turn-comparison.json`, wire 이벤트·출력 WAV는 `t11/live/`, CLI 출력은 `t11/live-validation.txt`에 있다.

## 통화 지표 정의와 실측

- 연결 1개를 1콜로 정의한다. 준비 완료부터 연결 종료까지 길이를 측정하고 대기 시간을 포함한다. 엔진을 바꾼 콜은 mixed로 묶는다.
- 3분 이상 비율은 ≥180초 콜/전체 콜, 끼어들기 비율은 AI 재생 중 barge-in이 1회 이상 있는 콜/전체 콜, 고객 발화량은 콜별 STT 문자 수 합계의 평균이다. 공백·문장부호를 포함하는 Python len을 사용한다. 새 브라우저는 재생 노드 활성 상태의 사용자 중단만 during_playback=true로 보내 설정/종료 취소를 제외한다. 기존 CLI 기록은 played_ms>0로 판별한다.
- 토스 발표의 콜 단위 정의를 따른다. 발표의 공백/문장부호 세부 처리 규칙은 확인되지 않았다. 근거는 설계 S1 로컬 발표 정리의 실제 통화 지표 절과 [원본 발표 16:55 이후](https://www.youtube.com/watch?v=j41oq_xbTgs&t=1015s)다.
- 최신 revision만 사용하고 병합 전 조각은 턴 수에서 제외한다. 워밍업·미종료 세션은 제외한다. 과거 T9/T10은 시작·종료 로그가 없으므로 길이 집계에서 제외했다. 전환 여부는 최종 reconciled ToolSession 상태다.

| 엔진 | 종료 콜 | 3분 이상 | 끼어들기 발생 | 고객 문자/콜 | 턴 수 | 전환 콜 |
|---|---:|---:|---:|---:|---:|---:|
| Kokoro | 9 | 0/9 (0.0%) | 1/9 (11.1%) | 33.2 | 9 | 0 |
| Qwen3-TTS/VoxCPM2/CosyVoice3 | 미측정 | 표본 없음 | 표본 없음 | 표본 없음 | 표본 없음 | 표본 없음 |

| 분포 | n | p50 | p90 |
|---|---:|---:|---:|
| 통화 길이, 초 | 9 | 4.0 | 5.7 |
| 첫 오디오 ACK, ms | 9 | 2214.2 | 4059.0 |
| 첫 답변 ACK, ms | 8 | 4167.2 | 5502.2 |

**PoC 소표본이며 전부 WAV 가상 ACK이다.** CLI는 실제 음성 길이만큼 기다리지 않고 서버 처리/수신 후 연결을 닫으므로 길이가 짧다. 이 11.1%는 자동 취소 1건이 만든 수치이며 토스의 고객 인게이지먼트나 엔진 선호와 비교하지 않는다. `/report`는 정의·소표본 표시·4개 엔진 행·세션별 원자료를 제공하고 `/api/report/sessions`와 `scripts/report_sessions.py`가 같은 집계를 반환한다. 출처 `session-metrics.json`, `t11/live/sessions.json`.

## 검증 명령과 실제 결과

| 완료 기준 | 명령 | 결과 |
|---|---|---|
| 전체 회귀 | `.venv/bin/python -m pytest tests -q --basetemp reports/t11/final-tests` | **208 passed**, 기존 의존성 warning 1건, `t11/pytest.txt` |
| Smart 대기/병합/취소·집계 | 위 테스트의 `tests/test_smart_sessions.py` | 미완료 timeout, 완료 즉시 진행, 병합/이전 턴 취소, native CPU 취소 직렬화, revision·워밍업·문자/콜 비율 검증 |
| 웹 빌드 | `pnpm --dir web build` | exit 0, TypeScript와 상담/blind 빌드 성공 |
| Chrome ON/OFF 선택·리포트 | `.venv/bin/python scripts/check_t11_pages.py` | vad → vad+smartturn 선택, JS 오류 0, 5개 라우트 HTTP 200 |
| README 설치 재현 | `source scripts/env.sh`, `uv sync --locked --offline`, `pnpm --dir web install --frozen-lockfile` | 모두 exit 0, main 설치 패키지 69개 확인 및 pnpm lock 일치 |
| README 환경 | `.venv/bin/python scripts/check_env.py` | Python/Apple Silicon/ffmpeg/Ollama/uv/pnpm/기존 모델/디스크 전부 PASS |
| 보고서 재생성 | `.venv/bin/python scripts/report.py`; `.venv/bin/python scripts/report_sessions.py` | exit 0, 벤치 Markdown 및 Kokoro 9콜 JSON 재생성 |
| 모델 정리 | `/api/ps`, psutil argv 확인 | models=[], serve.py 모델 프로세스 0 |

페이지 screenshot은 `t11/smart-toggle.png`, `t11/session-report.png`; 수평 표는 스크롤 가능하며 직접 이미지 검수했다. 환경·라우트·빌드·테스트 로그는 `t11/`에 보존했다. README의 다운로드 명령은 기존 다운로드 구현을 설명하며 이번 작업에서 대형 모델을 재다운로드하거나 벤치 전체를 재합성하지 않았다.

## 자원 가드

실모델 시작 swap **26749.62MiB**, 5초 간격 자원 기록 15개에서 최대 증가 **1694.63MiB**, 최소 디스크 **127.486GiB**, 최고 pressure **2**였다. pressure4, 시작 대비 +4096MiB 초과, 디스크10GiB 미만 중단 조건은 발생하지 않았다. `t11/resources.jsonl`이 원자료다. 소유 uvicorn/Kokoro 프로세스 종료 및 Ollama 언로드를 finally에서 실행했고 별도 확인도 models=[]였다 (`t11/cleanup.json`).

## T12 문서와 미완 항목

`README.md`는 새 터미널 환경/모델/포트/단일 엔진/메인/상담/블라인드/벤치/리포트 명령과 48GB 메모리 주의를 포함한다. `reports/FINAL-REPORT.md`는 한국어, em dash 없이 이전 보고서의 수치를 인용하고 구조 가설·발음·속도·메모리·상담 결과·한계·로컬/API 후속 실험을 정리했다. `docs/DESIGN.md` 1.4의 선정 4개 엔진과 7/11장을 갱신했으며 비선정 모델은 평가 제외다.

승인된 T11/T12 기능·문서 완료 기준에서 미완 항목은 없다. 남은 품질 검증은 자연 발화 Smart Turn 정답 평가, LLM 도구 누락, STT 번호/이름, 1.5초 목표, 대만 억양, 원어민 청취 평가이며 기존 실패를 수정했다고 주장하지 않는다. 기타 설계 차이는 `docs/DEVIATIONS.md`에 추가했다.

## 변경·생성 파일

- 서버: `server/turn_detect/smart_turn.py`, `server/pipeline/{turn,api,latency,sessions}.py`, `server/pipeline/PROTOCOL.md`, `server/app.py`.
- 웹: `web/src/call/app.ts`, `web/dist/` 빌드 산출물. 기존 blind 소스는 수정하지 않았다.
- CLI/검증: `scripts/{ws_wav_client,validate_smart_turn,report_sessions,check_t11_pages}.py`, `tests/test_smart_sessions.py`, `tests/test_voice_pipeline.py`의 지원 설정 기대값.
- 의존성: `pyproject.toml`, `uv.lock` (transformers 4.57.6 및 의존성), 루트 `package.json` (pnpm 10.34.5 고정).
- 문서: `README.md`, `docs/{DESIGN,DEVIATIONS}.md`, `reports/{FINAL-REPORT,T11-REPORT}.md`.
- 원자료: `reports/t11/`, `reports/session-metrics.json`, `data/logs/{turns,sessions,llm}-20261030.jsonl` 추가 기록; 기존 벤치 Markdown은 저장 JSON에서 재생성.

### 새 터미널 재현 수정

프로젝트 env.sh를 source한 새 환경에서 Corepack 0.33.0이 기본 pnpm 12.16.1의 pnpm.cjs 경로를 찾지 못했다. 기존 설치에 사용한 pnpm 10.34.5를 루트 package.json에 고정한 뒤 같은 `source scripts/env.sh; pnpm --dir web build` 경로가 성공했다. 실패 stderr는 `t11/build-corepack-failed.err`, 최종 성공 로그는 `t11/build.txt`에 보존했다. 버전 고정 근거는 [Corepack packageManager 문서](https://github.com/nodejs/corepack#when-authoring-packages)다.
