"""Sentence boundaries shared by LLM delivery and latency probes (no TTS dependency)."""
import re


def split_sentences(text: str) -> list[str]:
    parts, start = [], 0
    for match in re.finditer(r'[.!?。！？\n]+', text):
        i = match.start()
        if i < start:
            continue
        if text[i] == '.':
            # A line-leading numbered list marker belongs to the following text.
            if re.search(r'(?:^|\n)\s*\d+\.$', text[:i+1]):
                continue
            if i and i + 1 < len(text) and text[i-1].isdigit() and text[i+1].isdigit():
                continue
            # Preserve dotted abbreviations and common titles, including a.m./p.m.
            if re.search(r'(?:\b[a-z]\.){1,}$', text[:i+1], re.I) or re.search(r'\b(?:Mr|Mrs|Ms|Dr|St)\.$', text[:i+1], re.I):
                continue
        end = match.end()
        while end < len(text) and text[end] in '」』）)\"\'”’】':
            end += 1
        if text[start:end].strip():
            parts.append(text[start:end])
        start = end
    if text[start:].strip():
        parts.append(text[start:])
    return parts
