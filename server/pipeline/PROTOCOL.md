# Call WebSocket v1

Run `.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 8000` with the selected local TTS engine listening at the configured port. Connect to `ws://127.0.0.1:8000/ws/call`. This is a local PoC; no remote model API or automatic model download is used.

## Session and turn lifecycle

1. Send `{"type":"session.start","locale":"en","tts_engine":"kokoro","normalize":"full","stt":"whisper","turn_detect":"vad"}`. The same message can update settings later; an unfinished prior turn is cancelled first. Locale changes create a fresh language-specific agent; other changes preserve reconciled history.
2. Wait for `session.ready` (includes `session_id` and settings). The server checks `/v1/info` and caches the selected engine/locale's fixed full-normalized filler once per process. It does not load all four TTS engines. Failure sends `error`; no ready event means the new configuration was not accepted.
3. Send `{"type":"utterance","turn_num":1,"samples":16000,"t_vad_end":1234.5}` followed **immediately** by one binary frame. `turn_num` is an unsigned 32-bit integer, strictly increasing for the connection. `samples` is 1–960000 (up to 60 seconds). PCM is mono 16-bit signed little-endian at 16000 Hz. Optional `c_speech_end_est` uses the same client clock as `t_vad_end`.
4. Receive `stt.result`, `llm.delta`, tool events, and `tts.sentence`/audio. JSON events carry `turn_num`; events can interleave while LLM and TTS run concurrently. A later utterance cancels an unfinished previous turn and reconciles history before starting the next agent request. No concurrent agent mutation is allowed within one call.
5. `turn.done` means **all server audio has been sent**, not that browser playback finished. Continue draining the playback queue. Send `playback.finished` only after `turn.done` and the last audio finishes.
6. A true `handoff_queued` in `turn.done` means the actual `transfer_to_human` tool queued the fake handoff. Stop the microphone loop. A model sentence claiming transfer is not sufficient; known T6.1 tool omissions are preserved in the log.

`whisper` and `vad` are implemented. Unavailable `qwen3asr` and `vad+smartturn` return explicit `invalid_message` errors; Smart Turn belongs to T11. Locale support is en/zh-CN/zh-TW/ja, plus yue only for VoxCPM2/CosyVoice3. No automatic language switching is enabled here. Text control frames are limited to 16 KiB and STT output to 4000 characters.

## Binary frames

Eight-byte header `struct.pack('<BBHI', kind, 0, chunk_seq, turn_num)` followed by PCM16LE:

| Offset | Type | Meaning |
| --- | --- | --- |
| 0 | u8 | 1 microphone, 2 server TTS |
| 1 | u8 | reserved, must be 0 |
| 2 | u16 LE | microphone must be 0; TTS increments per chunk across the whole turn |
| 4 | u32 LE | turn number |
| 8 | bytes | mono PCM16LE; microphone 16 kHz, TTS 24 kHz |

An utterance's binary payload must contain exactly `samples * 2` bytes. TTS HTTP transport chunks are reassembled across odd byte boundaries and split into at most 48000-byte PCM chunks. Empty/truncated engine responses fail the turn.

## Audio and playback acknowledgments

`tts.sentence` contains the design fields (`turn_num`, `seq`, `display_text`, `tts_text`, `engine`, `sample_rate`) plus `role: "filler" | "reply"` and `first_chunk_seq`. `seq` counts all audio sentences, including filler. All following kind=2 frames until the next `tts.sentence` belong to that sentence; text/tool events may interleave. Discard frames belonging to a cancelled/obsolete turn.

For **each sentence whose playback actually starts**, send:

```json
{"type":"playback.started","turn_num":1,"chunk_seq":3,"t_client":1500.0}
```

Use the binary `chunk_seq` that begins playback, not `tts.sentence.seq`. Acknowledging every chunk is also accepted. The server maps it back to the sentence. On interruption it commits only the contiguous prefix with explicit sentence-start acknowledgments; on playback.finished it marks all sent reply sentences as played. Filler is never assistant history. Receipt/download of PCM is not playback. Unknown or stale chunk/turn acknowledgments do not extend history.

After server generation and playback both finish, send the following even if only the first audio start was acknowledged. It asserts that the entire sent audio queue played successfully, so all sent reply sentences are committed:

