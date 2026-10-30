# 설계 변경 기록

- T1: 코디네이터 결정에 따라 FLEURS 원본 4개는 data/refs/에 두고 data/ref_voices/{locale}는 심볼릭 링크로 제공하며 zh-TW는 zh-CN과 같은 참조를 사용한다(대만 억양 참조 없음).
- T1: CosyVoice 공식 README의 python=3.10을 따라 별도 uv 프로젝트는 3.10, 메인/MLX는 3.12로 설정했다.
- T1: HF 실파일 합계는 16,943,466,035 bytes(16.943466 GB)로 설계 8.5의 근사치 합계 16.93 GB보다 0.013466 GB 크다; --plan은 근사치와 실측값을 함께 표시하고 실측값으로 20 GB 상한을 검사한다.
- T1: FLEURS 영어/일본어 원본은 낮은 녹음 레벨이므로 원본을 보존하고 20 ms 프레임 RMS 90/10분위 대비 20 dB 이상·클리핑 비율 0.01% 미만으로 객관적 품질을 선별했다; 별도 주관적 청취 평가는 하지 않았다.
- T6: 실제 qwen3.5:35b-a3b가 계산된 환불 금액을 답변에서 잘못 재표기하는 사례가 있어 get_cancellation_policy 결과에 언어별 spoken_summary를 추가하고 프롬프트에서 그대로 읽도록 했다; 정책 계산과 금액은 서버가 결정하며 LLM의 취소 실행 제안은 금지한다.
- T6: 중국어 OpenCC 대만 어휘 변환이 토큰 경계에서 깨지지 않도록 zh-CN/zh-TW llm.delta만 문장 종결까지 버퍼링한다; first_token_ms는 원시 토큰 도착 시점이며 T9는 실제 문장 출력 시점을 별도로 기록해야 한다.
- T5: 코디네이터 지시에 따라 T3 WAV가 없는 동안 FLEURS 5 locale과 macOS say 4 locale로 STT를 검증했다; 동시 작업 중인 Ollama/TTS 프로세스를 내리지 않아 지연은 격리 벤치가 아닌 기능 스모크 수치이며 선택 Qwen3-ASR은 디스크 절약을 위해 생략했다.
- T3: mlx-audio VoxCPM2는 전체 문장 합성 후 한 번만 yield하고 ref_text 인자를 쓰지 않으므로 transcript를 실제 조건으로 넣는 prompt_audio+prompt_text를 사용하며, 연결 종료 시 추론 하위 프로세스를 종료해 중단하고 다음 요청에서 재로드한다.
- T3: Kokoro 영어 G2P의 필수 en_core_web_sm 3.8.0(12.2 MiB)과 zh-TW용 OpenCC를 MLX 프로젝트에 추가했다; pyopenjtalk 사전은 기존 설치본을 사용하며 추가 UniDic/espeak-ng 설치는 하지 않았다.
- T3: load_ms는 HTTP 감독 프로세스가 아닌 MLX 추론 하위 프로세스 초기화부터 모델 로드까지 측정한다; peak_mem_bytes는 MLX peak와 하위 프로세스 최대 RSS 중 큰 값이며 합산하지 않는다.
- T3: 병렬 모델 실행 중 swap 증가와 함께 여유 공간이 163MiB로 감소해 검증을 중단·escalation했다; 재발 방지를 위해 MLX allocator 캐시를 256MiB로 제한하고 생성 중 1초마다 5GB 하한을 검사하여 미달 시 HTTP 507 및 추론 프로세스 종료를 적용한다.
- T4: CosyVoice PR #1869 head 0cb2c1e3334c177b62685e8dbef687b971b85687 + Matcha dd9105b34bf2be2230f4aa1e4769fb586a3c824e를 source vendor로 고정하고, 명시적 CPU/MPS 선택 및 ONNX CPU provider를 추가했다; upstream LLM 비동기 스레드 대신 토큰별 취소 가능한 추론 루프를 사용하며 scipy 1.12.0/setuptools<81은 macOS 로딩·구형 패키지 호환을 위해 고정했다.

