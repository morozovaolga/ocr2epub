import argparse, json, re
from pathlib import Path
from html import unescape

del_attr = re.compile(r'<mark\s+class="del"[^>]*?data-pdf-text="(.*?)"[^>]*?>(.*?)</mark>', re.S)
mark_tok = re.compile(r'<mark\s+class="(del|ins)"[^>]*?>(.*?)</mark>', re.S)
strip_tags = re.compile(r'<[^>]+>')
ws = re.compile(r'\s+')

def norm(s: str) -> str:
    s = s.replace('\u00A0',' ').replace('\xad','')
    s = unescape(s)
    s = strip_tags.sub('', s)
    s = ws.sub(' ', s).strip()
    return s

def harvest_pairs(html: str):
    pairs = []
    # 1) prefer del[data-pdf-text]
    for m in del_attr.finditer(html):
        good = norm(m.group(1))
        bad  = norm(m.group(2))
        if good and bad and good != bad and '\n' not in good+bad and len(bad) <= 48:
            pairs.append((bad, good))
    if pairs:
        return pairs
    # 2) fallback: consecutive del -> next ins
    toks = [(m.group(1), norm(m.group(2))) for m in mark_tok.finditer(html)]
    i = 0
    while i < len(toks):
        if toks[i][0] == 'del':
            bads = []
            j = i
            while j < len(toks) and toks[j][0] == 'del':
                if toks[j][1]:
                    bads.append(toks[j][1])
                j += 1
            k = j
            while k < len(toks) and toks[k][0] == 'ins' and not toks[k][1]:
                k += 1
            if k < len(toks) and toks[k][0] == 'ins' and toks[k][1]:
                good = toks[k][1]
                bad  = ' '.join(bads).strip()
                if bad and good and bad != good and '\n' not in bad+good and len(bad) <= 48:
                    pairs.append((bad, good))
                i = k + 1
            else:
                i = j
        else:
            i += 1
    return pairs


def append_normalize(path: Path, pairs):
    if not pairs:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open('a', encoding='utf-8') as f:
        for bad, good in pairs:
            f.write(json.dumps({"from": bad, "to": good}, ensure_ascii=False) + "\n")
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sverka', required=True)
    ap.add_argument('--out', default='rules/ocr/dictionaries/normalize.jsonl')
    args = ap.parse_args()

    data = json.loads(Path(args.sverka).read_text(encoding='utf-8-sig', errors='ignore'))
    html = data.get('html') or ''
    pairs = harvest_pairs(html)
    added = append_normalize(Path(args.out), pairs)
    print(json.dumps({'pairs_found': len(pairs), 'added': added}, ensure_ascii=False))

if __name__ == '__main__':
    main()
