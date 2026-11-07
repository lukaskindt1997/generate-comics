#!/usr/bin/env python3
"""Milestone C: chapter brief. Claude reads a whole chapter once, before any page is planned.

Collects what is revealed anywhere in the chapter (appearance, ages, clothing, places,
time of day...) with the page where it is revealed, so page analyses can use facts that
only appear later. Plot reveals are listed separately with "don't hint before page N".

Uses reviewed text where a page is finished, otherwise the cleaned OCR text (a few
misread words don't matter for extracting facts).

Usage:  python3 scripts/analyze_chapter.py [--model MODEL]
Output: outputs/comic/chapter-<section>/brief.json (+ brief.meta.json)
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clean_ocr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source"
TEXT = ROOT / "outputs" / "text"
COMIC = ROOT / "outputs" / "comic"

FACT = {"type": "object", "properties": {
    "fact": {"type": "string"},
    "kind": {"type": "string", "enum": ["appearance", "age", "clothing", "equipment", "injury",
                                        "role", "relationship", "personality", "other"]},
    "page": {"type": "integer", "description": "Book page where this is revealed."}},
    "required": ["fact", "kind", "page"]}
PLACE_FACT = {"type": "object", "properties": {
    "fact": {"type": "string"}, "page": {"type": "integer"}}, "required": ["fact", "page"]}

SCHEMA = {
    "type": "object",
    "properties": {
        "page_summaries": {"type": "array", "items": {"type": "object", "properties": {
            "page": {"type": "integer"}, "summary": {"type": "string"}},
            "required": ["page", "summary"]}},
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "description": "snake_case, e.g. ganoes_paran"},
            "name": {"type": "string", "description": "Name, or a description if unnamed (e.g. 'the commander')."},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "on_pages": {"type": "array", "items": {"type": "integer"},
                         "description": "Pages where the character is physically present."},
            "age_category": {"type": "string", "enum": ["child", "teen", "adult", "unknown"]},
            "facts": {"type": "array", "items": FACT}},
            "required": ["id", "name", "aliases", "on_pages", "age_category", "facts"]}},
        "locations": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "facts": {"type": "array", "items": PLACE_FACT}},
            "required": ["id", "name", "facts"]}},
        "objects": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "facts": {"type": "array", "items": PLACE_FACT}},
            "required": ["id", "name", "facts"]}},
        "timeline": {"type": "array", "items": PLACE_FACT,
                     "description": "Time of day, weather, events in the background (e.g. riots)."},
        "reveals": {"type": "array", "items": {"type": "object", "properties": {
            "fact": {"type": "string"},
            "revealed_on_page": {"type": "integer"},
            "note": {"type": "string", "description": "What earlier pages must not show or hint at."}},
            "required": ["fact", "revealed_on_page", "note"]},
            "description": "Plot information the reader learns at a specific moment (identities, secrets)."},
        "ocr_doubts": {"type": "array", "items": {"type": "string"},
                       "description": "Spots where garbled OCR text makes a fact uncertain."},
    },
    "required": ["page_summaries", "characters", "locations", "objects", "timeline", "reveals", "ocr_doubts"],
}

PROMPT = """You are the story analyst for a personal comic adaptation of a fantasy novel.
Read this whole chapter ({section}) and write a chapter brief as JSON. Later, each book page
becomes one comic page; the brief lets those pages use facts that are only revealed later.

Rules:
- Use only this text. Do not add anything from your memory of the book or its sequels.
- Every fact gets the book page where it is revealed.
- Separate two kinds of information:
  * Visual/descriptive facts (appearance, age, clothing, time of day, weather): these are
    true from the start and may be drawn on earlier pages too.
  * Plot reveals (identities, secrets, surprises): list them under "reveals" with the page
    where the reader learns them and what earlier pages must not show or hint at.
- Characters: include unnamed ones (e.g. "the commander") with a stable id. Note aliases.
- Some pages are raw OCR and contain misread words (e.g. "Canoes" for "Ganoes",
  "Bum's" for "Burn's"). Read through them sensibly; list real doubts in "ocr_doubts".
- Keep facts short. Do not copy long passages.

{text}
"""


def page_text(entry):
    stem = Path(entry["photo"]).stem
    reviewed = TEXT / "reviewed" / f"p{entry['page']}.md"
    status_file = TEXT / "reviewed" / "status.json"
    status = json.loads(status_file.read_text()) if status_file.exists() else {}
    if status.get(str(entry["page"]), {}).get("finished") and reviewed.exists():
        return "reviewed", reviewed.read_text()
    return "ocr", (TEXT / f"{stem}.clean.txt").read_text()


def section_slug():
    m = re.search(r"^section:\s*(.+)$", (SOURCE / "manifest.yaml").read_text(), re.M)
    name = m.group(1).strip() if m else "chapter"
    return name, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    section, slug = section_slug()
    parts, sources = [], {}
    for entry in clean_ocr.read_manifest(SOURCE / "manifest.yaml"):
        kind, text = page_text(entry)
        sources[entry["page"]] = kind
        label = "reviewed text" if kind == "reviewed" else "raw OCR, may contain misreads"
        parts.append(f"=== Book page {entry['page']} ({label}) ===\n{text.strip()}")
    prompt = PROMPT.format(section=section, text="\n\n".join(parts))

    cmd = ["claude", "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--disallowedTools", "Bash", "Edit", "Write", "Read", "WebFetch", "WebSearch",
           "--no-session-persistence"]
    if args.model:
        cmd += ["--model", args.model]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800)
    elapsed = round(time.time() - t0, 1)
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"claude failed ({proc.returncode}): {proc.stderr[-1500:]} {proc.stdout[-1500:]}")
    if raw.get("is_error") or "structured_output" not in raw:
        sys.exit(f"claude returned an error: {raw.get('result')}")

    folder = COMIC / f"chapter-{slug}"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "brief.json"
    if out.exists():
        out.rename(folder / f"brief.{datetime.now():%Y%m%d-%H%M%S}.json")
    brief = raw["structured_output"]
    brief["section"] = section
    brief["page_sources"] = sources
    out.write_text(json.dumps(brief, indent=2, ensure_ascii=False))
    (folder / "brief.meta.json").write_text(json.dumps({
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "engine": "claude-cli", "models": list((raw.get("modelUsage") or {}).keys()),
        "elapsed_seconds": elapsed, "cost_usd_list_price": raw.get("total_cost_usd"),
        "page_sources": sources}, indent=2))
    print(f"chapter brief '{section}': {len(brief['characters'])} characters, "
          f"{len(brief['locations'])} locations, {len(brief['reveals'])} reveals ({elapsed}s) -> {out}")


if __name__ == "__main__":
    main()
