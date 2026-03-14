import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description='Run pdf_to_epub then harvest .sverka into rules/ocr')
    ap.add_argument('--activate', action='store_true', help='activate harvested regex rules (default: shadow)')
    ap.add_argument('--rules-dir', default='rules/ocr')
    # collect all remaining args for pdf_to_epub
    ap.add_argument('rest', nargs=argparse.REMAINDER, help='args passed to pdf_to_epub.py')
    args = ap.parse_args()

    # Prepare args for pipeline (strip leading "--" if present)
    rest = list(args.rest)
    if rest and rest[0] == '--':
        rest = rest[1:]

    # Run pipeline
    cmd = [sys.executable, str(HERE / 'pdf_to_epub.py')] + rest
    print('>', ' '.join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        sys.exit(rc)

    # Detect outdir from forwarded args
    outdir = 'out'
    for i, a in enumerate(rest):
        if a == '--outdir' and i + 1 < len(rest):
            outdir = rest[i + 1]
    out_path = Path(outdir)
    if not out_path.exists():
        print('No outdir found:', out_path)
        return 0

    # Harvest .sverka
    harv = [sys.executable, str(HERE / 'tools' / 'harvest_sverka.py'), '--rules-dir', args.rules_dir]
    if args.activate:
        harv.append('--activate')
    harv.append(str(out_path))
    print('>', ' '.join(harv))
    rc2 = subprocess.call(harv)
    return rc2

if __name__ == '__main__':
    raise SystemExit(main())
