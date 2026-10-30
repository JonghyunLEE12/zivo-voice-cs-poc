# T9 실시간 서버 파이프라인 코드 리뷰 (읽기 전용)

대상: `server/pipeline/{turn,api,latency,sentence_splitter}.py`, `server/tts/client.py`, `server/app.py`(/ws/call 등록), `server/llm/{ollama_client,sentences}.py`·`server/stt/mlx_whisper_stt.py` 경계, `scripts/ws_wav_client.py`, `scripts/validate_voice_pipeline.py`, `tests/test_voice_pipeline.py`, `tests/voice_stubs.py`.
검증: `tests/test_voice_pipeline.py` stub 테스트 36 passed. 아래 [재현] 표시 항목은 scratchpad의 stub 프로브(`probe.py`, 모델 로드 없음)로 직접 확인했다. 코드·git은 건드리지 않았다.

## 높음

### 1. 응답이 3문장 이상이면 바지인 시 이미 들은 도구 결과까지 롤백된다 [재현]
- 위치: `server/pipeline/turn.py:85`, `:127`, `:140` (큐 maxsize=2를 LLM generator에 직접 역압으로 걸었다) + 경계 `server/llm/ollama_client.py:276-301`
- 왜 문제인가: T6.1 agent는 마지막 라운드 done 뒤에 문장을 연속 yield하고, 마지막 yield 다음에야 `_publish`와 `success=True`, `llm.done`이 실행된다. produce가 `queue.put`에서 막혀 있으면 agent는 아직 "실패 상태"다. 이때 바지인이 오면 aclose로 `success=False`가 되고, `commit_spoken`은 `[user] + 재생 문장`만 남기며 `verified_codes`/`lookup_failures`/`transferred`를 되돌린다.
  - 프로브 결과: 2문장 응답에서 문장0 재생 후 바지인하면 `verified={'ZV-4821'}`, 기록 `user/assistant(tool)/tool/assistant`. 4문장 응답에서 똑같이 하면 `verified=set()`, 기록 `user/assistant`만 남는다. `s_llm_first_token=None`, `llm.done` 미수신, LLM jsonl 로그도 안 남는다.
  - 사용자는 "예약을 찾았습니다"를 들었는데 다음 턴 모델은 검증 코드가 없는 상태가 된다. 응답 길이(모델 변동)에 따라 상태가 달라지는 비결정성이다. 모델이 1~2문장 규칙을 어기는 것은 T6.1에서도 관찰됐다(길이 위반은 로그만 남긴다).
- 고칠 방법: LLM 쪽에는 역압을 걸지 않는다. produce는 agent 스트림을 끝까지(`llm.done`까지) 무제한 리스트/큐로 소진하고, 깊이 2 제한은 TTS 합성 단계(consume가 동시에 최대 2문장만 합성/보유)에만 둔다. T6.1이 이미 라운드 완료 뒤에 문장을 내보내므로 지연 손해가 없다. 회귀 테스트: 4~5문장 응답 + 문장0 재생 후 바지인 시 `verified_codes` 유지.

### 2. `playback.finished`가 "모두 재생됨"을 뜻하지 않아 문장별 ack가 없으면 정상 턴이 통째로 롤백된다 [재현]
- 위치: `server/pipeline/api.py:89-94`, `server/pipeline/turn.py:209-221`, `:241-252`
- 왜 문제인가: commit은 `self.started`(문장별 `playback.started`)만 본다. 설계 4.4 표의 `playback.started`는 "첫 청크는 지연 측정에 쓴다"이고, 문장마다 보내야 한다는 계약은 설계서 어디에도 없다. T10 브라우저가 첫 청크만 ack하고 `playback.finished`를 보내면 첫 청크가 filler라서 `started`가 비고, `commit_spoken('')`가 턴 전체와 도구 상태를 되돌린다(프로브: 기록 `[]`, `verified=set()`). 다 들은 응답이 기록에서 사라진다.
- 고칠 방법: `playback.finished` 수신 시 이 턴에서 오디오를 보낸 reply 문장 인덱스 전체를 `started`에 넣은 뒤 reconcile한다(끝까지 재생됐다는 뜻이므로). 문장별 `playback.started`(tts.sentence의 `first_chunk_seq`)를 보내야 바지인 시 정확한 접두부가 남는다는 점은 DEVIATIONS와 T10 spec에 프로토콜로 명시한다.

## 중간

