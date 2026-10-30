"""Separate clock domains and append-only turn snapshots, including playback updates."""
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

from server.config import ROOT

SERVER_MARKS = ('s_rx', 's_turn_decided', 's_stt_done', 's_llm_first_token',
                's_tool_call', 's_tool_done', 's_first_sentence', 's_tts_first_audio')


def now_ms():
    return perf_counter() * 1000


def durations(marks):
    pairs = {'turn_decision_ms': ('s_turn_decided', 's_rx'),
             'stt_ms': ('s_stt_done', 's_turn_decided'),
             'llm_ttft_ms': ('s_llm_first_token', 's_stt_done'),
             'first_sentence_ms': ('s_first_sentence', 's_llm_first_token'),
             'tts_ttfa_ms': ('s_tts_first_audio', 's_first_sentence'),
             'client_vad_to_play_ms': ('c_play_start', 'c_vad_end'),
             'client_speech_to_play_ms': ('c_play_start', 'c_speech_end_est'),
             'client_vad_to_reply_play_ms': ('c_reply_play_start', 'c_vad_end'),
             'client_speech_to_reply_play_ms': ('c_reply_play_start', 'c_speech_end_est')}
    return {key: marks[end] - marks[start] if marks.get(end) is not None and marks.get(start) is not None else None
            for key, (end, start) in pairs.items()}


@dataclass
class TurnLog:
    session_id: str
    turn_num: int
    settings: dict
    path: Path | None = None
    marks: dict = field(default_factory=lambda: dict.fromkeys(SERVER_MARKS))
    details: dict = field(default_factory=dict)
    revision: int = 0

    def mark(self, name, value=None):
        if self.marks.get(name) is None:
            self.marks[name] = now_ms() if value is None else value

    def write(self, status):
        self.revision += 1
        record = {'session_id': self.session_id, 'turn_num': self.turn_num,
                  'revision': self.revision, 'status': status,
                  'timestamp': datetime.now().astimezone().isoformat(),
                  **self.settings, 'marks': self.marks, 'durations': durations(self.marks), **self.details}
        path = self.path or ROOT / 'data/logs' / f'turns-{datetime.now():%Y%m%d}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as output:
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        return record
