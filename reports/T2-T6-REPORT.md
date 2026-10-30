# T2 정규화 + T6 텍스트 LLM 에이전트 완료 보고

작성일: 2026-10-30. 프로젝트: `/Users/jongram/Desktop/global_alpha/test/zivo-voice-cs-poc`.
T2를 먼저 완료한 후 T6를 구현했다. 루트 pyproject.toml, README, .gitignore는 수정하지 않았다.
모델 다운로드 및 설치 모델 변경은 없으며, 기존 Ollama `qwen3.5:35b-a3b`만 호출했다.

## 완료 기준과 실제 실행

| 기준 | 명령 | 실제 결과 |
|---|---|---|
| T2/T6 단위 테스트 | `.venv/bin/python -m pytest tests/test_normalize.py tests/test_llm.py -q` | **80 passed in 0.22s** |
| T2 고정 문장 표 | `.venv/bin/python scripts/normalize_demo.py` | **35행**: en/zh-CN/zh-TW/ja 각 8, yue 3; raw/lex/full 전부 생성 |
| T2 설계 5.2 기대값 | 위 pytest의 `test_design_examples` | **15/15** 일치: 코드, 금액, 날짜/시간, 4개 언어 50% |
| T2 사전 | `test_all_dictionary_items` | **13항목 × 5 locale** 표시→TTS 기대값, 긴 이름 우선 치환 확인 |
| T6 실제 Ollama 기본 시나리오 | `.venv/bin/python scripts/test_llm_scenarios.py` | **16/16 PASS**, exit 0, 길이 위반 0 |
| T6 실제 후속 대화 | `.venv/bin/python scripts/test_llm_dialogue.py` | **5/5 PASS**, exit 0: 성 누락, 코드 누락, 조회 2회 실패, 정책 예외, 불만 2회 |
| T6 텍스트 CLI | `.venv/bin/python scripts/llm_cli.py --locale en --text 'Please find my booking ZV-4821, surname Carter.' --events` | tool.call/result, llm.delta/done 이벤트와 조회 결과 확인 |

공용 venv 준비 전에는 `server/normalize/.venv/bin/python`으로 검증했고, 최종 검증은 T1의 공용 `.venv`로 실행했다. 동일 명령은 루트에서 `uv run python ...`으로 실행할 수 있다. 모듈 의존성은 각 requirements.txt 및 T1의 공용 pyproject에 있다.

## 구현 내용

- 원본 설계 5.1 사전 전체와 6.1 고정 문장 전체를 데이터화했다. `data/blind/sentences.yaml`은 `{id, locale, text}`의 YAML 리스트다.
- 정규화는 raw 원문 보존, lex 단일 패스 최장 일치, full 코드/금액/날짜/시간/숫자/퍼센트/기호 순서다. 일본어 날짜 특수 독음, 대만 兩의 단위별 처리, 코드의 선행 0·4자리 검증·복수 코드 거절을 포함한다.
- 가짜 예약 5건과 상품별 정책을 분리했다. DEMO_TODAY는 2026-11-01 Asia/Seoul이며, 호텔/투어 날짜 경계는 해당 날짜 전체를 포함하고 픽업은 정확한 24시간 경계를 포함한다.
- 예약 조회는 성 별칭/대소문자를 검증하며 실패 시 예약 존재나 상세를 노출하지 않는다. 성공한 세션만 해당 예약 정책을 볼 수 있고, 같은 예약의 성 검증이 실패하면 이전 권한도 제거한다.
- 네이티브 Ollama `/api/chat` 스트리밍과 도구 3개를 연결했다. 매 요청 `think:false`, `stream:true`, `keep_alive:"30m"`; 실제 thinking 출력은 최종 16건 모두 **0자**였다.
- 상담원 연결 후 상태는 queued이고 다음 턴을 거절한다. 조회 실패 2회는 서버에서도 연결을 강제한다. 취소 실행 도구는 없다.
- 정책 결과의 `spoken_summary`는 계산된 정수 금액으로 생성하며 LLM이 그대로 답하도록 했다. 초기에 모델이 숫자를 잘못 재표기하거나 취소 실행을 제안한 문제가 있어 보강했다. 변경 이유는 docs/DEVIATIONS.md에 기록했다.
- OpenCC `zh-TW=s2twp`, `zh-CN=t2s` 적용과 changed-character count, 길이 위반을 JSONL에 남긴다. 길이 초과 답변을 자르지 않는 통합 테스트가 통과했다.
- HTTP 스트림을 취소하면 연결이 닫히는 것을 async 테스트로 확인했다. T9의 실제 재생 기준 대화 기록 복구는 T9 담당이다.

