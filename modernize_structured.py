import argparse
import json
import re
from pathlib import Path


LAT_TO_CYR = {
    "A": "А", "a": "а",
    "B": "В", "E": "Е", "e": "е",
    "K": "К", "k": "к",
    "M": "М",
    "H": "Н",
    "O": "О", "o": "о",
    "P": "Р", "p": "р",
    "C": "С", "c": "с",
    "T": "Т",
    "X": "Х", "x": "х",
    "Y": "У", "y": "у",
}


def mark(ch_from: str, ch_to: str, shown: str | None = None) -> str:
    shown = ch_to if shown is None else shown
    title = f"{ch_from}→{ch_to}"
    return f"<mark class=\"flag\" data-from=\"{ch_from}\" data-to=\"{ch_to}\" title=\"{title}\">{shown}</mark>"


def apply_letter_flags(text: str):
    flags = []

    def sub_and_flag(pattern, to_char, name):
        nonlocal text
        out = []
        i = 0
        n = 0
        for m in re.finditer(pattern, text):
            out.append(text[i:m.start()])
            ch = m.group(0)
            out.append(mark(ch, to_char))
            flags.append({"type": name, "from": ch, "to": to_char, "pos": m.start()})
            i = m.end()
            n += 1
        out.append(text[i:])
        text = "".join(out)
        return n

    # Old letters
    sub_and_flag(r"ѣ", "е", "yat")
    sub_and_flag(r"Ѣ", "Е", "yat")
    sub_and_flag(r"і", "и", "i")
    sub_and_flag(r"І", "И", "i")
    sub_and_flag(r"ѳ", "ф", "fita")
    sub_and_flag(r"Ѳ", "Ф", "fita")
    sub_and_flag(r"ѵ", "и", "izhitsa")
    sub_and_flag(r"Ѵ", "И", "izhitsa")

    # Latin-to-Cyr in mixed tokens
    token_re = re.compile(r"[A-Za-z\u0400-\u04FF]+(?:-[A-Za-z\u0400-\u04FF]+)*")
    def fix_token(m):
        tok = m.group(0)
        has_cyr = re.search(r"[\u0400-\u04FF]", tok)
        has_lat = re.search(r"[A-Za-z]", tok)
        if not (has_cyr and has_lat):
            return tok
        new = []
        for ch in tok:
            if ch in LAT_TO_CYR:
                new.append(mark(ch, LAT_TO_CYR[ch], LAT_TO_CYR[ch]))
                flags.append({"type": "latin_to_cyr", "from": ch, "to": LAT_TO_CYR[ch]})
            else:
                new.append(ch)
        return "".join(new)
    text = token_re.sub(fix_token, text)

    return text, flags


def normalize_punct(text: str):
    # Em dash spacing: unify and ensure spaces on both sides
    text = text.replace("–", "—")
    text = re.sub(r"\s*—\s*", " — ", text)
    # Replace ... with …, normalize dashes and spacing
    text = re.sub(r"(?<!\.)\.\.\.(?!\.)", "…", text)
    text = re.sub(r"(?<=\S)\s-\s(?=\S)", " — ", text)
    text = re.sub(r"(?<=\s)--(?=\s)", " — ", text)
    text = re.sub(r"(?<=\S)\s–\s(?=\S)", " — ", text)
    text = re.sub(r"\s+([,.:;?!…»)])", r"\1", text)
    text = re.sub(r"([\.;:?!…])(?=[А-Яа-яЁё])", r"\1 ", text)
    text = re.sub(r"(?<!\d),(?=[А-Яа-яЁё])", ", ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Straight quotes to «…» and „…“ (simple)
    text = re.sub(r'"([^\"]+)"', r'«\1»', text)
    text = re.sub(r"'([^']+)'", r'„\1“', text)
    return text.strip()


def normalize_linebreaks(text: str) -> str:
    # Unify newlines
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    # Remove zero-width and soft hyphen
    t = re.sub(r"[\u200B\u200C\u200D\u00AD]", "", t)
    # Dehyphenate endings across newlines: hyphen-like before newline + letter after
    t = re.sub(r"([\wА-Яа-яЁё])[-‑–—]\n(?=[\wА-Яа-яЁё])", r"\1", t)
    # Replace remaining single newlines inside block with spaces
    t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)
    # Collapse spaces
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


_END_SENTENCE_RE = re.compile(r"[\.!?…](?:[»”\)\]\"])?\s*$")


def _join_continuation(prev: str, nxt: str) -> str:
    # If prev ends with hyphen-like and next starts with letter, dehyphenate
    if re.search(r"[-‑–—]\s*$", prev) and re.match(r"^[A-Za-zА-Яа-яЁё]", nxt or ""):
        return re.sub(r"[-‑–—]\s*$", "", prev) + nxt
    # Otherwise join with a space
    sep = "" if prev.endswith(" ") or (nxt or "").startswith((" ", ",", ".", ";", ":", "!", "?", "…", "»", ")", "]")) else " "
    return prev + sep + nxt


