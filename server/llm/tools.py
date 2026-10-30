"""Exactly three allowed tools, session-scoped verification and handoff state."""
from dataclasses import dataclass, field
from pathlib import Path
import json
from .fake_db import FakeDB
from server.normalize.codes import parse_spoken_code
from server.normalize.lexicon import validate_locale

TOOLS = json.loads(Path(__file__).with_name('tool_schemas.json').read_text())
REASONS = {'user_request','upset_customer','policy_exception','lookup_failed','other'}


@dataclass
class ToolSession:
    locale: str
    db: FakeDB = field(default_factory=FakeDB)
    verified_codes: set[str] = field(default_factory=set)
    lookup_failures: int = 0
    transferred: bool = False

    def __post_init__(self):
        validate_locale(self.locale)

    def execute(self, name: str, arguments: dict) -> dict:
        if self.transferred:
            return {'error':'session_transferred','queued':True}
        schema = next((t['function'] for t in TOOLS if t['function']['name']==name),None)
        if not schema:
            return {'error':'unknown_tool'}
        if not isinstance(arguments,dict):
            return {'error':'invalid_arguments'}
        props = schema['parameters']['properties']
        if set(arguments) != set(schema['parameters']['required']) or any(not isinstance(v,str) or not v.strip() or len(v)>1000 for v in arguments.values()):
            return {'error':'invalid_arguments','required':list(props)}
        if name == 'transfer_to_human':
            if arguments['reason'] not in REASONS:
                return {'error':'invalid_reason'}
            self.transferred = True
            return {'queued':True,'estimated_wait_min':3}
        code = parse_spoken_code(arguments['reservation_code'],self.locale)
        if not code:
            return {'error':'invalid_reservation_code','expected':'ZV-1234'}
        if name == 'lookup_reservation':
            result = self.db.lookup(code,arguments['last_name'],self.locale)
            if result['found']:
                self.verified_codes.add(code)
                self.lookup_failures = 0
            else:
                self.verified_codes.discard(code)
                self.lookup_failures += 1
                result['lookup_failures'] = self.lookup_failures
                result['transfer_required'] = self.lookup_failures >= 2
            return result
        if code not in self.verified_codes:
            return {'error':'lookup_required','message':'Verify code and surname with lookup_reservation first.'}
        return self.db.cancellation_policy(code,self.locale)
