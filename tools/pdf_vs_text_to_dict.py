import argparse, re, json
from pathlib import Path

try:
    import fitz  # PyMuPDF
except Exception:
    print('{"error":"PyMuPDF not installed"}')
    raise

TOK = re.compile(r"\w+|\S", re.U)
WS = re.compile(r"\s+")


def read_pdf_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    chunks = []
    for pno in range(len(doc)):
        t = doc.load_page(pno).get_text()
        chunks.append(t)
    return "\n".join(chunks)


def normalize(s: str) -> str:
    return WS.sub(' ', s.replace('\u00A0',' ')).strip()


def tokens(s: str):
    return TOK.findall(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf', required=True)
    ap.add_argument('--text', required=True)
    ap.add_argument('--out', default='rules/ocr/dictionaries/normalize.jsonl')
    ap.add_argument('--min-count', type=int, default=2)
    args = ap.parse_args()

    pdf_txt = normalize(read_pdf_text(Path(args.pdf)))
    out_txt = normalize(Path(args.text).read_text(encoding='utf-8', errors='ignore'))

    a = tokens(out_txt)
    b = tokens(pdf_txt)

    import difflib
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    from collections import Counter
    subs = Counter()
    for tag,i1,i2,j1,j2 in sm.get_opcodes():
        if tag in ('replace','delete','insert'):
            wrong = ''.join(a[i1:i2]).strip()
            right = ''.join(b[j1:j2]).strip()
            if wrong and right and wrong!= right and '\n' not in wrong+right:
                # if both are single words (or simple hyphen compounds)
                if re.fullmatch(r"[\w\-]{1,24}", wrong) and re.fullmatch(r"[\w\-]{1,24}", right):
                    subs[(wrong, right)] += 1
    pairs = [(w,r,c) for (w,r),c in subs.most_common() if c >= args.min_count]

    # append
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open('a', encoding='utf-8') as f:
        for w,r,c in pairs:
            f.write(json.dumps({'from': w, 'to': r}, ensure_ascii=False)+'\n')
            n += 1
    print(json.dumps({'candidates': len(subs), 'appended': n}, ensure_ascii=False))

if __name__=='__main__':
    main()
