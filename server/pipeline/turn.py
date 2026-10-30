"""One call's cancellable STT -> transactional LLM -> bounded TTS pipeline."""
import asyncio
from contextlib import aclosing
from dataclasses import dataclass, field
import struct
from uuid import uuid4

from server.config import LOCALES, ENGINE_URLS, OLLAMA_URL, OLLAMA_MODEL
from server.llm.ollama_client import VoiceAgent, OllamaClient
from server.normalize.pipeline import variants
from server.tts.client import TTSClient, FillerCache, FILLERS
from .latency import TurnLog, now_ms
from .sentence_splitter import SentenceSplitter
from .sessions import append_session

HEADER = struct.Struct('<BBHI')
MAX_SAMPLES = 16000 * 60


@dataclass(frozen=True)
class Settings:
    locale: str = 'en'
    tts_engine: str = 'kokoro'
    normalize: str = 'full'
    stt: str = 'whisper'
    turn_detect: str = 'vad'

    @classmethod
    def parse(cls, message):
        fields = {key: value for key, value in message.items() if key != 'type'}
        settings = cls(**fields)
        if settings.locale not in LOCALES or settings.tts_engine not in ENGINE_URLS:
            raise ValueError('Unsupported locale or engine')
        if settings.normalize not in ('raw', 'lex', 'full'):
            raise ValueError('Unsupported normalization')
        if settings.stt != 'whisper' or settings.turn_detect not in ('vad', 'vad+smartturn'):
            raise ValueError('Unsupported STT or turn detector')
        if settings.locale == 'yue' and settings.tts_engine not in ('voxcpm2', 'cosyvoice3'):
            raise ValueError('Selected engine does not support yue')
        return settings


class Runtime:
    """Dependency injection is explicit; production never imports test doubles."""
    def __init__(self, *, stt=None, tts=None, agent_factory=None, log_path=None, smart_turn=None):
        self.stt = stt
        self.tts = tts or TTSClient()
        self.cache = FillerCache()
        self.agent_factory = agent_factory or (lambda locale: VoiceAgent(
            locale, client=OllamaClient(OLLAMA_URL, OLLAMA_MODEL)))
        self.log_path = log_path
        self.smart_turn = smart_turn

    async def transcribe(self, pcm, locale):
        if self.stt is None:
            from server.stt.mlx_whisper_stt import MLXWhisperSTT
            self.stt = MLXWhisperSTT()
        return await self.stt.transcribe(pcm, locale=locale)

    async def prepare(self, settings):
        if settings.turn_detect == 'vad+smartturn' and self.smart_turn is None:
            from server.turn_detect.smart_turn import SmartTurn
            self.smart_turn = SmartTurn()
        info = await self.tts.info(settings.tts_engine)
        if settings.locale not in info['locales']:
            raise ValueError('Engine does not advertise the selected locale')
        return await self.cache.prepare(self.tts, settings.tts_engine, settings.locale)


