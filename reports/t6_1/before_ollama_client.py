"""Local Ollama streaming agent. No model downloads or external API calls."""
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


class VoiceAgent:
    """One instance per call/session. Consume stream_turn events for T9 integration."""
    def __init__(self, locale: str = 'en', client: OllamaClient | None = None, session: ToolSession | None = None, log_path: Path | None = None):
        self.session = session or ToolSession(locale)
        if self.session.locale != locale:
            raise ValueError('Session locale must match agent locale')
        self.locale = locale
        self.client = client or OllamaClient()
        language_name,locale_rules,max_units,register = LOCALE_CONFIG[locale]
        env = Environment(loader=FileSystemLoader(Path(__file__).with_name('prompts')),undefined=StrictUndefined,autoescape=False)
        prompt = env.get_template('system.md.j2').render(demo_today=self.session.db.today.isoformat(),language_name=language_name,locale_rules=locale_rules,max_units=max_units,register=register)
        self.messages = [{'role':'system','content':prompt}]
        self.log_path = log_path or Path(__file__).resolve().parents[2]/'data/logs'/f'llm-{datetime.now():%Y%m%d}.jsonl'
        self.session_id = str(uuid4())
        self.turn_num = 0

    async def stream_turn(self, text: str):
        if self.session.transferred:
            raise RuntimeError('Human handoff already queued; stop the microphone loop')
        self.turn_num += 1
        self.messages.append({'role':'user','content':text})
        started = perf_counter()
        first_token_ms = None
        transcript_raw, transcript = [], []
        trace = []
        raw_chunks = 0
        thinking_chars = 0
        for round_index in range(6):
            content,pending = '', ''
            calls = {}
            async for chunk in self.client.stream_chat(self.messages, [] if self.session.transferred else TOOLS):
                raw_chunks += 1
                message = chunk.get('message',{})
                thinking_chars += len(message.get('thinking',''))
                delta = message.get('content','')
                if delta:
                    if first_token_ms is None:
                        first_token_ms = (perf_counter()-started)*1000
                    content += delta
                    if self.locale in CONVERTERS:
                        pending += delta
                        while match := re.search(r'[。！？!?\n]',pending):
                            fragment,pending = pending[:match.end()],pending[match.end():]
                            normalized = convert_script(fragment,self.locale)
                            transcript.append(normalized)
                            yield {'type':'llm.delta','text':normalized}
                    else:
                        transcript.append(delta)
                        yield {'type':'llm.delta','text':delta}
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
            if pending:
                normalized = convert_script(pending,self.locale)
                transcript.append(normalized)
                yield {'type':'llm.delta','text':normalized}
            transcript_raw.append(content)
            assistant = {'role':'assistant','content':convert_script(content,self.locale)}
            if calls:
                call_list = list(calls.values())
                for call in call_list:
                    function = call['function']
                    if isinstance(function['arguments'],str):
                        function['arguments'] = json.loads(function['arguments'])
                assistant['tool_calls'] = call_list
                self.messages.append(assistant)
                for call in call_list:
                    function = call['function']
                    name,args = function['name'],function['arguments']
                    yield {'type':'tool.call','name':name,'args':args}
                    result = self.session.execute(name,args)
                    trace.append({'name':name,'args':args,'result':result})
                    self.messages.append({'role':'tool','tool_name':name,'content':json.dumps(result,ensure_ascii=False)})
                    yield {'type':'tool.result','name':name,'result':result}
                if self.session.lookup_failures >= 2 and not self.session.transferred:
                    args = {'reason':'lookup_failed','summary_ko':'예약 코드와 성 확인에 두 번 실패하여 상담원 연결이 필요합니다.'}
                    name = 'transfer_to_human'
                    self.messages.append({'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}}]})
                    yield {'type':'tool.call','name':name,'args':args}
                    result = self.session.execute(name,args)
                    self.messages.append({'role':'tool','tool_name':name,'content':json.dumps(result,ensure_ascii=False)})
                    trace.append({'name':name,'args':args,'result':result,'server_enforced':True})
                    yield {'type':'tool.result','name':name,'result':result}
                continue
            self.messages.append(assistant)
            break
        else:
            raise RuntimeError('Tool round limit reached')
        raw,reply = ''.join(transcript_raw),''.join(transcript)
        if not reply.strip():
            raise RuntimeError('Ollama returned an empty spoken reply')
        metrics = {**response_metrics(raw,reply,self.locale),'first_token_ms':first_token_ms,
                   'elapsed_ms':(perf_counter()-started)*1000,'stream_chunks':raw_chunks,'thinking_chars':thinking_chars}
        record = {'session_id':self.session_id,'turn_num':self.turn_num,'locale':self.locale,
                  'model':self.client.model,'think':False,'raw_text':raw,'text':reply,'tools':trace,'metrics':metrics,
                  'transferred':self.session.transferred}
        self.log_path.parent.mkdir(parents=True,exist_ok=True)
        with self.log_path.open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False)+'\n')
        if metrics['length_violation']:
            LOGGER.warning('Response length violation: %s',json.dumps(metrics))
        yield {'type':'llm.done',**record}

    async def turn(self, text: str) -> dict:
        result = None
        async for event in self.stream_turn(text):
            if event['type']=='llm.done': result=event
        return result
