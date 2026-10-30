# T9 실시간 서버 파이프라인 보고

상태: **구현, 지정 리뷰 수정, 실제 4언어 오디오 전송 및 200ms 이내 취소 검증 완료**. 실제 조회 성공은 2/4이며 STT/LLM 품질 한계는 그대로 기록했다. 헤드리스 답변 첫 오디오 수신은 2.345~5.517초로, 1.5초 목표 충족을 주장하지 않는다.

## 구현

- `/ws/call`: 8바이트 little-endian 오디오 헤더, 세션 설정/ready, 발화 유효성 검사, STT → T6.1 LLM/prefetch → 문장 정규화 → TTS HTTP 스트리밍.
- 선택 엔진·언어 안내 PCM 캐시, 깊이 2 문장 큐, 문장별 raw/lex/full 로그, 세션별 독립 에이전트.
- barge-in 응답은 모델 cleanup 이전에 전송하고 이후 오디오/done을 차단. 다음 턴 전에 스트림 cleanup을 await하고 실제 playback.started 문장 접두부로 `commit_spoken` 호출.
- 턴 JSONL은 revision 스냅샷으로 서버 계측과 늦게 도착한 클라이언트 재생 계측을 보존. 도구 없는 턴의 도구 포인트는 null이며, 캐시 안내음과 새 응답 합성 지연은 구분.
- `server/pipeline/PROTOCOL.md`에 T10 소비자가 필요한 재생 확인·오디오 취소·handoff·측정 계약을 명시.

## 실행 근거

| 완료 기준 | 명령 | 실제 결과 |
| --- | --- | --- |
| 최종 전체 회귀 | `.venv/bin/python -m pytest tests -q --basetemp reports/t9/full-pytest-temp --tb=short` | 201 passed, 1 dependency deprecation warning, 3.52s |
| 리뷰 수정 회귀 | `.venv/bin/python -m pytest tests/test_voice_pipeline.py tests/test_llm_review.py tests/test_llm.py -q --basetemp reports/t9/review-pytest-temp --tb=short` | 119 passed(T9 51개 포함), 1 warning, 2.79s |
| TCP stub 4언어 정상 + barge-in | `.venv/bin/python scripts/validate_voice_pipeline.py --mode stub --baseline-swap-mib 24414.25` | 8/8, exit 0; 언어별 정상 PCM 8640 bytes/0.18s, 전부 non-silent |
| stub barge-in <200ms | 위 명령의 4개 barge-in 케이스 | en 1.113ms, zh-CN 0.954ms, zh-TW 0.824ms, ja 0.790ms |
| stub 4.5 서버 포인트 | `reports/t9/stub/log-verification.json` (최신 JSONL revision 직접 검사) | 정상 4/4에서 8개 s_* 포인트 값 존재; client ACK도 로그에 보존 |
| 실제 모델 4언어 WAV/취소 | `.venv/bin/python -u scripts/validate_voice_pipeline.py --mode live --model-window-released --port 8000 --inputs reports/t9/inputs --baseline-swap-mib 24414.25` | 내부 `ws_wav_client.run`으로 4언어 응답 4/4 + barge-in 4/4, exit 0; 아래 실측 표 |
| 실제 4.5 서버 포인트 | `data/logs/turns-20261030.jsonl` → `reports/t9/live/log-verification.json` | 정상 4/4에서 서버 포인트 8개 key 보존 및 수행한 6개 기본 단계 값 존재; 도구 실행 en/zh-TW는 8개 값 존재, 미실행 zh-CN/ja의 도구 2개는 null |
| 정리 | Ollama `/api/ps`, psutil 이름/argv 기반 모델 프로세스 확인, 8000/8104 연결 확인 | `models: []`, 모델 프로세스 0개, 소유 서버 포트 모두 닫힘 (`reports/t9/live/cleanup.json`) |

`reports/t9/stub-validation.txt`, `reports/t9/pytest.txt`에 실행 원자료를 보존했다. 테스트 전용 STT는 WAV 내용을 인식하지 않고 언어별 고정 요청을 반환하고, LLM은 실제 VoiceAgent에 stub Ollama 스트림을 주입하며, TTS는 사인파를 반환한다. 따라서 stub 결과는 프로토콜·상태 테스트이며 음성 인식/상담 품질/성능 증거가 아니다.

`ws_wav_client.py`는 PCM 수신 시 재생 시작을 가정하여 ACK를 보낼 뿐 실제 스피커를 재생하지 않는다. CLI의 지연은 transport 수치이며, 1.5초 사용자 체감 목표나 T10 브라우저 재생 지연을 충족했다는 뜻이 아니다.


