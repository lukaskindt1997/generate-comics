#!/usr/bin/env python3
"""Milestone B3: mechanical cleanup of raw Vision OCR output.

For each page in source/manifest.yaml:
  1. keep only the main page's lines (drop facing page, page number)
  2. sort into reading order
  3. rebuild paragraphs from indents
  4. join words hyphenated across lines (only when the joined word is a real word)
  5. normalise quotes to the book's single quotes

No AI involved: these are deterministic rules. Finding likely errors is done by
check_text.py; fixing them is a review step.

Usage:  python3 scripts/clean_ocr.py
Output: outputs/text/<photo>.clean.txt, <photo>.lines.json (line positions), prologue.clean.txt
"""
import json
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source"
OCR_DIR = ROOT / "outputs" / "ocr"
OUT_DIR = ROOT / "outputs" / "text"

INDENT_WEAK = 0.005     # small indent: counts only if the previous line also ended short
INDENT_STRONG = 0.03    # large indent: a new paragraph on its own
SHORT_LINE = 0.06       # previous line ends this far before the local right margin -> paragraph end
LONG_LINE_CHARS = 30    # lines this long are certainly body text; used to find the text column
MARGIN_SLACK = 0.04     # how far outside the body column a line may start


# ---------- manifest (tiny parser: avoids a YAML dependency) ----------

def read_manifest(path):
    pages = []
    for m in re.finditer(r"\{\s*page:\s*(\d+)\s*,\s*photo:\s*([^\s}]+)\s*\}", path.read_text()):
        pages.append({"page": int(m.group(1)), "photo": m.group(2)})
    return pages


# ---------- dictionary ----------

def load_words():
    words = set()
    for name in ("words", "web2", "web2a", "propernames"):
        p = Path("/usr/share/dict") / name
        if p.exists():
            words.update(w.strip().lower() for w in p.read_text(errors="ignore").split())
    names_file = SOURCE / "names.txt"
    if names_file.exists():
        for line in names_file.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            words.update(part.lower() for part in re.split(r"[\s-]+", line) if part)
    return words


SUFFIXES = ("'s", "s'", "ies", "es", "s", "ed", "d", "ing", "ly", "er", "est", "ness", "'d", "'ll")


def is_known(word, words, _depth=0):
    w = word.lower().strip("'")
    w = w.translate(str.maketrans("éèêëàâäôöûüçïî", "eeeeaaaoouucii"))
    if w.endswith("n't"):
        w = w[:-3]
        if w in ("wo", "ca", "sha"):  # won't, can't, shan't
            return True
    if not w or not re.search(r"[a-z]", w) or w in words:
        return True
    if w.isdigit() or re.fullmatch(r"\d+(st|nd|rd|th)", w):
        return True
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) > len(suf) + 2:
            stem = w[: -len(suf)]
            if stem in words or stem + "e" in words or (suf == "ies" and stem + "y" in words):
                return True
            if len(stem) > 2 and stem[-1] == stem[-2] and stem[:-1] in words:  # stopped, grinned
                return True
    # British spellings: harbour/harbor, armour/armor, colour, realise
    for a, b in (("our", "or"), ("oul", "ol"), ("ise", "ize"), ("re", "er"), ("ll", "l")):
        if a in w and w.replace(a, b) in words:
            return True
    # Compounds: longsword, mainland, armspans. First part must be a dictionary word
    # of 3+ letters, second part 4+ letters (stricter, to avoid "sta"+"ree").
    if _depth == 0 and "'" not in w and "-" not in w:
        for k in range(3, len(w) - 3):
            if w[:k] in words and is_known(w[k:], words, 1):
                return True
    if "'" in w:  # contractions: he'd, they're, Hood's
        return all(is_known(part, words) or part in ("s", "d", "ll", "re", "ve", "t", "m")
                   for part in w.split("'") if part)
    if "-" in w:
        return all(is_known(part, words) for part in w.split("-") if part)
    return False


# ---------- geometry ----------

def main_column(lines):
    """Return the lines belonging to the main page (not facing page, not page number)."""
    long = [l for l in lines if len(l["text"]) >= LONG_LINE_CHARS]
    if not long:
        return lines
    # Anchor lines: long lines near the typical start position and angle. This rejects
    # the occasional long facing-page line, which sits far away or at a different angle.
    med_left = statistics.median(l["leftX"] for l in long)
    med_angle = statistics.median(l["angleDegrees"] for l in long)
    anchors = [l for l in long
               if abs(l["leftX"] - med_left) < 0.25 and abs(l["angleDegrees"] - med_angle) < 8]
    lo = min(l["leftX"] for l in anchors) - MARGIN_SLACK
    hi = max(l["leftX"] for l in anchors) + MARGIN_SLACK
    col_left = statistics.median(l["leftX"] for l in anchors)
    col_right = statistics.median(l["x"] + l["w"] for l in anchors)
    kept = []
    for l in lines:
        if not (lo <= l["leftX"] <= hi):
            continue
        # The line's centre must lie inside the text column (rejects facing-page
        # fragments that sit just beside it).
        centre = l["x"] + l["w"] / 2
        if not (col_left <= centre <= col_right):
            continue
        if re.fullmatch(r"\s*\d{1,4}\s*", l["text"]):  # page number
            continue
        kept.append(l)
    return kept