- T7/T8 coordinator update (2026-10-30, msg_0b194e076792): historical swap 21.8GB is baseline; abort on +8GiB swap growth or memory_pressure free <15%, require >=25% before work and >=10GB disk, one model at a time. Blind corpus uses full only per dispatch; raw/full comparison remains in T7.
- T6.1: 늦게 도착하는 도구 호출의 사전 발화를 확실히 차단하기 위해 모든 locale의 tool-capable 라운드를 done까지 보류한 뒤 문장 단위로 llm.delta를 출력한다; 설계 4.3의 토큰 즉시 문장 출력보다 지연되며 first_token_ms와 first_sentence_ms를 분리 측정한다.
- T6.1: 명시적 코드·성을 가진 현재 발화는 동일 ToolSession으로 서버가 lookup 및 필요한 policy를 선실행해 모델 라운드를 줄이며, 최근 4턴의 완전한 tool 쌍과 서버 검증 상태 요약만 모델에 전달한다.
- T6.1: DEMO_NOW(시간대 필수)를 추가하고 미설정 시 데모 날짜에 실제 서울 시각을 결합한다; 픽업 시작 이후에는 service_started로 취소 불가를 반환한다.
- T6.1: 코디네이터 msg_a12320c62b63/msg_370e6f7288fc에 따라 잔여 swap 절대값 대신 배치 시작 대비 +8GiB, 실행 중 메모리 여유15% 미만을 중단 기준으로 사용하며 시작25%/디스크10GiB와 모델 직렬화를 지킨다.
- T6.1: 위 free% 기준은 단독 Ollama 로딩에서도 14%가 되어 두 번 중단되었으므로 코디네이터 msg_4620c5dd9f01 지시로 대체했다: kern.memorystatus_vm_pressure_level=4 즉시 중단/2가30초 지속 중단, 시작 대비 swap +4GiB 초과 중단, 디스크10GiB 미만 중단; free%는 관찰만 하며 모델 직렬화는 유지한다.
- T7/T8 coordinator superseding guard (msg_7df0295a9f11): use kern.memorystatus_vm_pressure_level (critical=4 abort; warn=2 sustained 30s abort), swap growth >4GiB, disk free <10GB; free percentage is informational only. Ollama/TTS/STT remain serialized.
- T7/T8 final guard update (msg_c8ca463e0658): pressure level 2 (warn) is recorded, not an abort; only critical level 4, swap growth >4GiB, or disk <10GB stop the owned model process.
- T6.1: 코디네이터 msg_e85bb2d641bd 최종 지시에 따라 pressure level2(warn) 지속시간도 관찰만 한다; 최종 중단 조건은 level4(critical), 시작 대비 swap +4GiB 초과, 디스크10GiB 미만이며 모델 창 독점과 배치 종료 언로드는 유지한다.
- T8 public aggregate pages/exports use persistent randomized Voice labels, including ordering, to honor the dispatch requirement that engine identity never reaches the browser; actual names are available only through the private aggregate_blind.py --reveal operator command.

- T9: 공유 server/app.py에는 /ws/call 라우터 import/등록 한 줄만 추가했다. 안내 캐시는 전체 엔진을 서버 시작에 로드하는 대신 선택한 엔진·언어의 session.start에서 1회 합성하고 session.ready 이후 발화를 허용한다(모델 독점 창/단일 엔진 메모리 제약).
- T9: T6.1의 완성 문장·commit_spoken 접두부 계약을 보존하므로 실서비스 경로는 선택적 첫 쉼표 분할을 끄고 문장 단위를 그대로 사용한다. raw 첫 토큰은 llm.done의 라운드 계측으로 복원하며 도구 없는 턴의 s_tool_call/s_tool_done은 null로 기록한다. Smart Turn/Qwen3-ASR 선택은 구현이 없는 상태에서 명시 오류로 거절한다(T11 범위).

- T9 리뷰 수정(코디네이터 msg_d2ec1269d129): T6.1 트랜잭션 보존을 위해 LLM 텍스트/event collector는 llm.done까지 역압 없이 소진하고 깊이 2 제한은 별도 TTS dispatcher 큐에만 적용한다. 정상 playback.finished는 전송한 전체 reply 문장의 재생 완료로 처리하고, 중간 barge-in의 정확한 접두부를 위해 문장별 playback.started를 확장 계약으로 요구한다; playback.finished는 turn.done 이후에만 받는다.
- T9 리뷰 수정: 발음 가능한 문자(문자/숫자)가 없는 정규화 조각은 TTS/tts.sentence를 생략하고 audio_skipped를 기록한다. 적어도 한 실제 reply 문장이 재생된 경우에만 접두부 안의 무음 서식을 원문으로 보존하며, 무음 조각만 있는 턴은 재생된 assistant로 commit하지 않는다. c_play_start는 filler 포함 첫 오디오이고 답변 기준 지표 client_vad_to_reply_play_ms/client_speech_to_reply_play_ms를 별도로 기록한다.
- T7 user-directed reduction (msg_6198f30429ba, 2026-10-30): CosyVoice3 RTF ~2.6 led to full-only, one repeat per sentence; existing rows retained, existing full repeat=0 reused, primary raw/full comparison and three-repeat p90 excluded for this engine. T8 full corpus and Whisper scoring remain required.