## 실제 모델 결과

입력은 기존 macOS 음성(Samantha/Tingting/Meijia/Kyoko)으로 합성한 예약 코드+성 요청 4개다. 정규화한 텍스트와 출처는 `reports/t9/inputs/manifest.json`, WAV는 같은 디렉터리에 있다. 자연 발화/실제 고객 녹음이 아니다. **검증하는 서버 경로에는 실제 Whisper/Ollama/Kokoro를 사용했으며 stub 주입은 없다.**

Ollama 명시 warmup + 같은 서버 프로세스로 보내는 버리는 영어 WS 턴에 26790.35ms가 걸렸다. 워밍업 출력은 별도 `warmup-en.json/.wav`, `warmup.json`에 남겼고 아래 측정 8건에서 제외했다. 각 언어 정상 턴 1회, 취소 턴 1회이며 반복 통계는 아니다. 영어 워밍업은 같은 입력이므로 영어의 매우 짧은 TTFT는 prompt cache의 이점을 포함할 수 있다. 언어 간 우열이나 T6 대비 개선을 주장하지 않는다.

단위 ms(오디오 길이는 초). 첫 오디오와 답변 오디오 모두 **헤드리스 수신/가상 playback ACK** 기준이다. 사용자 스피커 재생 지연은 T10에서 별도로 측정해야 한다.

| locale | 출력 오디오 초 | STT | LLM raw TTFT | 첫 문장−첫 토큰 | TTS TTFA | 첫 오디오 수신(안내 포함) | 답변 첫 오디오 수신 | barge-in ACK |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| en | 14.725 | 625.9 | 98.9 | 1049.0 | 566.1 | 635.2 | 2344.7 | 2.209 |
| zh-CN | 7.325 | 638.4 | 2443.9 | 571.0 | 668.8 | 4326.6 | 4326.6 | 1.310 |
| zh-TW | 19.100 | 692.6 | 2775.7 | 1302.7 | 735.5 | 707.7 | 5516.8 | 1.795 |
| ja | 5.900 | 705.5 | 2479.3 | 431.8 | 391.8 | 4017.5 | 4017.5 | 1.727 |

정상 4개 WAV는 총2258400 bytes/47.05초이며 모두 non-silent다. 답변 수신 기준도 4/4가1.5초를 넘었다. en/zh-TW 안내음은 각각635.2/707.7ms에 도착했지만 이를 답변 지연으로 쓰지 않는다. `c_speech_end_est`는 파일 기반 CLI에 없으므로 관련 구간은 null이며 추정값을 꾸며내지 않았다.

### 관찰된 실패/미완

- **조회 성공 2/4:** en은 `ZV4821`/Carter, zh-TW는 `ZV5196`/陳을 정확히 얻어 실제 server_prefetch로 조회했다. zh-TW 응답은 번체다.
- **zh-CN 조회 실패:** Whisper가 입력의 ZV를 `CV7305`로 인식했다. 모델은 올바른 ZV 형식을 다시 요청했고 도구를 호출하지 않았다. 도구 포인트 null은 미실행을 뜻한다.
- **ja 조회 실패:** Whisper가 `名字は佐藤`를 `領事は佐藤`로 인식했다. 모델은 `ZV2610`이 포함된 전사에 대해 하이픈 형식 재입력을 요구했고 도구를 호출하지 않았다. 원 전사와 모델 문장을 보존했으며 parser/LLM 품질을 변경하지 않았다.
- 정상 4턴에서 모델 길이 위반0, thinking0자였다. 단 이는 성공적인 상담 품질을 의미하지 않는다. T6.1의 상담원 전환/다회차 도구 누락 한계는 별도로 남아 있다.
- 실제 8개 턴 최신 revision 모두 서버 포인트 8개 key가 있으며, **정상 4턴 중 2턴만 도구 포인트까지 전부 non-null**이다. 나머지 2턴의 도구 미실행을 수행한 것처럼 시간값으로 채우지 않았다. 모든 서버 포인트를 모든 언어에서 non-null로 요구한다면 그 더 강한 조건은 아직 미충족이다; full tool 경로 4언어 계측은 stub에서만4/4다.
- 실제 취소는 en/zh-TW에서 filler 중, zh-CN/ja에서 reply 시작 시 수행됐다. 전자는 spoken_text가 빈 문자열, 후자는 재생 시작된 단일 문장으로 남았다. 문장 N 이후 취소/같은 실제 WS 연결의 2턴 검증은 추가하지 않았다(리뷰 낮음13, 미수정 지시).
- Kokoro zh-TW는 본토 프리셋/간체 G2P를 사용하므로 대만 억양 평가가 아니다. 실제 브라우저 VAD/재생 정지100ms는 T10 범위이며 여기서 검증하지 않았다.

