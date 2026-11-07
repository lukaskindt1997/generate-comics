#!/usr/bin/env python3
"""Milestone B: find likely transcription errors (local, deterministic).

Runs after clean_ocr.py. Each check produces flags; nothing is changed in the text.

Checks:
  unknown      word not in the dictionary or names list
  name         word is one OCR letter-confusion away from a known name (Canoes -> Ganoes)
  suspect      real word that is a classic OCR misread (bum -> burn, dean -> clean)
  quotes       missing opening/closing quote (the book uses single quotes)
  first_letter first paragraph starts lowercase: the drop cap was probably lost
  disagree     the two Vision readings (language correction on/off) differ

Usage:  python3 scripts/check_text.py
Output: outputs/text/<photo>.checks.json
"""
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clean_ocr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source"
OCR_DIR = ROOT / "outputs" / "ocr"
TEXT_DIR = ROOT / "outputs" / "text"

# Letter shapes OCR commonly confuses (both directions are tried).
CONFUSIONS = [("rn", "m"), ("cl", "d"), ("C", "G"), ("li", "h"), ("ii", "n"), ("vv", "w"),
              ("I", "l"), ("c", "e"), ("u", "n"), ("b", "h"), ("f", "t"), ("0", "O"),
              ("5", "s"), ("1", "l")]

# Real words that are usually misreads in prose. word -> likely intended word.
SUSPECT_WORDS = {"bum": "burn", "bums": "burns", "dean": "clean", "modem": "modern",
                 "tum": "turn", "bom": "born", "arid": "and", "tbe": "the", "cf": "of",
                 "wim": "with", "chem": "them", "lie": "he", "tlie": "the"}
# Word pairs that are usually misreads. (first, second) -> suggestion.
SUSPECT_PAIRS = {("it", w): f"if {w}" for w in ("you", "I", "he", "she", "they", "we", "it")}

WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9'\-]*")


def load_names():
    names = set()
    f = SOURCE / "names.txt"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            for part in re.split(r"[\s\-]+", line):
                if part and part[0].isupper():
                    names.add(part.removesuffix("'s"))
    return names


def variants(word):
    out = set()
    for a, b in CONFUSIONS:
        for x, y in ((a, b), (b, a)):
            start = 0
            while (i := word.find(x, start)) != -1:
                out.add(word[:i] + y + word[i + len(x):])
                start = i + 1
    return out


def check_words(paragraphs, words, names):
    flags = []
    for pi, para in enumerate(paragraphs):
        tokens = list(WORD_RE.finditer(para))
        for ti, m in enumerate(tokens):
            raw = m.group(0).rstrip("'-")
            base = raw.removesuffix("'s").removesuffix("'")
            ctx = para[max(0, m.start() - 35): m.end() + 35]
            near_name = sorted(v for v in variants(base) if v in names) if base not in names else []
            if near_name:
                flags.append({"type": "name", "paragraph": pi, "word": raw,
                              "suggestion": raw.replace(base, near_name[0], 1), "context": ctx})
            elif raw.lower() in SUSPECT_WORDS:
                flags.append({"type": "suspect", "paragraph": pi, "word": raw,
                              "suggestion": SUSPECT_WORDS[raw.lower()], "context": ctx})
            elif not clean_ocr.is_known(raw, words):
                # Suggest only plain dictionary words that differ in more than case.
                known = sorted({v.lower() for v in variants(raw)
                                if v.lower() != raw.lower() and v.lower().strip("'") in words})
                sugg = known[0] if known else None
                if sugg and raw[0].isupper():
                    sugg = sugg[0].upper() + sugg[1:]
                flags.append({"type": "unknown", "paragraph": pi, "word": raw,
                              "suggestion": sugg, "context": ctx})
            if ti + 1 < len(tokens):
                pair = (raw.lower(), tokens[ti + 1].group(0))
                if pair in SUSPECT_PAIRS:
                    flags.append({"type": "suspect", "paragraph": pi,
                                  "word": f"{raw} {tokens[ti + 1].group(0)}",
                                  "suggestion": SUSPECT_PAIRS[pair], "context": ctx})
    return flags


