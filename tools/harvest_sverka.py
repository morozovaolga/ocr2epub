import argparse
import collections
import datetime as dt
import json
import os
import re
import unicodedata as ud
from pathlib import Path
from typing import List, Tuple, Dict

PAIR = collections.Counter()

TOK = re.compile(r'<mark\s+class="(del|ins)"[^>]*>(.*?)</mark>', re.S)
TAG = re.compile(r'<[^>]+>')
WS = re.compile(r'\s+')


def extract_pairs(html: str) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    pos = 0
    tokens: List[Tuple[str, str]] = []  # (type, text)
    for m in TOK.finditer(html):
        if m.start() > pos:
            tokens.append(("txt", html[pos:m.start()]))
        kind = m.group(1)
        text = m.group(2)
        tokens.append((kind, TAG.sub('', text)))
        pos = m.end()
    if pos < len(html):
        tokens.append(("txt", html[pos:]))

    i = 0
    n = len(tokens)
    while i < n:
        if tokens[i][0] == 'del':
            dels: List[str] = []
            while i < n and tokens[i][0] == 'del':
                dels.append(tokens[i][1])
                i += 1
            j = i
            while j < n and tokens[j][0] == 'txt' and WS.fullmatch(tokens[j][1] or ''):
                j += 1
            if j < n and tokens[j][0] == 'ins':
                ins: List[str] = []
                while j < n and tokens[j][0] == 'ins':
                    ins.append(tokens[j][1])
                    j += 1
                d = normalize(''.join(dels))
                s = normalize(''.join(ins))
                if d and s and d != s:
                    pairs.append((d, s))
                i = j
                continue
        i += 1
    return pairs


def normalize(s: str) -> str:
    s = s.replace('\u00A0', ' ')
    s = s.replace('\xad', '')  # soft hyphen
    s = TAG.sub('', s)
    s = s.strip()
    return s


def allow_char(c: str) -> bool:
    if not c:
        return False
    if c.isalpha():
        return True
    if ud.category(c).startswith('P'):
        # punctuation (quotes, dash etc.)
        return c in '—–-‑–“”„«»‹›'  # restrict to common textual punctuation
    return False


def harvest(sverka_paths: List[Path]) -> Tuple[Dict[str, str], List[Dict]]:
    char_map: Dict[str, str] = {}
    rules: List[Dict] = []

    agg = collections.Counter()
    for p in sverka_paths:
        try:
            j = json.loads(p.read_text(encoding='utf-8-sig', errors='ignore'))
        except Exception:
            continue
        html = j.get('html') or ''
        for d, s in extract_pairs(html):
            agg[(d, s)] += 1

    for (d, s), cnt in agg.most_common():
        if len(d) == 1 and len(s) == 1 and allow_char(d) and allow_char(s):
            if d != '.' and s != '.':
                char_map[d] = s
        else:
            if cnt >= 3 and 1 < len(d) <= 10 and '\n' not in d and '\n' not in s:
                rule = {
                    'id': f'R-HARV-{abs(hash((d,s)))%10**8:08d}',
                    'pattern': re.escape(d),
                    'replace': s,
                    'scope': ['paragraph','header'],
                    'status': 'shadow',
                    'notes': f'harvested x{cnt}'
                }
                rules.append(rule)
    return char_map, rules


def merge_char_confusions(path: Path, new_map: Dict[str, str]) -> int:
    if not new_map:
        return 0
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig', errors='ignore')) if path.exists() else {}
    except Exception:
        data = {}
    sec = data.setdefault('extra', {})
    for k, v in new_map.items():
        if k not in sec and k not in (data.get('lat_to_cyr') or {}):
            sec[k] = v
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return len(new_map)


def append_jsonl(path: Path, items: List[Dict]) -> int:
    if not items:
        return 0
    with path.open('a', encoding='utf-8') as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + '\n')
    return len(items)


def main():
    ap = argparse.ArgumentParser(description='Harvest rules from .sverka files')
    ap.add_argument('--rules-dir', default='rules/ocr')
    ap.add_argument('--activate', action='store_true', help='mark harvested regex rules as active')
    ap.add_argument('inputs', nargs='+', help='Paths to .sverka files or directories')
    args = ap.parse_args()

    sverkas: List[Path] = []
    for inp in args.inputs:
        p = Path(inp)
        if p.is_dir():
            sverkas += list(p.glob('*.sverka'))
        elif p.suffix.lower() == '.sverka':
            sverkas.append(p)
    if not sverkas:
        print('No .sverka files found')
        return 0

    char_map, rules = harvest(sverkas)
    if args.activate:
        for r in rules:
            r['status'] = 'active'

    rules_dir = Path(args.rules_dir)
    rules_dir.mkdir(parents=True, exist_ok=True)

    wrote_chars = merge_char_confusions(rules_dir / 'char_confusions.json', char_map)
    wrote_rules = append_jsonl(rules_dir / 'regex_rules.jsonl', rules)

    logp = rules_dir / 'harvest_log.jsonl'
    stamp = dt.datetime.utcnow().isoformat() + 'Z'
    with logp.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'ts': stamp, 'sverka': [str(p) for p in sverkas], 'chars': wrote_chars, 'regex': wrote_rules})+'\n')

    print(f'Harvested: chars={wrote_chars}, regex_rules={wrote_rules}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