```json
{"type":"playback.finished","turn_num":1,"t_client":2500.0}
```

On barge-in, the browser first stops audio sources and clears its queue, then sends:

```json
{"type":"barge_in","turn_num":1,"played_ms":400.0,"t_client":1800.0}
```

The server marks the turn cancelled, cancels STT await/LLM/TTS tasks, and sends `turn.cancelled` promptly before awaiting cleanup. A send lock plus the cancelled flag suppresses later audio/done events. Closing the TTS/LLM async generators closes HTTP responses; cleanup finishes before another turn can mutate agent history. Whisper's already-running worker-thread GPU decode cannot be forcibly stopped; its result is discarded and the existing single-worker STT service serializes later calls. Engine GPU/process termination can also take longer than the cancel acknowledgment.

A barge-in after `turn.done` still reconciles history while playback remains unfinished. Duplicate cancellation, obsolete turn IDs, and acknowledgments after finalization are ignored. Disconnect cancels and awaits cleanup without trying to send a terminal response.

On `error` with `code: "turn_failed"`, stop/clear playback for its `turn_num`; partial generated audio is not a successful response. Invalid frames return `invalid_message` without starting inference. Session engine/cache failures use `session_failed`.

## Sentences, concurrency, and history

T6.1's agent buffers a tool-capable round until its done frame, removes discarded preambles, and emits final `llm.delta` events as complete sentences. T9 preserves those exact strings including leading whitespace to satisfy `commit_spoken`. Its sentence boundary adapter handles decimals, codes, and dotted abbreviations. The standalone streaming splitter supports the optional first-comma boundary, but production leaves that option disabled because partial T6 sentence commits are not supported.

The LLM collector drains through llm.done without awaiting socket writes or the TTS queue. A text/event buffer decouples its transaction from a dispatcher and a queue of at most two pending TTS sentences; only the TTS side applies backpressure. The model output remains limited by the existing agent token/round limits. This preserves tool state even when a long final answer is interrupted during playback. One TTS HTTP request is active per call; as soon as sentence i's PCM has been sent, sentence i+1 synthesis proceeds while the browser plays buffered audio. Filler is queued once at the first tool detection/call, using cached PCM without fresh inference. Terminal closing quotes/brackets stay attached to their preceding sentence and line-leading numbered list markers are not sentence boundaries. Normalized fragments containing no letters or numbers skip TTS and metadata emission with audio_skipped=no_speakable_text in the log. Such formatting fragments may be retained inside an otherwise played history prefix to preserve the agent exact-string contract; an all-silent prefix is never committed as speech. The agent's prefetch behavior is unchanged.

After producer cleanup, cancellation/error/replacement/disconnect reconciles with `commit_spoken`. Successful agent turns preserve complete tool pairs when an audible reply prefix exists; empty audible prefixes roll back the turn/tool state as specified by T6.1. Cancelled LLM turns retain only user + audible assistant prefix. The fake handoff has no external side effect.

## Clock domains and logs

`data/logs/turns-YYYYMMDD.jsonl` is append-only. Group by `(session_id, turn_num)` and take the greatest `revision` for the latest state. `done` snapshots can precede playback acknowledgments; later revisions preserve client marks and final spoken text. Keep all revisions for event timing analysis.

All `s_*` marks use server `perf_counter()` milliseconds; `c_*` and `t_filler_play` use the client clock. Never subtract a client mark from a server mark. `s_turn_decided == s_rx` in VAD mode. Absent stages (e.g. tools in a greeting, or stages not reached before cancellation) have null marks, not fabricated timestamps.

`s_llm_first_token` is backfilled from T6.1 round metrics relative to the agent request start, selecting the earliest raw text token including subsequently discarded tool preambles. `s_first_sentence` is the later delivered/validated first reply sentence. `s_tts_first_audio` records the first **new reply synthesis** byte; cached filler does not satisfy it. `c_play_start` means first audio including filler, stated in c_play_start_meaning. `c_reply_play_start` separates actual reply playback; client_vad_to_reply_play_ms and client_speech_to_reply_play_ms measure reply latency independently from the first-audio metrics. If the client only acknowledges the first filler and later playback.finished, no reply start timestamp is invented; reply latency remains null even though the completed reply is committed. All original LLM metrics, raw text, tools, discarded preambles and transferred state are retained in the turn log when the LLM reaches done. Sentence rows retain raw/lex/full normalization variants.

