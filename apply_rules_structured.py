import argparse
import ast
import json
import re
from pathlib import Path
from typing import List, Tuple, Dict, Any


def load_rules_from_py(file_path: Path):
    src = file_path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src, str(file_path))
    rules: list[tuple[str, str]] = []

    class V(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign):
            val = node.value
            if not isinstance(val, ast.Call):
                return
            func = val.func
            is_resub = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "re"
                and func.attr == "sub"
            )
            if not is_resub:
                return
            args = val.args
            if len(args) >= 3:
                pat, repl, sarg = args[0], args[1], args[2]
                if isinstance(pat, (ast.Str, ast.Constant)) and isinstance(repl, (ast.Str, ast.Constant)):
                    p = pat.s if isinstance(pat, ast.Str) else pat.value
                    r = repl.s if isinstance(repl, ast.Str) else repl.value
                    if isinstance(p, str) and isinstance(r, str):
                        rules.append((p, r))
            self.generic_visit(node)

    V().visit(tree)
    return rules


# NEW: load OCR JSON rules

def load_char_confusions(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    j = json.loads(path.read_text(encoding="utf-8-sig", errors="ignore"))
    mapping: Dict[str, str] = {}
    for sec in ("lat_to_cyr", "cyr_to_lat", "digits"):
        for k, v in (j.get(sec) or {}).items():
            if isinstance(k, str) and isinstance(v, str) and k and v:
                mapping[k] = v
    return mapping


def apply_char_map(text: str, cmap: Dict[str, str]) -> str:
    if not cmap:
        return text
    # build translate table for single-char keys only, others via replace
    table = {ord(k): v for k, v in cmap.items() if len(k) == 1 and len(v) == 1}
    out = text.translate(table)
    for k, v in cmap.items():
        if len(k) != 1 or len(v) != 1:
            out = out.replace(k, v)
    return out


def load_regex_rules_jsonl(path: Path) -> List[Tuple[str, str, List[str]]]:
    out: List[Tuple[str, str, List[str]]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        status = (j.get("status") or "active").lower()
        if status != "active":
            continue
        pat = j.get("pattern")
        rep = j.get("replace", "")
        scope = j.get("scope") or []
        if isinstance(pat, str) and isinstance(rep, str):
            scopes = [s for s in scope if isinstance(s, str)] if scope else []
            out.append((pat, rep, scopes))
    return out



def load_dict_normalize(path: Path):
    items = []
    if not path.exists():
        return items
    for line in path.read_text(encoding='utf-8-sig', errors='ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        frm = j.get('from'); to = j.get('to')
        if isinstance(frm,str) and isinstance(to,str) and frm and to:
            pat = r'\b' + re.escape(frm) + r'\b'
            items.append((pat, to))
    return items


def main():
    ap = argparse.ArgumentParser(description="Apply re.sub rules to structured blocks JSON.")
    ap.add_argument("--rules", default="oldspelling.py", help="Path to legacy .py rules (optional)")
    ap.add_argument("--ocr-rules-dir", default="rules/ocr", help="Directory with OCR JSON rules (optional)")
    ap.add_argument("--in", dest="inp", default="output_vol2/structured.json", help="Structured JSON input")
    ap.add_argument("--out", default="output_vol2/structured_rules.json", help="Structured JSON output")
    args = ap.parse_args()

    # Load rules
    legacy_rules = []
    if args.rules and Path(args.rules).exists():
        legacy_rules = load_rules_from_py(Path(args.rules))

    ocr_dir = Path(args.ocr_rules_dir)
    char_map = load_char_confusions(ocr_dir / "char_confusions.json")
    json_rules = load_regex_rules_jsonl(ocr_dir / "regex_rules.jsonl")
    dict_norm = load_dict_normalize(ocr_dir / 'dictionaries' / 'normalize.jsonl')

    data = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    blocks = data.get("blocks", [])
    applied_total = 0

    for b in blocks:
        txt = b.get("text") or ""
        role = (b.get("role") or "paragraph").lower()
        # 1) Char confusions first
        new_txt = apply_char_map(txt, char_map)
        if new_txt != txt:
            applied_total += 1
            txt = new_txt
        # 2) Dictionary normalize (whole words)
        for pat, repl in dict_norm:
            try:
                new_txt, n = re.subn(pat, repl, txt)
            except re.error:
                n = 0
            if n:
                applied_total += n
                txt = new_txt
        # 3) JSON regex rules
        for pat, repl, scopes in json_rules:
            if scopes and role not in {s.lower() for s in scopes}:
                continue
            try:
                new_txt, n = re.subn(pat, repl, txt)
            except re.error:
                continue
            if n:
                applied_total += n
                txt = new_txt
        # 3) Legacy .py rules
        for pat, repl in legacy_rules:
            try:
                new_txt, n = re.subn(pat, repl, txt)
            except re.error:
                continue
            if n:
                applied_total += n
                txt = new_txt
        b["text"] = txt

    data["rules_applied"] = applied_total
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {args.out} (total replacements: {applied_total})")


if __name__ == "__main__":
    main()

