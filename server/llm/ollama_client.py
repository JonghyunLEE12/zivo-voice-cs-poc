"""Local Ollama streaming agent. No model downloads or external API calls."""
from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from time import perf_counter
import json
import logging
import re
from uuid import uuid4

import httpx
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from opencc import OpenCC
from .tools import ToolSession, TOOLS
from .prefetch import prefetch_calls
from .sentences import split_sentences

LOGGER = logging.getLogger(__name__)
LOCALE_CONFIG = {
    'en':('English','Use US English.','35 words','friendly, polite'),
    'zh-CN':('Simplified Chinese','使用简体中文和中国大陆用语（出租车、地铁、酒店、预订）。','60 Chinese characters','礼貌口语，称呼您'),
    'zh-TW':('Traditional Chinese (Taiwan)','只使用繁體中文和台灣用語（計程車、捷運、飯店、訂房）。不要使用簡體字。','60 Chinese characters','禮貌口語，稱呼您'),
    'ja':('Japanese','自然な話し言葉の日本語で、丁寧語（です・ます）を使う。','80 characters','丁寧、カスタマーサポート'),
    'yue':('Cantonese (written Hong Kong style)','用繁體字書面粵語口語。','60 characters','有禮貌'),
}
CONVERTERS = {'zh-CN':OpenCC('t2s'),'zh-TW':OpenCC('s2twp')}


def convert_script(text: str, locale: str) -> str:
    converter = CONVERTERS.get(locale)
    return converter.convert(text) if converter else text


def response_metrics(raw: str, text: str, locale: str) -> dict:
    changes = sum(max(i2-i1,j2-j1) for tag,i1,i2,j1,j2 in SequenceMatcher(a=raw,b=text).get_opcodes() if tag!='equal')
    if locale == 'en':
        units,limit = len(re.findall(r"\b[\w]+(?:['’-][\w]+)*\b",text)),35
    elif locale in ('zh-CN','zh-TW'):
        units,limit = len(re.findall(r'[\u3400-\u9fff]',text)),60
    else:
        units,limit = len(re.sub(r'\s','',text)),80 if locale=='ja' else 60
    sentences = len([s for s in re.split(r'[.!?。！？]+(?:\s+|$)|[。！？]',text) if s.strip()])
    return {'units':units,'unit_limit':limit,'length_violation':units>limit,
            'sentence_count':sentences,'sentence_violation':not 1<=sentences<=2,
            'script_changed_chars':changes}


class OllamaClient:
    def __init__(self, base_url: str = 'http://localhost:11434', model: str = 'qwen3.5:35b-a3b', transport=None):
        from urllib.parse import urlparse
        url = urlparse(base_url)
        if url.scheme != 'http' or url.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('This PoC only uses a local HTTP Ollama server')
        self.base_url,self.model,self.transport = base_url.rstrip('/'),model,transport

    async def stream_chat(self, messages: list[dict], tools: list[dict]):
        payload = {'model':self.model,'messages':messages,'tools':tools,'stream':True,
                   'think':False,'keep_alive':'30m','options':{'temperature':0,'presence_penalty':0,'repeat_penalty':1.0,'num_ctx':8192,'num_predict':512,'seed':42}}
        # The response context closes the socket promptly on asyncio cancellation.
        async with httpx.AsyncClient(timeout=httpx.Timeout(180,connect=10),transport=self.transport,trust_env=False) as client:
            async with client.stream('POST',self.base_url+'/api/chat',json=payload) as response:
                response.raise_for_status()
                done = False
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if 'error' in chunk:
                        raise RuntimeError(f'Ollama: {chunk["error"]}')
                    yield chunk
                    if chunk.get('done'):
                        if chunk.get('done_reason') == 'length':
                            raise RuntimeError('Ollama token limit reached; response is incomplete')
                        done = True
                if not done:
                    raise RuntimeError('Ollama stream ended without a done frame')


    async def warmup(self) -> dict:
        """Load the existing model only; caller owns resource checks and unload."""
        async with httpx.AsyncClient(timeout=180, transport=self.transport, trust_env=False) as client:
            response = await client.post(self.base_url+'/api/chat', json={
                'model':self.model, 'messages':[], 'stream':False,
                'think':False, 'keep_alive':'30m', 'options':{'num_ctx':8192}})
            response.raise_for_status()
            result = response.json()
            if result.get('error'):
                raise RuntimeError(result['error'])
            return result

    async def unload(self):
        async with httpx.AsyncClient(timeout=60, transport=self.transport, trust_env=False) as client:
            response = await client.post(self.base_url+'/api/chat', json={
                'model':self.model, 'messages':[], 'stream':False, 'keep_alive':0})
            response.raise_for_status()