@dataclass
class Turn:
    call: 'Call'
    number: int
    pcm: bytes
    utterance: dict
    received_ms: float
    cancelled: bool = False
    finalized: bool = False
    task: asyncio.Task | None = None
    sentences: list = field(default_factory=list)
    chunk_sentences: dict = field(default_factory=dict)
    started: set = field(default_factory=set)
    chunk_seq: int = 0
    llm_started: bool = False
    status: str = 'running'

    def __post_init__(self):
        self.settings = self.call.settings
        self.agent = self.call.agent
        self.queue = asyncio.Queue(maxsize=2)
        self.pending = asyncio.Queue()  # text/events only; never backpressure agent commit
        self.skipped = set()
        self.log = TurnLog(self.call.session_id, self.number, vars(self.settings), self.call.runtime.log_path)
        self.log.mark('s_rx', self.received_ms)
        self.deciding = self.settings.turn_detect == 'vad+smartturn'
        self.decision_deadline = self.utterance.get('_decision_deadline', self.received_ms + 1500)
        if not self.deciding:
            self.log.mark('s_turn_decided', self.received_ms)
        self.log.mark('c_vad_end', self.utterance['t_vad_end'])
        if 'c_speech_end_est' in self.utterance:
            self.log.mark('c_speech_end_est', self.utterance['c_speech_end_est'])
        self.log.details.update(source=self.utterance.get('source', 'unknown'),
                                measurement_phase=self.utterance.get('measurement_phase', 'measured'),
                                merged_turns=self.utterance.get('_merged_turns', []), sentences=self.sentences, tools=[], audio_bytes=0,
                                input_samples=len(self.pcm)//2, queue_capacity=2,
                                c_play_start_meaning='first_audio_including_filler',
                                llm_limitations='T6.1 human-transfer/multi-turn tool omissions are not corrected')

    async def event(self, kind, **fields):
        await self.call.send({'type': kind, 'turn_num': self.number, **fields}, turn=self)

    async def produce(self, text):
        started_ms = now_ms()
        filler_sent = False
        self.llm_started = True
        async with aclosing(self.agent.stream_turn(text)) as stream:
            async for event in stream:
                kind = event['type']
                if kind in ('tool.detected', 'tool.call'):
                    self.log.mark('s_tool_call')
                    if not filler_sent:
                        self.pending.put_nowait(('filler', FILLERS[self.settings.locale], None))
                        filler_sent = True
                if kind == 'tool.result':
                    self.log.mark('s_tool_done')
                if kind in ('tool.call', 'tool.result'):
                    self.log.details['tools'].append(dict(event))
                    self.pending.put_nowait(('event', dict(event), None))
                elif kind == 'llm.delta':
                    self.pending.put_nowait(('event', {'type': kind, 'text': event['text']}, None))
                    # T6.1 emits complete sentences; preserve their EXACT strings for
                    # commit_spoken (including whitespace). Do not split at commas.
                    parts = SentenceSplitter.complete(event['text'])
                    if len(parts) != 1 or parts[0] != event['text']:
                        raise RuntimeError('LLM violated the complete sentence contract')
                    self.log.mark('s_first_sentence')
                    index = len(self.sentences)
                    sentence = {**variants(event['text'], self.settings.locale), 'index': index}
                    self.sentences.append(sentence)
                    self.pending.put_nowait(('reply', event['text'], index))
                elif kind == 'llm.done':
                    metrics = event.get('metrics', {})
                    # The T6 adapter buffers tool-capable rounds. Reconstruct actual
                    # first raw token time from its clock-relative metrics, never
                    # relabel the later delivered sentence as a token arrival.
                    first = [r['first_token_ms'] for r in metrics.get('rounds', []) if r.get('first_token_ms') is not None]
                    token_ms = min(first) if first else metrics.get('first_token_ms')
                    if token_ms is not None:
                        self.log.mark('s_llm_first_token', started_ms + token_ms)
                    self.log.details['llm'] = event
                    self.log.details['handoff_queued'] = event.get('transferred', False)
        self.pending.put_nowait(None)

    async def dispatch(self):
        # Buffer text, not synthesized PCM. Draining T6's already-complete final
        # round through llm.done must not depend on TTS speed or socket writes.
        while (item := await self.pending.get()) is not None:
            role, value, index = item
            if role == 'event':
                await self.event(value['type'], **{k: v for k, v in value.items() if k != 'type'})
            else:
                await self.queue.put(item)
                self.log.details['max_queue_depth'] = max(self.queue.qsize(), self.log.details.get('max_queue_depth', 0))
        await self.queue.put(None)

    async def consume(self):
        sentence_seq = 0
        while (item := await self.queue.get()) is not None:
            role, text, index = item
            normalized = variants(text, self.settings.locale)
            tts_text = normalized['tts_text_' + self.settings.normalize]
            if role == 'filler':
                # Filler is fixed and cached using full normalization.
                tts_text = normalized['tts_text_full']
            if not any(char.isalnum() for char in tts_text):
                if index is not None:
                    self.skipped.add(index)
                    self.sentences[index]['audio_skipped'] = 'no_speakable_text'
                continue
            await self.event('tts.sentence', seq=sentence_seq, display_text=text,
                             tts_text=tts_text, engine=self.settings.tts_engine,
                             sample_rate=24000, role=role, first_chunk_seq=self.chunk_seq)
            sentence_seq += 1
            if role == 'filler':
                pcm = self.call.filler
                for offset in range(0, len(pcm), 48000):
                    await self.audio(pcm[offset:offset+48000], index, role)
            else:
                async with aclosing(self.call.runtime.tts.synthesize(
                        self.settings.tts_engine, tts_text, self.settings.locale)) as stream:
                    async for chunk in stream:
                        if not chunk or len(chunk) % 2:
                            raise RuntimeError('Invalid PCM from TTS adapter')
                        self.log.mark('s_tts_first_audio')
                        await self.audio(chunk, index, role)

    async def audio(self, pcm, index, role):
        if self.chunk_seq > 65535:
            raise RuntimeError('Turn exceeded binary chunk sequence range')
        self.chunk_sentences[self.chunk_seq] = (index, role)
        await self.call.send(HEADER.pack(2, 0, self.chunk_seq, self.number) + pcm, turn=self)
        self.chunk_seq += 1
        self.log.details['audio_bytes'] += len(pcm)

    async def run(self):
        tasks = []
        try:
            if self.deciding:
                decision = await self.call.runtime.smart_turn.decide(self.pcm)
                self.log.details['smart_turn'] = decision
                if not decision['complete']:
                    if self.decision_deadline is None:
                        self.decision_deadline = now_ms() + 1500
                    await self.event('turn.waiting', probability=decision['probability'], max_wait_ms=1500)
                    await asyncio.sleep(max(0, self.decision_deadline-now_ms())/1000)
                    decision['release'] = 'timeout'
                else:
                    decision['release'] = 'complete'
                self.deciding = False
                self.log.mark('s_turn_decided')
            result = await self.call.runtime.transcribe(self.pcm, self.settings.locale)
            self.log.mark('s_stt_done')
            self.log.details['stt_result'] = result.to_dict()
            self.log.details['user_chars'] = len(result.text)
            await self.event('stt.result', text=result.text, text_raw=result.text_raw,
                             lang_detected=result.lang_detected, lang_prob=result.lang_prob)
            if not result.text.strip() or len(result.text) > 4000:
                raise ValueError('STT text is empty or exceeds 4000 characters')
            tasks = [asyncio.create_task(self.produce(result.text)),
                     asyncio.create_task(self.dispatch()), asyncio.create_task(self.consume())]
            await asyncio.gather(*tasks)
            self.status = 'done'
            self.log.write(self.status)
            await self.event('turn.done', marks=self.log.marks,
                             handoff_queued=self.log.details.get('handoff_queued', False))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = 'error'
            self.log.details['error'] = f'{type(exc).__name__}: {exc}'
            await self.event('error', code='turn_failed', message=str(exc))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self.status == 'error':
                self.reconcile()
                self.log.write(self.status)

    def reconcile(self):
        if self.finalized:
            return
        prefix = ''
        for index, sentence in enumerate(self.sentences):
            if not self.started or (index not in self.started and index not in self.skipped):
                break
            prefix += sentence['display_text']
        if self.llm_started:
            self.agent.commit_spoken(prefix)
        self.log.details['spoken_text'] = prefix
        self.log.details['played_sentence_indices'] = sorted(self.started)
        self.finalized = True

    async def cancel(self, reason='barge_in', notify=True):
        if self.finalized:
            return
        self.log.details['cancel_reason'] = reason
        first_cancel = not self.cancelled
        self.cancelled = True
        self.log.mark('s_cancel_received')
        if self.task and not self.task.done():
            self.task.cancel()
        # Acknowledge cancellation independently of model/HTTP cleanup; the flag
        # under the send lock prevents stale audio or done after this response.
        try:
            if notify and first_cancel:
                await self.call.send({'type': 'turn.cancelled', 'turn_num': self.number, 'reason': reason})
                self.log.mark('s_cancel_ack')
        finally:
            # Disconnect may race the acknowledgment write. Cleanup/history must
            # still settle even when the socket cannot deliver the acknowledgment.
            if self.task:
                await asyncio.gather(self.task, return_exceptions=True)
            self.reconcile()
            self.status = 'cancelled'
            self.log.write(self.status)

    def playback_finished(self, client_ms):
        if self.finalized or self.cancelled:
            return
        if self.task and not self.task.done():
            raise ValueError('playback.finished requires turn.done first')
        # Unlike a start ACK, finished asserts the entire sent playback queue
        # drained. This supports clients that ACK only the first audio chunk.
        self.started.update(index for index, role in self.chunk_sentences.values() if role == 'reply')
        self.log.mark('c_play_finished', client_ms)
        self.reconcile()
        self.log.write(self.status)

    def playback_started(self, chunk_seq, client_ms):
        if self.cancelled or self.finalized or chunk_seq not in self.chunk_sentences:
            return
        index, role = self.chunk_sentences[chunk_seq]
        self.log.mark('c_play_start', client_ms)
        if role == 'filler':
            self.log.mark('t_filler_play', client_ms)  # client clock, never server math
        else:
            # Each sentence needs its own playback acknowledgment. Never invent
            # playback for an earlier sentence from a later chunk's arrival.
            self.started.add(index)
            self.log.mark('c_reply_play_start', client_ms)
        self.log.write(self.status)


