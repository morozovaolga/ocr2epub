import argparse
import json
import time
from abc import ABC, abstractmethod
from html import escape as hesc
from pathlib import Path
from typing import Dict, List
from urllib import request, parse


SAFE_RULE_SUBSTR = (
    'MORFOLOGIK',   # spelling
    'WHITESPACE',   # generic whitespace
    'SPACE',        # any *_SPACE*
    'MULTIPLE_SPACES',
    'DOUBLE_PUNCTUATION',
)


def is_safe(rule_id: str) -> bool:
    rid = (rule_id or '').upper()
    return any(s in rid for s in SAFE_RULE_SUBSTR)


def chunks_by_paragraphs(text: str, max_len: int = 6000) -> List[str]:
    paras = text.split("\n\n")
    out, buf = [], []
    cur = 0
    for p in paras:
        piece = p + "\n\n"
        if cur + len(piece) > max_len and buf:
            out.append("".join(buf))
            buf, cur = [], 0
        buf.append(piece)
        cur += len(piece)
    if buf:
        out.append("".join(buf))
    return out


class SpellChecker(ABC):
    name = "checker"

    @abstractmethod
    def check(self, text: str) -> List[Dict]:
        raise NotImplementedError


class LanguageToolChecker(SpellChecker):
    name = "LanguageTool"

    def __init__(self, lang: str = 'ru-RU', timeout: int = 60):
        self.lang = lang
        self.timeout = timeout

    def check(self, text: str) -> List[Dict]:
        matches = cloud_check(text, self.lang, timeout=self.timeout)
        safe = [
            m for m in matches
            if is_safe((m.get('rule') or {}).get('id'))
        ]
        return safe


class YandexSpellerChecker(SpellChecker):
    name = "Yandex.Speller"

    def __init__(self, lang: str = 'ru', timeout: int = 60):
        self.lang = lang
        self.timeout = timeout

    def check(self, text: str) -> List[Dict]:
        url = 'https://speller.yandex.net/services/spellservice.json/checkText'
        data = parse.urlencode({
            'text': text,
            'lang': self.lang,
            'format': 'json'
        }).encode('utf-8')
        req = request.Request(url, data=data, headers={
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
        })
        with request.urlopen(req, timeout=self.timeout) as resp:
            payload = resp.read().decode('utf-8', errors='replace')
        entries = json.loads(payload)
        matches = []
        for entry in entries:
            replacements = entry.get('s') or []
            if not replacements:
                continue
            matches.append({
                'offset': entry.get('pos', 0),
                'length': entry.get('len', 0),
                'replacements': [{'value': replacements[0]}],
                'rule': {'id': 'YANDEX', 'description': entry.get('word')}
            })
        return matches


def run_spell_pipeline(text: str, checkers: List[SpellChecker], chunk_size: int, sleep: float):
    parts = chunks_by_paragraphs(text, max_len=chunk_size)
    fixed_parts = []
    stats = {checker.name: 0 for checker in checkers}

    for part in parts:
        part_text = part
        for checker in checkers:
            try:
                matches = checker.check(part_text)
            except Exception as exc:
                print(f"[{checker.name}] ошибка запроса: {exc}")
                matches = []
            if not matches:
                continue
            part_text = apply_matches(part_text, matches)
            stats[checker.name] += len(matches)
        fixed_parts.append(part_text)
        time.sleep(sleep)

    return "".join(fixed_parts), stats


def cloud_check(text: str, lang: str = 'ru-RU', timeout: int = 60):
    url = 'https://api.languagetool.org/v2/check'
    data = parse.urlencode({'text': text, 'language': lang}).encode('utf-8')
    req = request.Request(url, data=data, headers={
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
    })
    with request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode('utf-8', errors='replace')
    try:
        return json.loads(payload).get('matches', [])
    except Exception:
        return []


def apply_matches(text: str, matches) -> str:
    """Apply replacements for each non-overlapping match."""
    matches.sort(key=lambda m: m.get('offset', 0))
    res, i, last_end = [], 0, 0
    for m in matches:
        start = m.get('offset', 0)
        length = m.get('length', 0)
        end = start + length
        if start < last_end:
            continue
        reps = m.get('replacements') or []
        rep = reps[0].get('value') if reps else None
        if rep is None:
            continue
        res.append(text[i:start])
        res.append(rep)
        i = end
        last_end = end
    res.append(text[i:])
    return "".join(res)


def to_html(text: str, title: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"ru\">\n<head>\n"
        "<meta charset=\"utf-8\"/>\n"
        f"<title>{hesc(title)}</title>\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>\n"
        "<style>body{font:18px/1.6 Georgia,Times,\"Times New Roman\",serif;margin:2rem;max-width:48rem;color:#111;background:#fff} pre{white-space:pre-wrap}</style>\n"
        "</head>\n<body>\n<pre contenteditable=\"true\" spellcheck=\"true\">" + hesc(text) + "</pre>\n</body>\n</html>\n"
    )


def main():
    ap = argparse.ArgumentParser(description='Apply safe LanguageTool (cloud) + Yandex.Speller fixes.')
    ap.add_argument('--in', dest='inp', required=True, help='Входной TXT')
    ap.add_argument('--outdir', default='out', help='Папка вывода')
    ap.add_argument('--title', default='Документ (LT)', help='Заголовок HTML')
    ap.add_argument('--sleep', type=float, default=0.5, help='Пауза между запросами (сек)')
    ap.add_argument('--timeout', type=int, default=60, help='Таймаут HTTP (сек)')
    ap.add_argument('--chunk-size', type=int, default=6000, help='Максимум символов для одного запроса')
    ap.add_argument('--yandex-speller', action='store_true',
                    help='Дополнительно использовать Yandex.Speller (бесплатно, без ключа)')
    ap.add_argument('--no-lt', action='store_true',
                    help='Не использовать LanguageTool (только Yandex.Speller)')
    args = ap.parse_args()

    inp = Path(args.inp)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    text = inp.read_text(encoding='utf-8', errors='replace')
    checkers = []
    if not args.no_lt:
        checkers.append(LanguageToolChecker(lang='ru-RU', timeout=args.timeout))
    if args.yandex_speller:
        checkers.append(YandexSpellerChecker(lang='ru', timeout=args.timeout))
    if not checkers:
        checkers.append(LanguageToolChecker(lang='ru-RU', timeout=args.timeout))

    fixed_text, stats = run_spell_pipeline(
        text,
        checkers,
        chunk_size=args.chunk_size,
        sleep=args.sleep,
    )
    (outdir / 'final_clean.txt').write_text(fixed_text, encoding='utf-8')
    (outdir / 'final_clean.html').write_text(to_html(fixed_text, args.title), encoding='utf-8')
    total_safe = sum(stats.values())
    detail = ", ".join(f"{name}: {count}" for name, count in stats.items())
    print(f'Применено исправлений: {total_safe} ({detail}). Сохранено final_clean.txt/html в {outdir}')


if __name__ == '__main__':
    main()