class VoiceAgent:
    """One sequential session. Cancel/close stream before commit_spoken on barge-in."""
    def __init__(self, locale: str = 'en', client: OllamaClient | None = None,
                 session: ToolSession | None = None, log_path: Path | None = None,
                 max_history_turns: int = 4, prefetch: bool = True):
        if max_history_turns < 1:
            raise ValueError('max_history_turns must be positive')
        self.session = session or ToolSession(locale)
        if self.session.locale != locale:
            raise ValueError('Session locale must match agent locale')
        self.locale = locale
        self.client = client or OllamaClient()
        language_name,locale_rules,max_units,register = LOCALE_CONFIG[locale]
        env = Environment(loader=FileSystemLoader(Path(__file__).with_name('prompts')),undefined=StrictUndefined,autoescape=False)
        prompt = env.get_template('system.md.j2').render(demo_today=self.session.db.today.isoformat(),language_name=language_name,locale_rules=locale_rules,max_units=max_units,register=register)
        self._system = {'role':'system','content':prompt}
        self.messages = [deepcopy(self._system)]
        self._turns = []
        self._active = False
        self._playback = None
        self.max_history_turns = max_history_turns
        self.prefetch = prefetch
        self.log_path = log_path or Path(__file__).resolve().parents[2]/'data/logs'/f'llm-{datetime.now():%Y%m%d}.jsonl'
        self.session_id = str(uuid4())
        self.turn_num = 0

    def _publish(self, turns):
        self._turns = deepcopy(turns[-self.max_history_turns:])
        self.messages = [deepcopy(self._system)] + [m for turn in self._turns for m in turn]

    def _context(self, work):
        system = deepcopy(self._system)
        system['content'] += '\nSESSION STATE: verified codes: ' + ', '.join(sorted(self.session.verified_codes))
        system['content'] += f'; lookup failures: {self.session.lookup_failures}; handoff queued: {self.session.transferred}.'
        # Reserve one turn for this request, never split assistant/tool pairs.
        recent = self._turns[-(self.max_history_turns-1):] if self.max_history_turns > 1 else []
        return [system] + [deepcopy(m) for turn in recent for m in turn] + deepcopy(work)

    @staticmethod
    def _tool_message(name, result):
        compact = {k:v for k,v in result.items() if k not in {'policy','as_of','timezone','basis','lexicon_id','last_name_aliases'}}
        return {'role':'tool','tool_name':name,'content':json.dumps(compact,ensure_ascii=False)}

    def commit_spoken(self, text_played: str):
        """Replace latest turn with emitted whole sentences whose playback started.

        Await cancellation/aclose first. Pass the exact concatenation of llm.delta
        sentences played; empty rolls back the turn. No arbitrary text is accepted.
        """
        if self._active:
            raise RuntimeError('Cancel and await/close stream_turn before commit_spoken')
        if self._playback is None:
            raise RuntimeError('No turn available for playback reconciliation')
        state = self._playback
        prefixes = ['']
        for sentence in state['emitted']:
            prefixes.append(prefixes[-1]+sentence)
        if text_played not in prefixes:
            raise ValueError('text_played must be an emitted whole-sentence prefix')
        if text_played:
            # Failed/cancelled tool actions were rolled back; retain only the audible
            # exchange. Successful turns keep complete tool pairs and session state.
            block = deepcopy(state['work'][:-1]) if state['success'] else [state['user']]
            block.append({'role':'assistant','content':text_played})
            self._publish(state['before'] + [block])
        else:
            self._publish(state['before'])
            self._restore_session(state['session_before'])

    def _session_state(self):
        return (set(self.session.verified_codes), self.session.lookup_failures, self.session.transferred)

    def _restore_session(self, state):
        self.session.verified_codes, self.session.lookup_failures, self.session.transferred = deepcopy(state)

    async def stream_turn(self, text: str):
        if self._active:
            raise RuntimeError('A turn is already running')
        if self.session.transferred:
            raise RuntimeError('Human handoff already queued; stop the microphone loop')
        self._active = True
        self.turn_num += 1
        user = {'role':'user','content':text}
        work = [user]
        state = {'before':deepcopy(self._turns), 'session_before':self._session_state(),
                 'user':user, 'work':work, 'emitted':[], 'success':False}
        self._playback = state
        started = perf_counter()
        first_token_ms = first_sentence_ms = None
        trace, rounds, preambles = [], [], []
        raw_chunks = thinking_chars = 0
        raw = reply = ''

        def execute(name, args, source='model', invalid=False):
            call = {'function':{'name':name,'arguments':args}}
            work.append({'role':'assistant','content':'','tool_calls':[call]})
            result = {'error':'invalid_arguments'} if invalid else self.session.execute(name,args)
            work.append(self._tool_message(name,result))
            entry = {'name':name,'args':args,'result':result,'source':source}
            if source == 'server_enforced': entry['server_enforced'] = True
            trace.append(entry)
            return result

        async def tool_events(name, args, source='model', invalid=False):
            yield {'type':'tool.call','name':name,'args':args,'source':source}
            result = execute(name,args,source,invalid)
            yield {'type':'tool.result','name':name,'result':result,'source':source}

        async def enforce_handoff():
            if self.session.lookup_failures >= 2 and not self.session.transferred:
                args = {'reason':'lookup_failed','summary_ko':'예약 코드와 성 확인에 두 번 실패하여 상담원 연결이 필요합니다.'}
                async for event in tool_events('transfer_to_human',args,'server_enforced'):
                    yield event

        try:
            if self.prefetch:
                for name,args in prefetch_calls(text,self.locale):
                    if name == 'get_cancellation_policy' and args['reservation_code'] not in self.session.verified_codes:
                        break
                    yield {'type':'tool.detected','name':name,'elapsed_ms':(perf_counter()-started)*1000,'source':'server_prefetch'}
                    async for event in tool_events(name,args,'server_prefetch'):
                        yield event
                async for event in enforce_handoff():
                    yield event
            for round_index in range(6):
                content, calls = '', {}
                round_started = perf_counter()
                round_first = None
                done_metrics = {}
                # Hold all tool-capable round text until done: even a late tool call
                # must never leak a promise/unsupported fact into the TTS queue.
                async for chunk in self.client.stream_chat(self._context(work), TOOLS):
                    raw_chunks += 1
                    message = chunk.get('message',{})
                    thinking_chars += len(message.get('thinking',''))
                    delta = message.get('content','')
                    if delta:
                        if round_first is None: round_first = (perf_counter()-started)*1000
                        content += delta
                    if chunk.get('done'):
                        done_metrics = {k:chunk[k] for k in ('total_duration','load_duration','prompt_eval_count','prompt_eval_duration','eval_count','eval_duration','done_reason') if k in chunk}
                    for position,call in enumerate(message.get('tool_calls',[])):
                        function = call.get('function',{})
                        index = function.get('index',call.get('index',position))
                        if index not in calls:
                            calls[index] = {'function':{'name':'','arguments':{}}}
                            yield {'type':'tool.detected','name':function.get('name',''),'elapsed_ms':(perf_counter()-started)*1000}
                        target = calls[index]['function']
                        if function.get('name'):
                            name = function['name']
                            target['name'] = name if not target['name'] or target['name']==name else target['name']+name
                        args = function.get('arguments',{})
                        if isinstance(args,str):
                            if not isinstance(target['arguments'],str): target['arguments']=''
                            target['arguments'] += args
                        elif isinstance(args,dict):
                            if not isinstance(target['arguments'],dict): raise RuntimeError('Mixed tool argument stream format')
                            target['arguments'].update(args)
                        else:
                            raise RuntimeError('Invalid Ollama tool arguments')
                rounds.append({'round':round_index+1,'elapsed_ms':(perf_counter()-round_started)*1000,
                               'first_token_ms':round_first,'tool_calls':len(calls),**done_metrics})
                if calls:
                    if content: preambles.append(content)
                    for call in calls.values():
                        function = call['function']
                        args, invalid = function['arguments'], False
                        if isinstance(args,str):
                            try: args = json.loads(args)
                            except (ValueError, TypeError): args, invalid = {}, True
                        async for event in tool_events(function['name'],args,invalid=invalid):
                            yield event
                    async for event in enforce_handoff():
                        yield event
                    continue
                raw = content
                first_token_ms = round_first
                for fragment in split_sentences(content):
                    normalized = convert_script(fragment,self.locale)
                    state['emitted'].append(normalized)
                    reply += normalized
                    if first_sentence_ms is None: first_sentence_ms = (perf_counter()-started)*1000
                    yield {'type':'llm.delta','text':normalized}
                work.append({'role':'assistant','content':reply})
                break
            else:
                raise RuntimeError('Tool round limit reached')
            if not reply.strip():
                raise RuntimeError('Ollama returned an empty spoken reply')
            metrics = {**response_metrics(raw,reply,self.locale),'first_token_ms':first_token_ms,
                       'first_sentence_ms':first_sentence_ms,'rounds':rounds,'llm_rounds':len(rounds),
                       'elapsed_ms':(perf_counter()-started)*1000,'stream_chunks':raw_chunks,'thinking_chars':thinking_chars}
            record = {'session_id':self.session_id,'turn_num':self.turn_num,'locale':self.locale,
                      'model':self.client.model,'think':False,'raw_text':raw,'text':reply,'tools':trace,'metrics':metrics,
                      'discarded_preambles':preambles,'transferred':self.session.transferred}
            self.log_path.parent.mkdir(parents=True,exist_ok=True)
            with self.log_path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(record,ensure_ascii=False)+'\n')
            self._publish(state['before'] + [work])
            state['success'] = True
            if metrics['length_violation']:
                LOGGER.warning('Response length violation: %s',json.dumps(metrics))
            yield {'type':'llm.done',**record}
        finally:
            if not state['success']:
                self._restore_session(state['session_before'])
            self._active = False

    async def turn(self, text: str) -> dict:
        result = None
        async for event in self.stream_turn(text):
            if event['type']=='llm.done': result=event
        return result
