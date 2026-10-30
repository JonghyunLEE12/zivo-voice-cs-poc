# T6.1 text agent integration

```sh
uv run python scripts/llm_cli.py --locale en --text 'Find ZV-4821, surname Carter.' --events
uv run python scripts/test_llm_scenarios.py --variants 3 --output reports/t6_1/scenarios.json
uv run python scripts/test_llm_dialogue.py --output reports/t6_1/dialogue.json
uv run python scripts/bench_llm_review.py --repeats 3
uv run python -m pytest tests -q --basetemp reports/t6_1/pytest-temp
```

Create one `VoiceAgent(locale)` per call and consume `stream_turn(user_text)`, or
use `await agent.turn(user_text)` for the final record. Concurrent turns on one
agent are rejected. `max_history_turns=4` keeps complete user/assistant/tool
blocks; the current request reserves one slot. Verified codes, failed lookup
count and handoff status are included in the system context from `ToolSession`.
Tool messages omit `policy`, `as_of`, timezone/basis and internal lexicon IDs;
full results remain in the audit trace. This bounds turn count, not token count:
very long individual inputs still need an input limit in T9.

Events:

- `tool.detected`: name, elapsed_ms; trigger the app's cached filler.
- `tool.call`: name, args, source (`model`, `server_prefetch`, `server_enforced`).
- `tool.result`: name, result, source.
- `llm.delta`: one whole sentence of display text, converted with OpenCC for Chinese.
- `llm.done`: text, raw_text, tools, metrics, transferred, session_id, turn_num.

All tool-capable rounds are buffered until their done frame. Text in a round
with tool calls is discarded and recorded in `discarded_preambles`; a late tool
call cannot leak its preamble to TTS. This deliberately adds up to a full final
response's generation time to the first sentence. English/Japanese also deliver
sentences. Decimal points and common dotted abbreviations are preserved.
`first_token_ms` measures the first raw text token of the final spoken round;
`first_sentence_ms` measures actual first `llm.delta` availability, both relative
to turn start. `metrics.rounds` retains native Ollama nanosecond duration and
token-count fields plus round wall ms; `llm_rounds` counts model calls.

A conservative prefetch path recognizes a single explicit code and surname in
the current utterance (English last name/surname, Chinese 我姓/姓氏是, Japanese
名字は/姓は…です). It verifies through the same tool implementation; it never
infers a surname by looking at the database. Explicit cancellation questions
also prefetch policy after successful lookup. Missing/ambiguous inputs and
handoff/exception requests go through the model. `prefetch=False` disables it.

Turn history and the fake ToolSession state commit atomically after successful
completion. Errors, malformed/incomplete streams, round limits and cancellation
restore pre-turn state. Invalid JSON tool arguments produce an
`invalid_arguments` tool result so the model can recover. No real external
handoff/cancellation side effect exists in this PoC; a future real handoff must
use its own irreversible-action reconciliation.

For barge-in, cancel and await the stream consumer (or `await stream.aclose()`),
then call `agent.commit_spoken(text_played)`. Pass the exact concatenation of
`llm.delta` sentences whose playback **started**, not arbitrary text or a partial
sentence. On a completed turn this replaces the assistant reply while preserving
complete tool pairs. On a failed/cancelled turn it retains only the user and
played assistant text; tool state remains rolled back. Empty text removes the
turn and restores its fake session state. Repeated reconciliation is supported
until the next turn; call it before beginning that turn. T9 owns tracking which
sentences actually began playback and must close abandoned generators.

`DEMO_TODAY` defaults to 2026-11-01. Set `DEMO_NOW=2026-11-06T20:00:00+09:00`
for deterministic elapsed-hour policies; if both are set their Seoul dates must
match. Without DEMO_NOW, the demo date uses the current Asia/Seoul wall-clock
time, never midnight by default. Explicit datetimes require a timezone. Pickup
at/after service start returns `cancellable:false, reason:service_started`, with
no misleading fee/refund quote. Date-based cutoffs retain their original rules.

T9 should `await agent.client.warmup()` before the first turn, with resource
ownership arranged first. It loads only the installed model with the same 8192
context as chat. It does not download models. `await agent.client.unload()`
releases it once the owning batch ends; never unload another worker's model.
All chat requests use `think:false`, `stream:true`, `keep_alive:"30m"` and fixed
tool schemas. `transferred=true` means queued, not connected, and blocks future
turns. Two failed lookups still enforce server handoff; this is reported
separately from model-originated policy compliance by dialogue tests.

Records append to `data/logs/llm-YYYYMMDD.jsonl`. Length violations are logged
without truncation and now FAIL validation. Chinese compliance is checked on
`raw_text`, before OpenCC. Cancellation replies must exactly match the computed
`spoken_summary`; lookup replies need the actual name and schedule dates;
mismatch replies must not disclose names or amounts; handoff needs queue and
3-minute wait wording without a claim of an established connection. Three
prompt variants and four-language multi-turn cases expose failures; no retry is
used to turn a failed sample into PASS. Language heuristics are not a native
speaker evaluation.

Live validation resource guards follow the coordinator's final local-machine
policy: stop at `kern.memorystatus_vm_pressure_level=4`, swap growth over 4 GiB
from batch start, or disk free below 10 GiB. Pressure level 2 and memory free
percentage are logged only. Each script samples before calls and every five
seconds; it unloads its model in `finally`. Only run after obtaining the exclusive
model window from the coordinator. Earlier guard-triggered runs are retained in
the report and are not successful measurements.
