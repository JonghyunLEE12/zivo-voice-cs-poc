"""Conservative fast path: explicit code and surname in this utterance only."""
import re
from server.normalize.codes import parse_spoken_code


def prefetch_calls(text: str, locale: str) -> list[tuple[str, dict]]:
    # Escalation/exception requests belong to the model; do not expose booking details first.
    if re.search(r'human|agent|exception|waive|人工|真人|破例|例外|担当者|免除', text, re.I):
        return []
    code = parse_spoken_code(text, locale)
    patterns = {
        'en': r'\b(?:last name|surname)\s*(?:is\s+)?([A-Za-z][A-Za-z\'-]{0,39})(?=[\s.,!?]|$)',
        'zh-CN': r'(?:我姓|姓氏是)\s*([\u3400-\u9fff]{1,4})(?=[，。！？\s]|$)',
        'zh-TW': r'(?:我姓|姓氏是)\s*([\u3400-\u9fff]{1,4})(?=[，。！？\s]|$)',
        'ja': r'(?:名字|姓)は\s*([\u3400-\u9fff\u3040-\u30ff]{1,12}?)です(?=[。、\s]|$)',
    }
    matches = list(re.finditer(patterns.get(locale, r'(?!)'), text, re.I))
    if not code or len(matches) != 1:
        return []
    surname = matches[0][1]
    if surname.casefold() in {'not', 'is', 'unknown', 'wrong', 'isn\'t'}:
        return []
    calls = [('lookup_reservation', {'reservation_code':code, 'last_name':surname})]
    if re.search(r'cancel|取消|キャンセル', text, re.I):
        calls.append(('get_cancellation_policy', {'reservation_code':code}))
    return calls
