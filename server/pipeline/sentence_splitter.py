"""Streaming punctuation splitter; keep whitespace for exact history prefixes.

The T6 agent already emits complete, validated sentences. Call complete() for
that interface; feed()/finish() support a future genuine text-token producer.
"""
import re
from server.llm.sentences import split_sentences


class SentenceSplitter:
    def __init__(self, locale='en', first_comma=False):
        self.locale = locale
        self.first_comma = first_comma
        self.buffer = ''
        self.emitted = False

    def _boundary(self, final):
        for match in re.finditer(r'[.!?。！？,，、]', self.buffer):
            i = match.start()
            char = self.buffer[i]
            if char == '.':
                if i + 1 == len(self.buffer) and not final:
                    return None  # one character lookahead for decimals/abbreviations
                if i and i + 1 < len(self.buffer) and self.buffer[i-1].isdigit() and self.buffer[i+1].isdigit():
                    continue
                if re.search(r'(?:\b[a-z]\.)+$|\b(?:Mr|Mrs|Ms|Dr|St)\.$', self.buffer[:i+1], re.I):
                    continue
            if char in ',，、':
                if not self.first_comma or self.emitted:
                    continue
                if i and i + 1 < len(self.buffer) and self.buffer[i-1].isdigit() and self.buffer[i+1].isdigit():
                    continue
                count = len(self.buffer[:i].split()) if self.locale == 'en' else len(re.sub(r'\s', '', self.buffer[:i]))
                if count < (4 if self.locale == 'en' else 6):
                    continue
            return i + 1
        return None

    def feed(self, text):
        self.buffer += text
        return self._drain(False)

    def _drain(self, final):
        result = []
        while (end := self._boundary(final)) is not None:
            result.append(self.buffer[:end])
            self.buffer = self.buffer[end:]
            self.emitted = True
        return result

    def finish(self):
        result = self._drain(True)
        if self.buffer.strip():
            result.append(self.buffer)
        self.buffer = ''
        return result

    @staticmethod
    def complete(text):
        return split_sentences(text)