def check_quotes(paragraphs):
    """Single-quote state machine per paragraph.

    Opening ' : at paragraph start or after space/dash, followed by a letter.
    Closing ' : after punctuation, followed by space/end/punctuation.
    Apostrophes inside or at the end of words are ignored.
    A paragraph may end inside a quote only if the next paragraph opens one
    (multi-paragraph speech).
    """
    flags = []
    for pi, para in enumerate(paragraphs):
        inside = False
        for i, ch in enumerate(para):
            if ch != "'":
                continue
            prev = para[i - 1] if i > 0 else " "
            nxt = para[i + 1] if i + 1 < len(para) else " "
            ctx = para[max(0, i - 35): i + 35]
            if prev in " –—-(" and (nxt.isalnum() or nxt in "…."):
                if inside:
                    flags.append({"type": "quotes", "paragraph": pi, "word": None,
                                  "suggestion": "missing closing quote before this point",
                                  "context": ctx})
                inside = True
            elif prev in ".,?!–—-…" and (nxt in " .,;:?!–—-…" or i + 1 == len(para)):
                if not inside:
                    flags.append({"type": "quotes", "paragraph": pi, "word": None,
                                  "suggestion": "closing quote without an opening quote",
                                  "context": ctx})
                inside = False
        if inside:
            nxt_para = paragraphs[pi + 1] if pi + 1 < len(paragraphs) else ""
            if not nxt_para.startswith("'"):
                flags.append({"type": "quotes", "paragraph": pi, "word": None,
                              "suggestion": "quote never closed in this paragraph",
                              "context": para[-70:]})
    return flags


def check_first_letter(paragraphs):
    for pi, para in enumerate(paragraphs[:4]):
        first = para.lstrip("'")[:1]
        if first.islower():
            return [{"type": "first_letter", "paragraph": pi, "word": para.split()[0],
                     "suggestion": "large first letter (drop cap) probably missing",
                     "context": para[:70]}]
    return []


def check_cut_off(lines):
    return [{"type": "cut_off", "paragraph": l["para"], "word": l["text"].split()[-1],
             "suggestion": "line end may be lost (weak signal)", "context": l["text"][-70:]}
            for l in lines if l.get("cut_off")]


def check_disagreement(entry, words, paragraphs, existing):
    """Where the two readings differ, add the other reading to an existing flag, or
    create a new flag unless the main reading is a real word and the other isn't."""
    stem = Path(entry["photo"]).stem
    if not (OCR_DIR / f"{stem}.vision-raw.json").exists():
        return []
    other = clean_ocr.process_page(entry, words, ocr_kind="vision-raw")["paragraphs"]
    a = WORD_RE.findall(" ".join(paragraphs))
    b = WORD_RE.findall(" ".join(other))
    flags = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        wa, wb = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if wa.strip("'") == wb.strip("'"):
            continue
        ctx = " ".join(a[max(0, i1 - 5): i2 + 5])
        same = [f for f in existing if f["word"] and f["word"].split()[0] in wa.split()]
        if same:
            for f in same:
                f["other_reading"] = wb
            continue
        main_ok = all(clean_ocr.is_known(w, words) for w in wa.split()) if wa else False
        other_ok = all(clean_ocr.is_known(w, words) for w in wb.split()) if wb else False
        if main_ok and not other_ok:
            continue
        flags.append({"type": "disagree", "paragraph": None, "word": wa or "(nothing)",
                      "suggestion": f"other reading: {wb or '(nothing)'}", "context": ctx})
    return flags


def locate(flag, lines):
    """Attach the photo line (box) where the flagged text sits, for review crops."""
    probe = flag["word"] or ""
    if not probe:
        probe = flag["context"].strip()[:20]
    probe = probe.split()[0] if probe else ""
    candidates = [l for l in lines if flag["paragraph"] is None or l["para"] == flag["paragraph"]]
    for l in candidates:
        if probe and probe.strip("'") in clean_ocr.normalise_quotes(l["text"]):
            flag["line"] = {k: round(l[k], 4) for k in ("x", "y", "w", "h")}
            flag["line_text"] = l["text"]
            return flag
    flag["line"] = None
    return flag


def main():
    words = clean_ocr.load_words()
    names = load_names()
    summary = {}
    for entry in clean_ocr.read_manifest(SOURCE / "manifest.yaml"):
        stem = Path(entry["photo"]).stem
        paragraphs = [p for p in (TEXT_DIR / f"{stem}.clean.txt").read_text().split("\n\n") if p.strip()]
        lines = json.loads((TEXT_DIR / f"{stem}.lines.json").read_text())["lines"]
        first_page = entry == clean_ocr.read_manifest(SOURCE / "manifest.yaml")[0]
        flags = (check_words(paragraphs, words, names) + check_quotes(paragraphs)
                 + (check_first_letter(paragraphs) if first_page else [])
                 )  # cut-off check dropped: too many false alarms, real cases are caught as unknown words
        flags += check_disagreement(entry, words, paragraphs, flags)
        flags = [locate(f, lines) for f in flags]
        (TEXT_DIR / f"{stem}.checks.json").write_text(json.dumps(
            {"page": entry["page"], "photo": entry["photo"], "flags": flags}, indent=2, ensure_ascii=False))
        counts = {}
        for f in flags:
            counts[f["type"]] = counts.get(f["type"], 0) + 1
        summary[stem] = counts
        print(f"p{entry['page']} ({entry['photo']}): {len(flags)} flags {counts}")


if __name__ == "__main__":
    main()
