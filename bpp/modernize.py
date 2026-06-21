# -*- coding: utf-8 -*-
"""Модернизация орфографии на блоке (символы + пунктуация, без flatten)."""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_WORD_FINAL_HARD = re.compile(r"(?<=[А-Яа-яЁё])ъ(?=$|\b)")
_PUNCT_HARD = re.compile(r"(?<=[А-Яа-яЁё])ъ(?=[\s,.:;!?…""»\)\]\-])")
_CHAR_TRANS = str.maketrans({
    "ѣ": "е", "Ѣ": "Е",
    "і": "и", "І": "И",
    "ѳ": "ф", "Ѳ": "Ф",
    "ѵ": "и", "Ѵ": "И",
})

LAT_TO_CYR = {
    "A": "А", "a": "а", "B": "В", "E": "Е", "e": "е", "K": "К", "k": "к",
    "M": "М", "H": "Н", "O": "О", "o": "о", "P": "Р", "p": "р",
    "C": "С", "c": "с", "T": "Т", "X": "Х", "x": "х", "Y": "У", "y": "у",
}

_TOKEN_RE = re.compile(r"[A-Za-z\u0400-\u04FF]+(?:-[A-Za-z\u0400-\u04FF]+)*")


def normalize_chars(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\u00A0", " ")
    t = t.translate(_CHAR_TRANS)
    t = _WORD_FINAL_HARD.sub("", t)
    t = _PUNCT_HARD.sub("", t)
    return t


def fix_mixed_latin_tokens(text: str) -> str:
    def fix_token(m: re.Match[str]) -> str:
        tok = m.group(0)
        if not (re.search(r"[\u0400-\u04FF]", tok) and re.search(r"[A-Za-z]", tok)):
            return tok
        return "".join(LAT_TO_CYR.get(ch, ch) for ch in tok)

    return _TOKEN_RE.sub(fix_token, text)


def normalize_punct(text: str) -> str:
    if not text:
        return ""
    t = text.replace("–", "—")
    t = re.sub(r"\s*—\s*", " — ", t)
    t = re.sub(r"(?<!\.)\.\.\.(?!\.)", "…", t)
    t = re.sub(r"\s+([,.:;?!…»)])", r"\1", t)
    t = re.sub(r"([\.;:?!…])(?=[А-Яа-яЁё])", r"\1 ", t)
    t = re.sub(r"(?<!\d),(?=[А-Яа-яЁё])", ", ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def modernize_block(text: str, *, role: str = "paragraph") -> str:
    if not text:
        return ""
    if role == "verse":
        lines = [normalize_punct(normalize_chars(ln)) for ln in text.split("\n")]
        return "\n".join(ln for ln in lines if ln)
    t = normalize_chars(text)
    t = fix_mixed_latin_tokens(t)
    t = normalize_punct(t)
    return _WS.sub(" ", t).strip()