원문 및 tool trace: `reports/t9/live/log-verification.json`, `summary.json`, `data/logs/turns-20261030.jsonl`. CLI 출력: `reports/t9/live-validation.txt`. 각 결과 JSON의 session_id로 JSONL 최신 revision을 직접 확인했다. 질의나 응답을 바꿔 성공으로 다시 시도하지 않았고, 최초 실측을 그대로 남겼다.

## 검증 범위와 한계

STT/LLM/TTS 진행 중과 생성 완료 후 취소, 느린 cleanup(300ms) 이전 200ms 내 ACK, 이후 stale audio/done 차단, 재생 접두부 기록 보존/미재생 롤백, 누락된 이전 문장 ACK 비추론, 새 턴 재진입, 단절 cleanup, 큐 깊이 2, 캐시 1회, TTS 오류·odd-byte HTTP 청크·응답 닫기, malformed 프레임, 미지원 설정 거절, 실제 tool 기반 handoff 상태를 검사했다.

T6.1의 상담원 전환/다회차 도구 누락은 수정하지 않았다. 모델 원문/도구/라운드 metrics/전환 여부를 그대로 기록한다. Whisper 진행 중 worker-thread decode와 엔진 프로세스 종료는 ACK보다 오래 걸릴 수 있으며, ACK가 GPU 계산 종료 증거는 아니다.

## 자원과 설계 차이

dispatch 시작 swap 24414.25 MiB, pressure 1을 중단 기준의 baseline으로 고정했다. 코디네이터의 **msg_d7ca1ddcbb09, 2026-10-30 04:44:35 UTC '모델 창 해제'** 이후에만 실제 모델을 사용했다. Ollama 기존 `qwen3.5:35b-a3b` + Whisper + Kokoro만 실행했으며 모델 다운로드는 0건이다.

자원 원자료 `reports/t9/resources.jsonl` 27개 샘플: pressure 1/2만 관찰, free% 최소15%, dispatch baseline 대비 swap 최대 +3686.94 MiB(+3.6005 GiB), 디스크 최소128.838 GiB. critical(4), +4096 MiB 초과, 디스크10 GiB 미만 중단 기준에 도달하지 않았다. warn/free%는 기록만 했다. 참고로 T8 종료 후 모델 창 직전 swap은21494.88 MiB로 task 시작보다 낮았고, 이 시점 대비 실제 peak28101.19 MiB는 +6606.31 MiB(+6.4515 GiB)이다; 이 수치를 task 시작 기준 증가량과 혼동하지 않는다. guard는 실행 내내 원래 dispatch baseline을 사용했다.

검증 스크립트 finally에서 소유 uvicorn/Kokoro를 종료하고 Ollama를 unload했다. 별도 확인에서도 models:[]/모델 프로세스0/8000·8104 닫힘이었다. 이후 추가 모델 요청은 하지 않았다.

`docs/DEVIATIONS.md`에 전체 엔진 startup preload 대신 선택 세션 준비 캐시, 공유 app 한 줄 등록, T6.1 계약 때문에 첫 쉼표 분할 비활성, 미구현 Smart Turn/Qwen3-ASR 명시 거절을 기록했다.

## 변경/생성 파일

- `server/pipeline/api.py`, `turn.py`, `latency.py`, `sentence_splitter.py`, `PROTOCOL.md`
- `server/llm/sentences.py` (리뷰 지시: 닫는 부호·목록 번호 경계)
- `server/tts/client.py`, `server/app.py` (import/라우터 등록 한 줄만)
- `scripts/ws_wav_client.py`, `scripts/validate_voice_pipeline.py`
- `tests/voice_stubs.py`, `tests/voice_fixture_app.py`, `tests/test_voice_pipeline.py`
- `docs/DEVIATIONS.md` (추가), `reports/T9-REPORT.md`, `reports/t9/*` (입력 4 WAV/manifest, stub·실제 8개씩 결과 JSON/WAV, warmup, 실행 로그, 자원/정리 증거)
- `data/logs/turns-20261030.jsonl`, `data/logs/llm-20261030.jsonl` (실제 검증 기록 추가)

`scripts/bench_tts.py`와 blind 소유 파일은 수정하지 않았다. git 명령/외부 게시/유료 API/모델 다운로드는 사용하지 않았다.


## 리뷰 후속 수정 (코디네이터 msg_d2ec1269d129)

