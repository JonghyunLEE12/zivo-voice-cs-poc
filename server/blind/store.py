"""Server-only manifest, random bearer tokens, balanced assignment and durable votes."""
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import secrets
import threading
import time

from server.config import ROOT


class BlindStore:
    def __init__(self, root=ROOT / 'data/blind', clock=time.monotonic):
        self.root = Path(root)
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.assignments = {}
        self.audio_tokens = {}
        self.labels = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self.reload()

    def reload(self):
        path = self.root / 'manifest.json'
        self.items = json.loads(path.read_text()).get('items',[]) if path.exists() else []
        engines = sorted(set(i['engine'] for i in self.items))
        mapping_path = self.root / 'private-labels.json'
        self.labels = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
        remaining = [e for e in engines if e not in self.labels]
        secrets.SystemRandom().shuffle(remaining)
        for engine in remaining:
            self.labels[engine] = f'Voice {len(self.labels)+1}'
        mapping_path.write_text(json.dumps(self.labels))

    def votes(self):
        with self.lock:
            votes = []
            for path in sorted((self.root/'results').glob('*.jsonl')):
                votes.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
            return votes

    def session(self, alias, native, device):
        token = secrets.token_urlsafe(32)
        # Alias is never a path and remains stable across browser sessions.
        rater_id = hashlib.sha256(alias.casefold().strip().encode()).hexdigest()[:32]
        self.sessions[token] = {'rater':alias.strip(),'rater_id':rater_id,'rater_native':native,'device':device}
        return token

    def _session(self, token):
        if token not in self.sessions:
            raise ValueError('Start a listening session first')
        return self.sessions[token]

    @staticmethod
    def pair_key(a,b):
        return '|'.join([a['sentence_id'], *sorted([a['item_id'],b['item_id']])])

    def next(self, token, locale):
        with self.lock:
            session = self._session(token)
            # Preserve pending assignment when refreshed; no hidden alternate pairs.
            for assignment in self.assignments.values():
                if assignment['session']==token and assignment['locale']==locale and not assignment['voted']:
                    return self.public(assignment)
            votes = self.votes()
            seen = {v['pair_key'] for v in votes if v['rater_id']==session['rater_id']}
            counts = {}
            for vote in votes:
                counts[vote['pair_key']] = counts.get(vote['pair_key'],0)+1
            grouped = {}
            for item in self.items:
                if item.get('status')=='ok' and item['locale']==locale and item['normalize']=='full':
                    grouped.setdefault(item['sentence_id'],[]).append(item)
            candidates = [(a,b) for items in grouped.values() for a,b in itertools.combinations(items,2)
                          if a['engine']!=b['engine'] and self.pair_key(a,b) not in seen]
            if not candidates:
                return None
            minimum = min(counts.get(self.pair_key(a,b),0) for a,b in candidates)
            a,b = secrets.choice([(a,b) for a,b in candidates if counts.get(self.pair_key(a,b),0)==minimum])
            if secrets.randbelow(2):
                a,b = b,a
            assignment_id = secrets.token_urlsafe(24)
            assignment = {'id':assignment_id,'session':token,'locale':locale,'a':a,'b':b,'voted':False,
                          'pair_key':self.pair_key(a,b),'tokens':{},'audio_started':{}}
            for side in ('a','b'):
                audio_token = secrets.token_urlsafe(32)
                assignment['tokens'][side] = audio_token
                self.audio_tokens[audio_token] = (assignment_id,side)
            self.assignments[assignment_id] = assignment
            return self.public(assignment)

    @staticmethod
    def public(assignment):
        return {'id':assignment['id'],'locale':assignment['locale'],'text':assignment['a']['display_text'],
                **{side:{'url':'/api/blind/audio/'+assignment['tokens'][side],
                         'duration_ms':round(assignment[side]['duration_s']*1000)} for side in ('a','b')}}

    def audio(self, token, audio_token):
        with self.lock:
            self._session(token)
            pair = self.audio_tokens.get(audio_token)
            if pair is None:
                raise ValueError('Audio not found')
            assignment_id,side = pair
            assignment = self.assignments[assignment_id]
            if assignment['session']!=token or assignment['voted']:
                raise ValueError('Audio not found')
            item = assignment[side]
            path = Path(item['file'])
            path = (ROOT/path).resolve() if not path.is_absolute() else path.resolve()
            allowed = (self.root/'audio').resolve()
            if not path.is_relative_to(allowed) or not path.is_file():
                raise ValueError('Audio not found')
            assignment['audio_started'].setdefault(side,self.clock())
            return path.read_bytes()

    @staticmethod
    def pair_kind(a,b):
        pair = frozenset((a['engine'],b['engine']))
        if 'kokoro' in pair:
            return 'candidate-control'
        return {frozenset(('voxcpm2','qwen3tts')):'weak1-baseline',
                frozenset(('cosyvoice3','qwen3tts')):'weak2-baseline',
                frozenset(('voxcpm2','cosyvoice3')):'weak1-weak2'}.get(pair,'engine-comparison')

    def vote(self, token, payload):
        with self.lock:
            session = self._session(token)
            assignment = self.assignments.get(payload['assignment_id'])
            if not assignment or assignment['session']!=token or assignment['voted']:
                raise ValueError('Assignment unavailable or already voted')
            for side in ('a','b'):
                required = assignment[side]['duration_s']*1000
                started = assignment['audio_started'].get(side)
                if started is None or (self.clock()-started)*1000 < required-100 or payload['listened_ms_'+side] < required-100:
                    raise ValueError('Listen to both complete clips before voting')
            row = {'ts':datetime.now(timezone.utc).isoformat(),**session,'locale':assignment['locale'],
                   'sentence_id':assignment['a']['sentence_id'],'pair_kind':self.pair_kind(assignment['a'],assignment['b']),
                   'pair_key':assignment['pair_key'], **{side:{k:assignment[side][k] for k in ('engine','normalize','item_id')}
                                                      for side in ('a','b')},
                   'audio_tokens':assignment['tokens'], **payload}
            folder = self.root/'results'
            folder.mkdir(parents=True,exist_ok=True)
            with (folder/(session['rater_id']+'.jsonl')).open('a') as file:
                file.write(json.dumps(row,ensure_ascii=False)+'\n')
                file.flush()
                import os
                os.fsync(file.fileno())
            assignment['voted'] = True
            for audio_token in assignment['tokens'].values():
                self.audio_tokens.pop(audio_token,None)
            return {'saved':True}