## 실제 기본 시나리오 결과

| locale | 시나리오 | 도구 순서 | 응답 |
|---|---|---|---|
| en | lookup PASS | lookup_reservation | Your reservation ZV-4821 is confirmed at Hanbit Stay Myeongdong from November 3rd to November 5th. |
| en | mismatch PASS | lookup_reservation | I couldn't find a reservation under the code ZV-4821 for Smith. Could you please double-check the reservation code? |
| en | cancellation PASS | lookup_reservation → get_cancellation_policy | Today the cancellation fee is 50%, or ₩143,000, and the refund is ₩143,000. |
| en | human PASS | transfer_to_human | You are now queued for a human agent. The estimated wait time is about 3 minutes. |
| zh-CN | lookup PASS | lookup_reservation | 您的预订已确认，是11月7日晚上7点半从仁川国际机场1号航站楼到乙支路灯笼酒店的接机服务。请问还有什么可以帮您？ |
| zh-CN | mismatch PASS | lookup_reservation | 预订号或姓氏可能有误，请再确认一下。 |
| zh-CN | cancellation PASS | lookup_reservation → get_cancellation_policy | 今天取消需支付0%的费用，即0韩元，可退还78,000韩元。 |
| zh-CN | human PASS | transfer_to_human | 已为您转接人工客服，排队等待时间约为3分钟。 |
| zh-TW | lookup PASS | lookup_reservation | 您的訂單編號ZV-5196已確認，入住漢江月光套房飯店，11月10日至12日，共兩晚，總價412,000韓元。 |
| zh-TW | mismatch PASS | lookup_reservation | 查無此訂房，請問訂單編號或姓氏是否有誤？ |
| zh-TW | cancellation PASS | lookup_reservation → get_cancellation_policy | 今天取消需支付0%的手續費，也就是0韓元，可退還412,000韓元。 |
| zh-TW | human PASS | transfer_to_human | 已為您轉接真人客服，目前排隊中，預計等待約3分鐘。 |
| ja | lookup PASS | lookup_reservation | 景福宮韓服体験ツアーの予約が確認できました。2026年11月5日10時からです。 |
| ja | mismatch PASS | lookup_reservation | 申し訳ありませんが、予約番号 ZV-2610 と田中様の予約は見つかりませんでした。別の予約番号をお調べでしょうか。 |
| ja | cancellation PASS | lookup_reservation → get_cancellation_policy | 本日のキャンセル料は0%で0ウォン、返金額は130,000ウォンです。 |
| ja | human PASS | transfer_to_human | 人間の担当者におつなぎします。お待ちいただく時間は約3分です。 |

문자체계 검사는 en의 비영어 문자, zh의 간체/번체 및 가나/한글 혼입, ja의 가나 포함 여부를 확인한다. 환불 금액은 아라비아/한자/통화 기호 표기를 모두 검사한다. 출력은 직접 검토했으나 원어민 자연스러움 평가를 대신하지 않는다.

## 정책 금액 검증

| 예약 | 오늘 수수료 | 오늘 환불 |
|---|---:|---:|
| ZV-4821 | 50%, 143,000 KRW | 143,000 KRW |
| ZV-7305 | 0 | 78,000 KRW |
| ZV-5196 | 0 | 412,000 KRW |
| ZV-2610 | 0 | 130,000 KRW |
| ZV-9034 | 0 | 300,000 KRW |

호텔 무료/50%/환불 불가 전환, 픽업 24시간 정확한 시각 및 1초 이후, 투어 무료 날짜 끝 및 다음날, UTC→Asia/Seoul 변환을 단위 테스트했다.

## 지연과 한계

| locale | 첫 원시 텍스트 토큰 ms 중앙값 | 전체 응답 ms 중앙값 |
|---|---:|---:|
| en | 3495.4 | 3996.5 |
| zh-CN | 3982.4 | 4507.1 |
| zh-TW | 3790.0 | 4450.7 |
| ja | 3661.9 | 4107.2 |