def mark_indents(lines):
    """Mark lines that start a new paragraph.

    Signals: the line starts right of its neighbours' left margin (indent), and the
    previous line stops short of the right margin. Tilted pages make the indent
    alone too noisy, so a small indent only counts together with a short previous line.
    """
    n = len(lines)
    for l in lines:
        l["right"] = l["x"] + l["w"]
    for i, l in enumerate(lines):
        idx = [j for j in range(max(0, i - 4), min(n, i + 5)) if j != i]
        margin = min((lines[j]["leftX"] for j in idx), default=l["leftX"])
        indent = l["leftX"] - margin
        prev_short = False
        if i > 0:
            p = lines[i - 1]
            pidx = [j for j in range(max(0, i - 5), min(n, i + 4)) if j != i - 1]
            right_margin = max((lines[j]["right"] for j in pidx), default=p["right"])
            prev_short = (right_margin - p["right"]) >= SHORT_LINE and not p["text"].rstrip().endswith("-")
        l["indent"] = round(indent, 4)
        l["prev_short"] = prev_short
        l["indented"] = indent >= INDENT_STRONG or (prev_short and indent >= INDENT_WEAK)


# ---------- text ----------

def normalise_quotes(text):
    # The book uses single quotes only; OCR often reads them as " or *.
    text = re.sub(r"[\"“”*]", "'", text)
    text = text.replace("‘", "'").replace("’", "'")
    return text


def join_lines(lines, words):
    """Return paragraphs (list of str) plus a log of hyphenation decisions."""
    paragraphs, current, log = [], "", []
    prev = None
    for l in lines:
        text = normalise_quotes(l["text"].strip())
        if l["indented"] and current:
            paragraphs.append(current)
            current = ""
        l["para"] = len(paragraphs)
        prev = l
        if not current:
            current = text
            continue
        m = re.search(r"(\w+)-$", current)
        if m:
            head = m.group(1)
            tail_m = re.match(r"(\w+)", text)
            tail = tail_m.group(1) if tail_m else ""
            if tail and is_known(head + tail, words) and not is_known(head + "-" + tail, words) \
                    or (tail and is_known(head + tail, words) and tail[0].islower()
                        and not (is_known(head, words) and is_known(tail, words))):
                current = current[:-1] + text          # mis-/shapen -> misshapen
                log.append({"joined": head + tail})
            else:
                current = current + text                # smoke-/filled -> smoke-filled
                log.append({"kept_hyphen": head + "-" + tail})
        else:
            current = current + " " + text
    if current:
        paragraphs.append(current)
    mark_cut_off(lines)
    return paragraphs, log


CUT_OFF_RATIO = 0.88  # a continuing line with fewer characters than this x a full line


def mark_cut_off(lines):
    """Flag lines whose end was probably lost (page curve, blur near the spine).

    Justified text: every line that continues into the next one is about equally long
    in characters. A continuing line that is clearly shorter has lost its end.
    """
    continuing = [l for l, nxt in zip(lines, lines[1:]) if nxt["para"] == l["para"]]
    if len(continuing) < 5:
        return
    # 80th percentile, not median: on a badly curved page many lines are cut off.
    lengths = sorted(len(l["text"]) for l in continuing)
    full = lengths[int(0.8 * (len(lengths) - 1))]
    for l in continuing:
        t = l["text"].rstrip()
        l["cut_off"] = len(t) < CUT_OFF_RATIO * full and not t.endswith("-")


def process_page(entry, words, ocr_kind="vision"):
    data = json.loads((OCR_DIR / f"{Path(entry['photo']).stem}.{ocr_kind}.json").read_text())
    lines = main_column(data["lines"])
    lines.sort(key=lambda l: l["leftY"])
    mark_indents(lines)
    paragraphs, hyph_log = join_lines(lines, words)
    return {
        "page": entry["page"],
        "photo": entry["photo"],
        "lines_in": len(data["lines"]),
        "lines_kept": len(lines),
        "starts_with_indent": bool(lines and lines[0]["indented"]),
        "paragraphs": paragraphs,
        "hyphenation": hyph_log,
        "lines": [{"text": l["text"], "para": l["para"], "cut_off": l.get("cut_off", False),
                   "x": l["x"], "y": l["y"], "w": l["w"], "h": l["h"]} for l in lines],
    }


def main():
    words = load_words()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    combined = []
    for entry in read_manifest(SOURCE / "manifest.yaml"):
        r = process_page(entry, words)
        stem = Path(entry["photo"]).stem
        (OUT_DIR / f"{stem}.clean.txt").write_text("\n\n".join(r["paragraphs"]) + "\n")
        (OUT_DIR / f"{stem}.lines.json").write_text(json.dumps(
            {k: r[k] for k in ("page", "photo", "lines_in", "lines_kept", "starts_with_indent",
                               "hyphenation", "lines")},
            indent=2, ensure_ascii=False))
        # A page that doesn't start with an indent continues the previous paragraph.
        paras = list(r["paragraphs"])
        if combined and paras and not r["starts_with_indent"]:
            combined[-1] += f" ⟨p{r['page']}⟩ " + paras.pop(0)
        elif paras:
            paras[0] = f"⟨p{r['page']}⟩ " + paras[0]
        combined.extend(paras)
        print(f"p{r['page']} ({entry['photo']}): {r['lines_in']} lines -> {r['lines_kept']} kept, "
              f"{len(r['paragraphs'])} paragraphs")
    (OUT_DIR / "prologue.clean.txt").write_text("\n\n".join(combined) + "\n")
    print(f"-> {OUT_DIR}")


if __name__ == "__main__":
    main()
