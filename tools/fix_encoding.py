import sys, re
from pathlib import Path

CYR = re.compile(r'[А-Яа-яЁё]')
R_SEQ = re.compile(r'Р[Ѐ-џ]')

def choose_decode(raw: bytes):
    try:
        t = raw.decode('utf-8-sig')
        if CYR.search(t) and R_SEQ.search(t):
            pass
        else:
            return t
    except Exception:
        pass
    try:
        t1251 = raw.decode('cp1251')
        if CYR.search(t1251):
            return t1251
    except Exception:
        pass
    try:
        return raw.decode('utf-8', errors='replace')
    except Exception:
        return raw.decode('latin1', errors='replace')


def process_dir(src: Path):
    exts = {'.txt','.md','.json','.jsonl','.html'}
    fixed = 0
    for p in src.rglob('*'):
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        raw = p.read_bytes()
        new = choose_decode(raw)
        try:
            old = raw.decode('utf-8-sig')
        except Exception:
            old = None
        if old != new:
            p.write_text(new, encoding='utf-8')
            fixed += 1
    print(f'fixed={fixed}')

if __name__ == '__main__':
    process_dir(Path('out/bu_utf8'))