The headless WAV client sends simulated playback acknowledgments when it receives each sentence's first chunk; it never drives speakers. Its reported latency is transport-only and must not be reported as measured browser/human playback latency. T10 owns actual browser VAD/playback measurement.

## Verification

```sh
mkdir -p reports/t9
.venv/bin/python -m pytest tests/test_voice_pipeline.py -q --basetemp reports/t9/pytest-temp
.venv/bin/python scripts/validate_voice_pipeline.py --mode stub
# Only after coordinator model-window release:
.venv/bin/python scripts/validate_voice_pipeline.py --mode live --model-window-released
# Against an already managed server:
.venv/bin/python scripts/ws_wav_client.py reports/stt/say/en.wav --locale en --output reports/t9/manual-en
.venv/bin/python scripts/ws_wav_client.py reports/stt/say/en.wav --locale en --barge-in --output reports/t9/manual-barge
```

Stub implementation and fixture app exist only under `tests/`. The live driver runs the real `server.app`, Whisper, existing Ollama model, and one owned Kokoro process. Before measured turns it calls Ollama warmup and discards one real en WebSocket turn to warm Whisper inside the server process, recording warmup.json and warm=true on measured outputs; locale-specific cold effects are still possible. It it stops owned processes and unloads Ollama in `finally`. It logs memory status every 5 seconds and aborts on kernel pressure 4, swap growth >4 GiB, or disk <10 GiB. Pressure 2 and free percentage are observations only. `--baseline-swap-mib` can pin the dispatch-start swap baseline. It refuses occupied server ports or a pre-existing model process.

## T11 Smart Turn and call metrics

`session.start.turn_detect` accepts `vad` or `vad+smartturn`. The latter requires the existing local `smart-turn-v3.2-cpu.onnx` (at most 200 MB); missing files are explicit session errors, never a silent fallback. CPU inference uses official Whisper features, the last eight seconds with leading zero padding, and completion probability >0.5. Initial CPU feature/model loading is lazy and included in server decision latency.

Incomplete speech emits `turn.waiting {turn_num, probability, max_wait_ms}`. Up to 1.5 seconds from the first receive, a new utterance merges PCM and cancels the previous wire turn as `reason=merged`; only the latest turn number produces STT/reply. The original deadline never extends across fragments, total PCM is limited to 60 seconds, and after timeout STT proceeds. Inference/loading itself has no hard 1.5-second deadline. `barge_in.reason=speech|next_utterance` while deciding leaves the pending fragment available for merging. Explicit interruption, settings changes, and disconnect cancel it. Cancelled native inference settles under the CPU lock before another inference starts.

Optional utterance `source` is `unknown|vad|wav|wav_simulated_ack`; `measurement_phase` is `measured|warmup_discarded`. The browser sends vad/wav and the CLI marks simulated playback. Warmup exclusion is explicit. Turn logs include `smart_turn`, `merged_turns`, `cancel_reason`, and `turn_decision_ms`. First-receive server delay includes the whole merged window; client delay is measured from the latest fragment VAD end, using only the client clock.

`data/logs/sessions-YYYYMMDD.jsonl` records connection start/close revisions. Duration is readiness to disconnect, excluding cancellation cleanup after disconnect. Closed calls with actual turn records are grouped by engine; engine-switching calls are `mixed`. The call's final reconciled handoff state is authoritative. `/api/report/sessions`, `/report`, and `scripts/report_sessions.py` use only each key's latest revision. Incomplete calls and discarded warmups are excluded; old turn-only logs never supply invented call duration. Recognized user characters use Python len(text), including spaces/punctuation. New clients send boolean `barge_in.during_playback`, true only for customer interruption with active playback; settings/stop/error cancellations do not count. Older CLI records fall back to positive played_ms. Barge-in incidence counts calls with such an interruption; merged fragments are not extra turns. A simulated player's call duration does not include real speaker playback and is not a human engagement measurement.