class Call:
    def __init__(self, socket, runtime):
        self.socket, self.runtime = socket, runtime
        self.session_id = str(uuid4())
        self.settings = None
        self.agent = None
        self.turn = None
        self.filler = None
        self.lock = asyncio.Lock()
        self.last_turn = -1
        self.started_ms = None
        self.engines = set()
        self.session_revision = 0

    async def send(self, payload, turn=None):
        async with self.lock:
            if turn is not None and turn.cancelled:
                return
            if isinstance(payload, bytes):
                await self.socket.send_bytes(payload)
            else:
                await self.socket.send_json(payload)

    async def configure(self, settings):
        if self.turn and not self.turn.finalized:
            await self.turn.cancel('session_changed')
        self.filler = await self.runtime.prepare(settings)
        if self.agent is None or settings.locale != self.settings.locale:
            self.agent = self.runtime.agent_factory(settings.locale)
        self.settings = settings
        if self.started_ms is None:
            self.started_ms = now_ms()
        self.engines.add(settings.tts_engine)
        append_session(self)
        await self.send({'type': 'session.ready', 'session_id': self.session_id, **vars(settings)})

    async def start(self, pcm, metadata, received_ms):
        metadata = dict(metadata)
        if self.turn and self.turn.deciding and not self.turn.cancelled:
            previous = self.turn
            if len(previous.pcm) + len(pcm) > MAX_SAMPLES*2:
                raise ValueError('Merged utterance exceeds 60 seconds')
            pcm = previous.pcm + pcm
            metadata['_decision_deadline'] = previous.decision_deadline
            metadata['_merged_turns'] = previous.utterance.get('_merged_turns', []) + [previous.number]
            received_ms = previous.received_ms
            await previous.cancel('merged')
        if self.turn and not self.turn.finalized:
            if self.turn.task and not self.turn.task.done():
                await self.turn.cancel('superseded')
            else:
                self.turn.reconcile()
                self.turn.log.write(self.turn.status)
        if getattr(self.agent.session, 'transferred', False):
            raise ValueError('Human handoff queued; stop the microphone loop')
        self.turn = Turn(self, metadata['turn_num'], pcm, metadata, received_ms)
        self.last_turn = self.turn.number
        self.turn.task = asyncio.create_task(self.turn.run())

    async def close(self):
        ended_ms = now_ms()
        if self.turn and not self.turn.finalized:
            await self.turn.cancel('disconnect', notify=False)
        append_session(self, closed=True, ended_ms=ended_ms)