- T10: server/app.py에는 상담 정적 라우터 import/등록 한 줄만 추가하고 /blind, /blind/results, /report, /ws/call을 유지했다; VAD v5/ONNX WASM은 npm 설치본을 Vite 빌드에 복사해 외부 CDN 없이 제공한다. 마이크 없는 검증용 ?dev=1 WAV 입력은 VAD를 우회하며 로그 source=wav로 구분한다.
- T10: 최소 발화 필터를 통과한 onSpeechRealStart(minSpeechMs=250)에서 즉시 barge-in하며 redemptionMs 기본 500을 슬라이더로 제공한다; 로컬 패키지 0.0.30의 실제 API를 따랐다. 실시간 UI yue는 코디네이터 지시에 따라 지원 엔진 없음으로 비활성 표시한다.
- T10: 시작 swap 21494.88 MiB 대비 +5990.24 MiB로 중단 후 코디네이터 msg_4ce07150b917이 T9 모델 언로드와 재개를 승인했다; 새 baseline 27421.12 MiB, stub 작업은 swap 증가 관찰만/pressure4 또는 디스크10GB 미만 중단, 실제 모델은 별도 모델 창 해제 전 금지.
- T10: 코디네이터 msg_1d0f90793e52의 '모델 창 해제' 이후에만 Ollama+Whisper+Kokoro 실제 브라우저 검증을 시작했다; 실모델 시작 baseline 26531.75 MiB로 +4GiB/pressure4/디스크10GiB 가드를 적용하며 종료 시 소유 프로세스 종료와 Ollama 언로드를 수행한다.

- T11: 세션 길이는 준비 완료(session.ready)부터 WebSocket 종료까지로 고정하고 설정 변경 중 엔진이 달라진 연결은 mixed로 별도 집계한다; 시작/종료가 없는 T9/T10 과거 로그에서 통화 길이를 추정하지 않으며 워밍업 제외·CLI 가상 ACK 출처를 보존한다.
- T11: Smart Turn 미완료 조각은 최초 수신부터 최대 1.5초의 단일 대기창 안에서 다음 VAD 조각과 합친다; 병합된 이전 turn_num은 merged로 취소 기록하고 최신 번호로 응답한다. CPU 초기 로딩/추론 자체의 시간 제한은 아니며 실제 첫 ON cold 비용도 로그에 포함했다.
- T11: Smart Turn 공식 WhisperFeatureExtractor 전처리를 위해 메인 환경에 transformers 4.57.6을 추가했다; T1의 고정 v3.2 CPU ONNX 8,679,182 bytes를 재사용해 새 모델 다운로드는 0건이다.
- T11: 토스 발표의 콜 단위 3분 이상 비율·끼어들기 발생 통화 비율·콜당 문자 수 정의를 사용하되, 발표에서 공백/문장부호 세부 계수 규칙은 확인되지 않아 PoC는 STT text의 Python len(공백/문장부호 포함)으로 명시한다. CLI 수치는 자연 고객 인게이지먼트와 비교하지 않는다.
- T12: 1.4에서 선정 4개 엔진만 실측으로 갱신하고 비선정 모델은 평가 제외로 표시했다; 모델 구조의 B1/B2/B3 미확인 근거를 관측된 발음 점수로 채우지 않는다.

- T12: 새 터미널의 프로젝트 XDG_CACHE_HOME에서 Corepack 0.33.0이 pnpm 12.16.1의 pnpm.cjs를 찾지 못하는 오류를 재현했다; 루트 package.json에 기존 node_modules 생성 버전 pnpm 10.34.5를 고정해 README의 pnpm --dir web 명령을 재검증했다.
