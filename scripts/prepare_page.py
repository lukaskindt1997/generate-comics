#!/usr/bin/env python3
"""Milestone C0: assemble the text for one comic page (1 book page = 1 comic page).

Rules:
  - A paragraph that started on the previous book page and was deferred is joined
    with its continuation and put at the start of this page.
  - If this page's last paragraph continues on the next book page, it is NOT used
    here: it is deferred to the next page (outputs/comic/carryover/to_p<N+1>.md).
    So every paragraph appears whole, on exactly one comic page.

Input:  outputs/text/reviewed/p<N>.md + status.json (from the review UI)
Output: outputs/comic/page-<NNN>/input.json

Usage:  python3 scripts/prepare_page.py 3
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEWED = ROOT / "outputs" / "text" / "reviewed"
COMIC = ROOT / "outputs" / "comic"
CARRY = COMIC / "carryover"

SENTENCE_END = re.compile(r"[.!?…'’\"—–-]\s*$")


def paragraphs(path):
    text = path.read_text()
    return [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=int)
    args = ap.parse_args()
    n = args.page

    status = json.loads((REVIEWED / "status.json").read_text())
    st = status.get(str(n))
    if not st:
        sys.exit(f"Book page {n} not found in the review output.")
    if not st["finished"]:
        sys.exit(f"Book page {n} is not marked finished in the review UI yet.")

    paras = [{"page": n, "text": t} for t in paragraphs(REVIEWED / st["file"])]

    # 1. Paragraph carried over from the previous page.
    carry_in = CARRY / f"to_p{n}.md"
    carried = None
    if carry_in.exists():
        carried = carry_in.read_text().strip()
        if st["continues_previous"] and paras:
            paras[0] = {"page": f"{n - 1}-{n}", "text": carried + " " + paras[0]["text"]}
        else:  # previous page said "continues", this page says it doesn't: keep both, flag it
            paras.insert(0, {"page": n - 1, "text": carried})
            print("warning: carried-over paragraph but this page does not continue it; check the review")

    # 2. Does the last paragraph continue on the next page?
    nxt = status.get(str(n + 1))
    continues = bool(nxt and nxt["continues_previous"]) or (paras and not SENTENCE_END.search(paras[-1]["text"]))
    deferred = None
    if continues and paras:
        deferred = paras.pop()
        CARRY.mkdir(parents=True, exist_ok=True)
        (CARRY / f"to_p{n + 1}.md").write_text(deferred["text"] + "\n")
        if not nxt:
            print(f"note: page {n + 1} is not reviewed yet; the deferral is based on punctuation only")

    for i, p in enumerate(paras, 1):
        p["id"] = f"P{i}"
    out = COMIC / f"page-{n:03d}"
    out.mkdir(parents=True, exist_ok=True)
    record = {
        "comic_page": n,
        "book_pages": sorted({str(p["page"]) for p in paras}),
        "paragraphs": [{"id": p["id"], "from_page": p["page"], "text": p["text"]} for p in paras],
        "carried_in": carried,
        "deferred_to_next_page": deferred["text"] if deferred else None,
    }
    (out / "input.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"comic page {n}: {len(paras)} paragraphs"
          + (", 1 carried in" if carried else "")
          + (f", last paragraph deferred to page {n + 1}" if deferred else "")
          + f" -> {out / 'input.json'}")


if __name__ == "__main__":
    main()