### 3. `」`, `）` 같은 닫는 부호가 단독 "문장"이 되어 TTS로 가고, 빈 PCM이면 턴 전체가 실패한다 [재현]
- 위치: `server/llm/sentences.py:7-20`(분할), `server/pipeline/turn.py:160-166`, `server/tts/client.py:65-66`, `server/normalize/pipeline.py:25`
- 왜 문제인가: `split_sentences('ホテルは明洞です。」')` → `['ホテルは明洞です。', '」']`, `'…需要？）'` → `[..., '）']`, `'He said "OK." Then left.'` → `['He said "OK.', '" Then left.']`, `'1. Check in.\n2. Pay.'` → `['1.', ' Check in.\n', '2.', ' Pay.']`. `）`는 full 정규화에서 지워져 tts_text가 빈 문자열이 된다. 엔진이 빈 텍스트에 4xx를 주거나 0바이트를 주면 `Truncated or empty engine PCM`으로 그 턴 전체가 `turn_failed`가 된다. zh/ja에서 괄호·인용 부호는 흔하다.
- 고칠 방법: `split_sentences`에서 종결부호 뒤에 붙은 닫는 부호(`」』）)"'”’】`)를 앞 문장에 흡수한다. 번호 목록 `^\d+\.` 은 경계에서 뺀다. 이 함수는 agent와 T9 계약 검사가 같이 쓰므로 한 곳만 고치면 된다. 추가로 consume에서 `tts_text`에 발음할 문자가 없으면 합성을 건너뛰고(오디오 없이 `tts.sentence`만 보내거나 생략) 그 문장을 재생된 것으로 볼지 규칙을 정한다.

### 4. `c_play_start`와 `client_vad_to_play_ms`가 filler 재생 시각을 잡아 목표 지표가 좋아 보이게 된다
- 위치: `server/pipeline/turn.py:245`, `server/pipeline/latency.py:23-24`
- 왜 문제인가: 도구 턴(prefetch 포함, T9 검증 입력 4개 모두 해당)에서 첫 오디오는 filler라 `c_play_start`가 filler 시각이다. 설계 4.5의 목표 `c_play_start - c_vad_end ≤ 1.5초`가 실제 답변이 아닌 안내 음성 지연으로 채워진다. `c_reply_play_start`는 기록하지만 durations에 대응 지표가 없다.
- 고칠 방법: durations에 `client_vad_to_reply_play_ms = c_reply_play_start - c_vad_end`(그리고 speech_end_est 버전)를 추가하고, 리포트에서 filler 지표와 reply 지표를 나눠 쓴다. `c_play_start`의 의미(첫 오디오, filler 포함)를 로그에 명시한다.

### 5. 라이브 검증의 첫 locale(en)에 Whisper·Ollama 모델 로드 시간이 섞인다
- 위치: `scripts/validate_voice_pipeline.py:76-86`, `server/pipeline/turn.py:52-56`, `server/stt/mlx_whisper_stt.py:98`
- 왜 문제인가: STT는 첫 transcribe에서 모델을 로드하고, Ollama 35B도 첫 `/api/chat`에서 로드된다. 검증 스크립트는 워밍업 없이 en부터 돌리므로 en의 `stt_ms`, `llm_ttft_ms`가 로드 시간으로 오염된다. T6.1 벤치는 워밍업 후 측정이라 비교가 어긋난다.
- 고칠 방법: live 모드에서 측정 전에 Whisper 1회 더미 transcribe와 `OllamaClient.warmup()`을 돌리고(또는 버리는 워밍업 턴 1회), 결과에 `warm: true`를 기록한다. 서버 쪽은 session.start에서 STT 모델 로드를 옵션으로 선행하는 방법도 있다.

### 6. 취소된 턴의 STT 디코드가 계속 돌아 다음 턴 STT가 뒤에서 기다린다. stub 테스트는 반대로 "취소됨"을 단언한다
- 위치: `server/stt/mlx_whisper_stt.py:23-24`, `:124-126`, `tests/voice_stubs.py:27-32`, `tests/test_voice_pipeline.py:102`
- 왜 문제인가: 단일 워커 executor라 취소된 턴(최대 60초 오디오)의 디코드가 끝날 때까지 새 턴 STT가 대기한다. 새 턴의 `stt_ms`(= `s_stt_done - s_turn_decided`)에 이전 턴 디코드 시간이 들어간다. StubSTT는 `asyncio.sleep`이라 실제로 취소되고 테스트가 `stt.cancelled`를 단언해서, 실제 경로와 다른 성질을 통과로 보여준다.
- 고칠 방법: 로그에 `result.elapsed_ms`(STT 내부 시간)와 executor 대기 시간을 따로 남긴다. stub은 "await는 취소되지만 작업은 끝까지 돈다"를 흉내 내도록 thread executor 기반으로 바꾸고, 대기 시간이 다음 턴에 귀속되는지 테스트한다. 짧은 간격 연속 발화(superseded)는 Smart Turn(T11) 전까지 알려진 제약으로 문서화한다.