| 리뷰 항목 | 수정 | 회귀 근거 |
| --- | --- | --- |
| 높음 1: 큐 역압에 따른 도구 롤백 | LLM collector는 네트워크 send/TTS put을 기다리지 않고 최종 llm.done까지 소진; 텍스트 event buffer → dispatcher → 깊이 2 TTS 큐로 분리 | 실제 VoiceAgent+prefetch 경로에서 2/4/5문장 응답의 문장0 시작 후 취소: verified ZV-4821 보존, user/assistant(tool)/tool/assistant 보존, LLM JSONL 및 첫 토큰 포인트 존재 |
| 높음 2: 정상 playback.finished | 전체 전송된 reply 인덱스를 시작 집합에 추가한 뒤 commit; barge-in 때만 개별 문장 ACK 접두부 적용 | 실제 FastAPI WS에서 filler 첫 청크만 ACK + finished: 전체 답변과 검증 코드 보존 |
| 중간 3: 단독 닫는 부호/목록 번호 | 공통 split_sentences가 닫는 부호를 흡수하고 줄 시작 번호를 문장 경계에서 제외; 발음 문자 없는 정규화 조각 TTS 생략 | 일본어/중국어 닫는 부호, 영어 인용문, 번호 목록 단위 테스트 + 실제 VoiceAgent→정규화→StubTTS 경로; 빈 문자는 합성 호출 안 함 |
| 중간 4: filler/reply 지표 혼합 | 기존 첫 오디오 의미를 명시하고 client_vad_to_reply_play_ms 및 client_speech_to_reply_play_ms 추가 | filler 1100/reply 2500/VAD 1000에서 첫 오디오100ms, 실제 reply1500ms 분리 |
| 중간 5: live cold start | Ollama 명시 warmup 후 real WS 워밍업 en 턴을 버려 같은 서버 프로세스의 Whisper 캐시를 준비; warmup.json과 측정 warm:true | 주입 테스트로 Ollama→실제 WS runner 호출 순서, warmup_discarded/warm 플래그 검증; 실제 워밍업 26790.35ms 후 측정 8건 warm:true; warmup.json 보존 |

리뷰 프로브의 모델 부분은 stub이지만 **트랜잭션·prefetch·문장 분할·정규화·라우터·TTS 호출/취소는 실제 서버 구현**을 탄다. 원본 리뷰 프로브 파일은 공유 경로에 제공되지 않았으므로 동일 조건을 tests/test_voice_pipeline.py의 test_review_* 회귀로 재현했다. T6 모델 도구 선택/품질 로직은 변경하지 않았다.

### 코디네이터 지시로 미수정

- 중간 6: Whisper await 취소 후 executor decode 지속·다음 턴 대기; stub STT의 asyncio 취소는 실제 GPU 종료를 검증하지 않는다. 내부 elapsed_ms는 STT 원본 결과에 있으나 별도 executor 대기 지표/현실적 thread stub은 추가하지 않았다.
- 중간 7: 첫 바이트/턴 전체 deadline 및 key별 filler lock. 기존 180초 HTTP read timeout과 전역 filler lock을 유지한다.
- 낮음 8: s_llm_reply_first_token 별도 mark는 추가하지 않았다. s_llm_first_token은 버려진 preamble을 포함한 raw 최초 토큰이며 원본 rounds/최종라운드 metrics는 로그에 있다.
- 낮음 9: VoiceAgent 내부 stream_chat의 별도 aclosing은 추가하지 않았다.
- 낮음 10: 서버 종료 등의 이중 취소에 대한 shield는 추가하지 않았다. 일반 cancel ACK 쓰기 실패 시 finally cleanup 보완은 리뷰 수신 전에 적용한 기존 변경이다.
- 낮음 11: llm_started와 agent playback turn id 방어는 추가하지 않았다. 현재 Call.start 사전검사를 유지한다.
- 낮음 12: 이미 finalized된 턴의 barge_in은 ACK 없이 무시하며 프로토콜에 명시했다.
- 낮음 13: CLI의 문장 N 이후 barge-in/연결당 2턴 기능은 추가하지 않았다. 현재 TCP CLI는 첫 오디오(도구 턴이면 filler)에서 취소하며, reply 접두부/후속 턴 상태는 별도 실제 VoiceAgent 단위 회귀로 검증한다.
- 낮음 14: complete sentence 계약 위반 시 warning 대신 실패하는 현재 처리를 유지한다.
- 낮음 15: 청크 ACK별 동기 JSONL, streaming splitter의 newline 차이, reconcile 전 handoff_queued 의미, clone reference의 문장별 manifest 읽기/HTTP clone 경로 미테스트를 유지한다. T10은 생성 시 handoff flag와 재생 취소 이후 상태가 달라질 수 있음을 고려해야 한다.
