# -*- coding: utf-8 -*-
"""Правила OCR: char_confusions, словари, regex, oldspelling."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .paths import default_oldspelling_path, default_rules_dir


def load_char_confusions(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    mapping: Dict[str, str] = {}
    for sec in ("lat_to_cyr", "digits"):
        for k, v in (data.get(sec) or {}).items():
            if isinstance(k, str) and isinstance(v, str) and k and v:
                mapping[k] = v
    return mapping


def load_regex_rules_jsonl(path: Path) -> List[Tuple[str, str, str, List[str]]]:
    out: List[Tuple[str, str, str, List[str]]] = []
    if not path.is_file():
        return out
    for idx, line in enumerate(path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (j.get("status") or "active").lower() != "active":
            continue
        pat = j.get("pattern")
        rep = j.get("replace", "")
        scope = j.get("scope") or []
        rule_id = j.get("id") or f"regex:{idx}"
        if isinstance(pat, str) and isinstance(rep, str):
            scopes = [s for s in scope if isinstance(s, str)] if scope else []
            out.append((rule_id, pat, rep, scopes))
    return out


def load_dict_normalize(path: Path) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    if not path.exists():
        return items
    paths = [path] if path.is_file() else sorted(path.glob("*.jsonl"))
    for fp in paths:
        for line in fp.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except json.JSONDecodeError:
                continue
            frm = j.get("from") or j.get("src")
            to = j.get("to") or j.get("dst")
            if isinstance(frm, str) and isinstance(to, str) and frm and to:
                items.append((r"\b" + re.escape(frm) + r"\b", to))
    return items


def load_rules_from_py(file_path: Path) -> List[Tuple[str, str]]:
    if not file_path.is_file():
        return []
    src = file_path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src, str(file_path))
    rules: List[Tuple[str, str]] = []

    class V(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
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
            if not is_resub or len(val.args) < 3:
                return
            pat, repl = val.args[0], val.args[1]
            if isinstance(pat, ast.Constant) and isinstance(repl, ast.Constant):
                p, r = pat.value, repl.value
                if isinstance(p, str) and isinstance(r, str):
                    rules.append((p, r))
            self.generic_visit(node)

    V().visit(tree)
    return rules


def apply_char_map(text: str, cmap: Dict[str, str]) -> str:
    if not cmap or not text:
        return text
    table = {ord(k): v for k, v in cmap.items() if len(k) == 1 and len(v) == 1}
    out = text.translate(table)
    for k, v in sorted(cmap.items(), key=lambda x: -len(x[0])):
        if len(k) != 1 or len(v) != 1:
            out = out.replace(k, v)
    return out


def _apply_regex_rules(text: str, rules: List[Tuple[str, str, str, List[str]]], role: str) -> str:
    role_l = (role or "paragraph").lower()
    for _rid, pat, repl, scopes in rules:
        if scopes and role_l not in {s.lower() for s in scopes}:
            continue
        try:
            text = re.sub(pat, repl, text)
        except re.error:
            continue
    return text


@dataclass
class RulesPack:
    char_map: Dict[str, str]
    dict_norm: List[Tuple[str, str]]
    json_rules: List[Tuple[str, str, str, List[str]]]
    legacy_rules: List[Tuple[str, str]]

    @classmethod
    def load(
        cls,
        rules_dir: Optional[Path] = None,
        *,
        oldspelling_path: Optional[Path] = None,
        use_oldspelling: bool = True,
    ) -> RulesPack:
        rd = rules_dir or default_rules_dir()
        legacy: List[Tuple[str, str]] = []
        if use_oldspelling:
            osp = oldspelling_path or default_oldspelling_path()
            legacy = load_rules_from_py(osp)
        return cls(
            char_map=load_char_confusions(rd / "char_confusions.json"),
            dict_norm=load_dict_normalize(rd / "dictionaries"),
            json_rules=load_regex_rules_jsonl(rd / "regex_rules.jsonl"),
            legacy_rules=legacy,
        )

    def apply_text(self, text: str, *, role: str = "paragraph") -> str:
        if not text:
            return text
        txt = apply_char_map(text, self.char_map)
        for pat, repl in self.dict_norm:
            txt = re.sub(pat, repl, txt)
        txt = _apply_regex_rules(txt, self.json_rules, role)
        for pat, repl in self.legacy_rules:
            txt = re.sub(pat, repl, txt)
        return txt

    def apply_block(self, block: dict) -> dict:
        nb = dict(block)
        raw = block.get("text_raw") or block.get("text") or ""
        nb["text_raw"] = raw
        role = block.get("role") or "paragraph"
        nb["text"] = self.apply_text(raw, role=role)
        return nb


@lru_cache(maxsize=4)
def _cached_pack(
    rules_dir_str: str,
    oldspelling_str: str,
    use_oldspelling: bool,
) -> RulesPack:
    rd = Path(rules_dir_str) if rules_dir_str else None
    osp = Path(oldspelling_str) if oldspelling_str else None
    return RulesPack.load(rd, oldspelling_path=osp, use_oldspelling=use_oldspelling)


def get_rules_pack(
    rules_dir: Optional[Path] = None,
    *,
    oldspelling_path: Optional[Path] = None,
    use_oldspelling: bool = True,
) -> RulesPack:
    return _cached_pack(
        str(rules_dir.resolve()) if rules_dir else "",
        str(oldspelling_path.resolve()) if oldspelling_path else "",
        use_oldspelling,
    )


def apply_rules_to_blocks(
    blocks: List[dict],
    *,
    rules_dir: Path | None = None,
    oldspelling_path: Path | None = None,
    use_oldspelling: bool = True,
) -> List[dict]:
    pack = get_rules_pack(
        rules_dir,
        oldspelling_path=oldspelling_path,
        use_oldspelling=use_oldspelling,
    )
    return [pack.apply_block(b) for b in blocks]