### 7. 엔진이 멈추면 최대 180초 동안 오디오도 오류도 없다
- 위치: `server/tts/client.py:48`, `server/llm/ollama_client.py:61`, `server/tts/client.py:76-87`(FillerCache 전역 lock)
- 왜 문제인가: read timeout 180초만 있고 턴 단위 마감이 없다. TTS 엔진이 첫 바이트를 안 주면 사용자는 3분간 무음이다(바지인으로는 끊을 수 있다). FillerCache는 전역 lock을 잡은 채 합성하므로 한 엔진이 멈추면 모든 세션의 session.start가 같이 막힌다.
- 고칠 방법: 문장별 첫 바이트 마감(예: 15초)과 턴 전체 마감을 `asyncio.timeout`으로 두고 초과 시 `error code=engine_timeout`. FillerCache는 key별 lock(또는 key별 Future)으로 바꾼다.

## 낮음

### 8. `s_llm_first_token`이 버려진 도구 라운드의 사전 발화 토큰을 잡을 수 있다
- 위치: `server/pipeline/turn.py:134-137`
- 왜 문제인가: 모든 라운드의 `first_token_ms` 중 최솟값을 쓴다. 1라운드에 preamble 텍스트를 쓰고 도구를 부르면, 말하지 않는(버려진) 토큰이 "첫 텍스트 토큰"이 되고 `first_sentence_ms`에 도구 실행과 2라운드가 통째로 들어간다.
- 고칠 방법: `s_llm_first_token`(전체 최초 raw 토큰)과 `s_llm_reply_first_token`(= `metrics['first_token_ms']`, 실제 말한 라운드)을 둘 다 기록하고, 리포트의 `llm_ttft`가 어느 쪽인지 명시한다.

### 9. stream_turn 내부의 `stream_chat` generator를 aclosing하지 않는다
- 위치: `server/llm/ollama_client.py:230`
- 왜 문제인가: `tool.detected` yield가 `stream_chat` 루프 안에 있다. 그 지점에서 바깥 generator가 닫히면 안쪽 HTTP 스트림은 GC finalizer가 나중에 닫는다. 지금 produce는 tool.detected 처리 중 실제로 suspend하지 않아서(빈 큐에 put) 재현되지 않았다. 다만 tool.detected를 클라이언트로 보내는 등 await가 하나만 생겨도 Ollama 연결이 즉시 안 닫히는 누수가 된다.
- 고칠 방법: `async with aclosing(self.client.stream_chat(...)) as chunks:`로 감싼다.

### 10. 이중 취소 시 run의 cleanup gather가 끊겨 commit_spoken이 `_active` 상태에서 호출될 수 있다
- 위치: `server/pipeline/turn.py:199-207`, `:235-237`
- 왜 문제인가: `Turn.cancel`이 `gather(self.task)`를 기다리는 동안 핸들러 자체가 취소되면(서버 종료 등) run 태스크가 finally 안에서 두 번째 CancelledError를 받아 자식 cleanup을 기다리지 못한다. 그 뒤 `reconcile()`이 `RuntimeError('Cancel and await/close stream_turn before commit_spoken')`를 낼 수 있다.
- 고칠 방법: run의 finally gather를 `asyncio.shield`로 감싸거나, cancel에서 자식 태스크까지 명시적으로 await한다. reconcile은 `agent._active`면 건너뛰고 로그만 남긴다.

### 11. `llm_started`를 stream 진입 전에 켜서, stream_turn이 시작 검사에서 실패하면 이전 턴 기록을 지울 수 있다
- 위치: `server/pipeline/turn.py:102`, `:217-218`, 경계 `server/llm/ollama_client.py:175-185`
- 왜 문제인가: stream_turn은 `_active`/`transferred` 검사에서 `_playback`을 바꾸기 전에 예외를 낸다. 이 경우 reconcile의 `commit_spoken('')`이 이전 턴의 `_playback`에 적용되어 이전 턴과 도구 상태가 롤백된다. 지금은 Call.start의 transferred 사전 검사 덕에 경로가 막혀 있지만 방어가 우연에 의존한다.
- 고칠 방법: `llm_started`를 첫 이벤트를 받은 뒤 켜거나, agent가 turn id를 돌려주고 commit_spoken이 그 id를 확인하게 한다.

### 12. 종료된 턴에 대한 barge_in에는 응답이 없다
- 위치: `server/pipeline/turn.py:224-225`, `server/pipeline/api.py:82-85`
- 왜 문제인가: error나 playback.finished로 finalized된 턴에 barge_in이 오면 아무 프레임도 안 보낸다. `turn.cancelled`를 기다리는 클라이언트(ws_wav_client 포함)는 타임아웃까지 대기한다.
- 고칠 방법: finalized여도 `turn.cancelled`(reason `already_finished`)를 보내거나, 프로토콜에 "응답 없음"을 명시한다.