첫 텍스트 토큰은 사용자 요청 이후 도구 왕복을 포함한다. 첫 최초 스모크는 cold 상태 약 20,952 ms였으나 최종 프롬프트와 다른 초기 실행이므로 표와 직접 비교하지 않는다. 최종 warmed 결과도 설계의 1초 경보 기준을 초과한다. 새 모델을 받지 않았고 성능을 과장하지 않았다. 현재 모델은 keep_alive 30m이므로 TTS 단독 벤치 전에 코디네이터가 언로드해야 한다.

중국어 출력은 토큰 경계의 어휘를 보존하려고 문장 종결까지 버퍼링한다. TTFT는 원시 토큰 시점이며 T9에서는 문장 출력 시각도 따로 기록해야 한다. 영어·일본어는 토큰 단위로 출력된다.

초기 시행착오 자료도 보존했다. 첫 16건 실행에서 실제 도구 누락 3건과 길이 위반 2건이 있었고, 뒤의 실행에서 금액 재표기 오류를 확인했다. 최종 16건은 모두 통과하고 길이 위반/문장 수 위반은 0건이다. 초기 길이 위반은 원문 그대로 `data/logs/llm-20261030.jsonl` 및 초기 리포트에 남아 있다. 유한한 시나리오 통과가 모든 발화에 대한 LLM 정확도를 보장하지는 않는다.

T2/T6 완료 기준 중 미완/실패 항목은 없다. 음성 인식·TTS·브라우저·T9 전체 파이프라인은 이 작업 범위가 아니다.

## 인계 API

- `server.normalize.pipeline.normalize(text, locale, level)` → string.
- `server.normalize.pipeline.variants(text, locale)` → display_text / tts_text_raw / tts_text_lex / tts_text_full.
- `server.normalize.codes.parse_spoken_code(text, locale)` → canonical code or None.
- `server.normalize.lexicon.initial_prompt(locale)` → STT용 사전 표기; `display_name(id, locale)` → 도구용 이름.
- `VoiceAgent(locale).stream_turn(text)` → tool.detected / tool.call / tool.result / llm.delta / llm.done dict events.
- `VoiceAgent(locale).turn(text)` → 최종 record. 세션마다 독립 인스턴스를 만들어야 한다.
- 상세 사용법: server/normalize/README.md, server/llm/README.md.

## 생성/변경한 소스 파일

- `data/lexicon.yaml`
- `data/blind/sentences.yaml`
- `data/fake_reservations.json`
- `data/policies.json`
- `server/normalize/lexicon.py`
- `server/normalize/codes.py`
- `server/normalize/num_en.py`
- `server/normalize/num_zh.py`
- `server/normalize/num_ja.py`
- `server/normalize/pipeline.py`
- `server/normalize/requirements.txt`
- `server/normalize/README.md`
- `server/llm/fake_db.py`
- `server/llm/tools.py`
- `server/llm/tool_schemas.json`
- `server/llm/ollama_client.py`
- `server/llm/prompts/system.md.j2`
- `server/llm/requirements.txt`
- `server/llm/README.md`
- `scripts/normalize_demo.py`
- `scripts/llm_cli.py`
- `scripts/test_llm_scenarios.py`
- `scripts/test_llm_dialogue.py`
- `tests/test_normalize.py`
- `tests/test_llm.py`
- `docs/DEVIATIONS.md`

## 검증 산출물

- reports/normalize-demo.md: 고정 문장 35개 표.
- reports/t2-t6-tests.log: 최종 단위 테스트 출력.
- reports/llm-scenarios.json, reports/llm-scenarios-run.log: 최종 실제 16개 응답/도구/측정.
- reports/llm-dialogue.json, reports/llm-dialogue-run.log: 최종 실제 추가 대화 5개.
- reports/llm-first-smoke.jsonl, reports/llm-second-smoke.jsonl: 최초 모델 동작 확인.
- reports/llm-scenarios-initial.json / .log, reports/llm-scenarios-second.json, reports/llm-dialogue-initial.json: 실패 분석 근거.
- data/logs/llm-20261030.jsonl: 실제 실행 전체의 세션별 응답 로그.

## 참조

Ollama 호출 형식은 [공식 tool calling 문서](https://docs.ollama.com/capabilities/tool-calling), thinking 비활성화는 [공식 thinking 문서](https://docs.ollama.com/capabilities/thinking)를 확인했다. 설치된 Ollama 버전은 0.33.0이다. 라이브러리는 설치 메타데이터와 [num2words](https://pypi.org/project/num2words/) / [cn2an](https://pypi.org/project/cn2an/) 프로젝트 페이지에서 라이선스를 확인했다: num2words LGPL, cn2an MIT.
