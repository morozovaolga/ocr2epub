import argparse, json, re
from collections import Counter
from pathlib import Path
import difflib

TOK = re.compile(r"\w+|\S", re.U)


def load(p: Path) -> str:
    return p.read_text(encoding='utf-8', errors='ignore')


def tokenize(s: str):
    return TOK.findall(s)


def suggest(orig: str, edit: str):
    a = tokenize(orig)
    b = tokenize(edit)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    subs = Counter()
    for tag,i1,i2,j1,j2 in sm.get_opcodes():
        if tag in ('replace','delete','insert'):
            aa = ''.join(a[i1:i2]).strip()
            bb = ''.join(b[j1:j2]).strip()
            if aa==bb or (not aa and not bb):
                continue
            aa = re.sub(r"\s+"," ", aa)
            bb = re.sub(r"\s+"," ", bb)
            if aa and bb:
                subs[(aa,bb)] += 1
    char_map = {}
    dict_norm = []
    regex_rules = []
    for (aa,bb), cnt in subs.most_common():
        if len(aa)==1 and len(bb)==1 and aa != '.' and bb != '.':
            char_map[aa]=bb
        elif re.fullmatch(r"[\w\-]+", aa) and re.fullmatch(r"[\w\-]+", bb):
            dict_norm.append({'from':aa,'to':bb,'count':cnt})
        elif 2<=len(aa)<=48 and '\n' not in aa+bb:
            regex_rules.append({'pattern':aa,'replace':bb,'count':cnt})
    return char_map, dict_norm, regex_rules


def append_jsonl(path: Path, items):
    if not items: return 0
    with path.open('a', encoding='utf-8') as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False)+'\n')
    return len(items)


def merge_chars(path: Path, m):
    if not m: return 0
    try:
        j = json.loads(path.read_text(encoding='utf-8-sig', errors='ignore')) if path.exists() else {}
    except Exception:
        j = {}
    extra = j.setdefault('extra', {})
    added = 0
    for k,v in m.items():
        if k not in extra:
            extra[k]=v; added+=1
    path.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding='utf-8')
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--orig', required=True)
    ap.add_argument('--edit', required=True)
    ap.add_argument('--rules-dir', default='rules/ocr')
    ap.add_argument('--min-dict', type=int, default=1)
    ap.add_argument('--min-regex', type=int, default=2)
    args = ap.parse_args()

    char_map, dict_norm, regex_rules = suggest(load(Path(args.orig)), load(Path(args.edit)))
    # thresholds
    dict_norm = [d for d in dict_norm if d['count'] >= args.min_dict]
    regex_rules = [r | {'id': f"R-DIFF-{abs(hash((r['pattern'],r['replace'])) )%10**8:08d}", 'status':'shadow','scope':['paragraph','header']}
                    for r in regex_rules if r['count'] >= args.min_regex]

    rules_dir = Path(args.rules_dir)
    rules_dir.mkdir(parents=True, exist_ok=True)
    added_chars = merge_chars(rules_dir/'char_confusions.json', char_map)
    added_dict = append_jsonl(rules_dir/'dictionaries/normalize.jsonl', [{'from':d['from'],'to':d['to']} for d in dict_norm])
    added_regex = append_jsonl(rules_dir/'regex_rules.jsonl', [{k:v for k,v in r.items() if k!='count'} for r in regex_rules])

    print(json.dumps({'added_chars':added_chars,'added_dict':added_dict,'added_regex':added_regex}, ensure_ascii=False))

if __name__=='__main__':
    main()