def merge_paragraph_blocks(blocks):
    merged = []
    buf = None
    for b in blocks:
        role = b.get("role")
        text = normalize_linebreaks(b.get("text") or "")
        if role == "heading":
            if buf is not None:
                merged.append({"role": "paragraph", "text": buf})
                buf = None
            merged.append({"role": "heading", "text": text})
            continue
        if role == "verse":
            if buf is not None:
                merged.append({"role": "paragraph", "text": buf})
                buf = None
            merged.append({"role": "verse", "text": b.get("text") or ""})
            continue
        # paragraph
        if buf is None:
            buf = text
        else:
            buf = _join_continuation(buf, text)
        if _END_SENTENCE_RE.search(text):
            merged.append({"role": "paragraph", "text": buf})
            buf = None
    if buf is not None:
        merged.append({"role": "paragraph", "text": buf})
    return merged


def render_html(blocks, title: str):
    from html import escape as esc
    head = (
        "<!doctype html>\n<html lang=\"ru\">\n<head>\n"
        "<meta charset=\"utf-8\"/>\n"
        f"<title>{esc(title)}</title>\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>\n"
        "<style>body{font:18px/1.6 Georgia,Times,\"Times New Roman\",serif;margin:2rem;max-width:48rem;color:#111;background:#fff} h2{font-size:1.15em;margin:1.2rem 0 .6rem} p{margin:0 0 1rem} mark.flag{background:#fff3cd;padding:0 .1em;border-radius:.15em}</style>\n"
        "</head>\n<body contenteditable=\"true\" spellcheck=\"true\">\n"
    )
    body = []
    for b in blocks:
        t = b["text"] or ""
        if b["role"] == "heading":
            body.append(f"<h2>{t}</h2>")
        elif b["role"] == "verse":
            from html import escape as _esc
            stanzas = _esc(t).split("\n\n")
            for stanza in stanzas:
                lines = stanza.split("\n")
                body.append('<div class="stanza"><p>' + "<br/>".join(lines) + "</p></div>")
        else:
            body.append(f"<p>{t}</p>")
    return head + "\n".join(body) + "\n</body>\n</html>\n"


def main():
    ap = argparse.ArgumentParser(description="Modernize structured text; flag ambiguous letter changes; output HTML/TXT and flags.")
    ap.add_argument("--in", dest="inp", default="output_vol2/structured_rules.json", help="Structured JSON after rules")
    ap.add_argument("--outdir", default="output_vol2", help="Output directory")
    ap.add_argument("--title", default="Книга (современная орфография)", help="HTML title")
    args = ap.parse_args()

    data = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    blocks = data.get("blocks", [])
    footnotes = data.get("footnotes", [])

    # Normalize footnote texts
    for fn in footnotes:
        fn["text"] = normalize_punct(fn.get("text") or "")

    norm_blocks = []
    for b in blocks:
        txt = b.get("text") or ""
        if b.get("role") == "verse":
            out_lines = [normalize_punct(ln) for ln in txt.split("\n")]
            txt = "\n".join(out_lines)
        else:
            txt = normalize_linebreaks(txt)
            txt = normalize_punct(txt)
        norm_blocks.append({"role": b.get("role"), "text": txt, "page": b.get("page")})

    # 2) Merge paragraph blocks to avoid mid‑sentence breaks
    merged_blocks = merge_paragraph_blocks(norm_blocks)

    # 3) Apply letter flags on merged blocks
    flags_all = []
    new_blocks = []
    for i, b in enumerate(merged_blocks):
        txt_flagged, flags = apply_letter_flags(b.get("text") or "")
        flags_all.append({"block": i, "role": b.get("role"), "flags": flags})
        new_blocks.append({"role": b.get("role"), "text": txt_flagged})

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Write HTML and TXT
    html = render_html(new_blocks, args.title)
    Path(outdir / "final.html").write_text(html, encoding="utf-8")
    txt_plain = "\n\n".join(re.sub(r"<[^>]+>", "", b["text"]) for b in new_blocks)
    Path(outdir / "final.txt").write_text(txt_plain, encoding="utf-8")

    # Structured JSON with roles and footnotes preserved (for EPUB generation)
    has_verse = any(b.get("role") == "verse" for b in new_blocks)
    if has_verse or footnotes:
        struct_out = {"file": data.get("file", ""), "blocks": new_blocks}
        if footnotes:
            struct_out["footnotes"] = footnotes
        Path(outdir / "final_structured.json").write_text(
            json.dumps(struct_out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if footnotes:
        Path(outdir / "footnotes.json").write_text(
            json.dumps(footnotes, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Flags
    Path(outdir / "flags.json").write_text(json.dumps(flags_all, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved: final.html, final.txt, flags.json in", outdir)


if __name__ == "__main__":
    main()

