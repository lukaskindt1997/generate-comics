#!/usr/bin/env python3
"""Adopt a candidate's look into the description.

When a candidate fits the book but differs from free choices in the description (hair
colour, clothing colour...), Claude looks at the image and rewrites only those free choices
so description and image agree. Book facts and user decisions are kept, even if the image
contradicts them. The old description is kept in description_history.

Usage:  python3 scripts/adopt_candidate.py characters/the_commander 20261108-023131_seed1570312246.png
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_cli  # noqa: E402
from assess_reference import chapter_context, decisions_context  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"

SCHEMA = {"type": "object", "properties": {
    "description": {"type": "string"},
    "changes": {"type": "array", "items": {"type": "string"},
                "description": "Each change made, e.g. 'hair: dark brown -> black'."},
    "kept_despite_image": {"type": "array", "items": {"type": "string"},
                           "description": "Book facts/decisions the image contradicts, kept in the description."}},
    "required": ["description", "changes", "kept_despite_image"]}

PROMPT = """Update a reference description of a {kind} so it matches the chosen reference image.

Read the image with the Read tool: {image}

Rules:
- BOOK FACTS and USER DECISIONS below are binding: keep them, even if the image differs.
  List such points under kept_despite_image.
- Every other detail is a free choice: change it to what the image shows (colours, hair,
  shapes, materials, accessories...). Add clearly visible details that matter for drawing
  the {kind} consistently; drop details the image doesn't have.
- Keep the length (60-110 words) and the style of the current description.

{kind_upper}: {name}
CURRENT DESCRIPTION:
{description}

BOOK FACTS:
{book}

USER DECISIONS:
{decisions}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("candidate")
    args = ap.parse_args()

    folder = LIB / args.ref
    ref_file = folder / "ref.json"
    ref = json.loads(ref_file.read_text())
    img = folder / "candidates" / Path(args.candidate).name
    if not img.exists():
        sys.exit("candidate not found")
    out, meta = claude_cli.ask(PROMPT.format(
        kind=ref["kind"], kind_upper=ref["kind"].upper(), name=ref["name"], description=ref["description"],
        image=img.relative_to(ROOT), book="\n".join(chapter_context(ref)) or "(none)",
        decisions="\n".join(decisions_context(ref)) or "(none)"), SCHEMA, read_files=True)
    ref = json.loads(ref_file.read_text())
    ref.setdefault("description_history", []).append(ref["description"])
    ref["description"] = out["description"].strip()
    ref.setdefault("adopted_from", []).append({"candidate": img.name, "changes": out["changes"],
                                               "kept_despite_image": out["kept_despite_image"]})
    ref_file.write_text(json.dumps(ref, indent=2, ensure_ascii=False))
    print(json.dumps({"changes": out["changes"], "kept_despite_image": out["kept_despite_image"]}))


if __name__ == "__main__":
    main()