### 13. ws_wav_client와 live 검증이 "재생된 문장까지만 commit"을 검증하지 않는다
- 위치: `scripts/ws_wav_client.py:67-74`, `scripts/validate_voice_pipeline.py:83-90`
- 왜 문제인가: 바지인은 첫 오디오 프레임(= filler)에서 보내므로 reply 문장이 하나도 재생되지 않은 경우만 본다. 연결당 1턴이라 다음 턴에 기록이 이어지는지도 안 본다. 필수 마크 검사는 턴 로그 파일이 아니라 `turn.done` 이벤트의 marks를 보고, `s_tool_call`/`s_tool_done`은 빠져 있다(T9 완료 기준은 "턴 로그에 4.5 서버 포인트가 모두").
- 고칠 방법: `--barge-after-sentence N` 옵션과 2턴 시나리오를 추가하고, 끝난 뒤 `data/logs/turns-*.jsonl`의 최신 revision에서 마크와 `spoken_text`를 확인한다.

### 14. 문장 계약 검사가 너무 엄격해 무해한 분할 차이로 턴이 실패할 수 있다
- 위치: `server/pipeline/turn.py:120-122`
- 왜 문제인가: 같은 `split_sentences`를 쓰지만 약어/소수 판정이 앞뒤 문맥(`text[:i+1]`, `text[i+1]`)에 의존하고, 분할 뒤 OpenCC 변환이 적용된다. 조각을 다시 나누면 결과가 달라지는 입력이 생기면 `LLM violated the complete sentence contract`로 응답 전체를 버린다.
- 고칠 방법: 위반 시 턴을 실패시키지 말고 경고만 남긴 채 agent가 준 문자열 그대로 한 문장으로 쓴다. commit_spoken 접두부 계약은 agent 문자열 그대로이므로 깨지지 않는다.

### 15. 기타
- `server/pipeline/turn.py:252` 청크마다(약 1초 오디오) 동기 파일 append. 지금 규모에선 괜찮지만 이벤트 루프를 막는다. 첫 ack와 문장 첫 ack에서만 쓰도록 줄일 수 있다.
- `server/pipeline/sentence_splitter.py:18` feed/finish 경로에는 `\n`이 없어 `split_sentences`와 규칙이 다르다. 지금은 쓰지 않지만 나중에 토큰 스트림을 붙이면 두 분할기가 어긋난다.
- `turn.done.handoff_queued`는 reconcile 전 값이다. 빈 재생으로 롤백되면 실제 transferred가 false가 될 수 있으므로 T10에서 확정 상태로 쓰면 안 된다(T6.1 보고서 경고와 같다).
- `voice()`(`server/tts/client.py:24`)는 kokoro 외 엔진에서 매 문장 MANIFEST를 읽고, 이 경로는 테스트가 없다(StubTTS가 TTSClient를 우회).

## 설계서 대비 프로토콜 확인
- 헤더 `<BBHI>`, kind 1/2, turn_num, 클라이언트→서버 5종, 서버→클라이언트 7종 모두 설계 4.4와 맞다. 추가 필드(`tts.sentence.role/first_chunk_seq`, `tool.*.source`, `turn.done.handoff_queued`, `session.ready`)는 하위 호환이며 `session.ready`는 DEVIATIONS에 있다.
- 설계에 없는 암묵 계약 2개: (a) 문장별 `playback.started`가 있어야 기록이 남는다(높음 2), (b) `playback.finished`는 `turn.done` 뒤에만 허용된다. 둘 다 DEVIATIONS와 T10 spec에 적어야 한다.
- 취소 ack가 정리 전에 나가고 send lock 아래 cancelled 플래그로 늦은 TTS/LLM 프레임을 막는 구조는 올바르다. 엔진 HTTP는 `aclosing` + `async with`로 취소 시 닫히는 것을 확인했다(TTS는 테스트 있음).

## 실제 모델 검증 전에 꼭 고칠 것
1. 높음 1: LLM 스트림에 큐 역압을 걸지 말고 끝까지 소진한 뒤 TTS 단계에만 깊이 2를 적용. 3문장 이상 + 바지인 회귀 테스트.
2. 높음 2: `playback.finished` 시 보낸 reply 문장 전체를 재생된 것으로 처리. 문장별 ack 계약 문서화.
3. 중간 3: 닫는 부호/번호 목록 분할 수정과 빈 tts_text 건너뛰기(zh/ja 실모델에서 바로 터질 수 있음).
4. 중간 4·5: reply 기준 체감 지연 지표 추가, live 검증 전 Whisper·Ollama 워밍업. 이게 없으면 T9 실측 수치가 설계 4.5 목표를 잘못 판정한다.
5. 중간 7은 검증 중 엔진이 멈췄을 때 원인 파악을 위해 최소한 TTS 첫 바이트 마감만이라도 권장.
