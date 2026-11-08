#!/usr/bin/env python3
"""Create a new reference entry: Claude writes the description from the story context.

Context: chapter-brief facts for this id/name, the user's decisions, and how page plans use
it. Creates library/<kind>s/<id>/ref.json (never overwrites an existing one). Generating
candidates is a separate step (make_reference.py / References view).

Usage:  python3 scripts/describe_reference.py character the_commander "the commander"
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_cli  # noqa: E402
from assess_reference import chapter_context, decisions_context, plan_context  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
KIND_DIR = {"character": "characters", "object": "objects", "location": "locations"}

SCHEMA = {"type": "object", "properties": {
    "description": {"type": "string"},
    "age_category": {"type": "string", "enum": ["child", "teen", "adult", "unknown", "n/a"]},
    "open_points": {"type": "array", "items": {"type": "string"},
                    "description": "Visual details the book doesn't give and you had to choose."}},
    "required": ["description", "age_category", "open_points"]}

PROMPT = """You are the continuity supervisor for a personal comic adaptation of a fantasy novel
(realistic European dark-fantasy style). Write the reference description for a {kind}: the
precise visual description that fixes how it looks in every panel.

- Use the facts from the book and the user's decisions below; they win over anything else.
- Where the book is silent, choose plausible, concrete details that fit the setting and the
  story, and list those choices under open_points so the user can check them.
- Describe only what is visible: build, face, hair, clothing, equipment, colours, materials,
  shapes, size. 60-110 words, in the style of a costume or prop brief.
- Do not use knowledge of later books (no spoilers). Use facts from this chapter only.
{kind_rules}

{kind_upper}: {name} (id {id})
STORY CONTEXT:
{context}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=list(KIND_DIR))
    ap.add_argument("id")
    ap.add_argument("name")
    args = ap.parse_args()

    folder = LIB / KIND_DIR[args.kind] / args.id
    if (folder / "ref.json").exists():
        sys.exit("reference already exists")
    stub = {"id": args.id, "name": args.name, "kind": args.kind}
    context = (chapter_context(stub) + decisions_context(stub) + plan_context(stub)) or ["(no facts found)"]
    print("writing description…", flush=True)
    kind_rules = ""
    if args.kind == "location":
        others = sorted(json.loads(f.read_text())["name"] for f in LIB.glob("*/*/ref.json")
                        if json.loads(f.read_text())["kind"] != "location")
        kind_rules = ("- LOCATION: describe only its PERMANENT look (architecture, materials, colours, layout, "
                      "landmarks). Leave out events and changing states (fires, smoke, riots, magic, weather, "
                      "time of day, ships or crowds that come and go) and any plot information: those belong "
                      "to the individual panel. Leave out characters and objects that have their own reference: "
                      + (", ".join(others) or "(none)") + ".")
    out, meta = claude_cli.ask(PROMPT.format(kind=args.kind, kind_upper=args.kind.upper(), name=args.name,
                                             id=args.id, context="\n".join(context), kind_rules=kind_rules), SCHEMA)
    ref = {"id": args.id, "kind": args.kind, "name": args.name, "description": out["description"].strip(),
           "sources": context, "open_points": out["open_points"],
           "created_utc": datetime.now(timezone.utc).isoformat(), "approved": None}
    if args.kind == "character":
        ref["age_category"] = out["age_category"] if out["age_category"] != "n/a" else "unknown"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ref.json").write_text(json.dumps(ref, indent=2, ensure_ascii=False))
    print(f"created {folder.relative_to(ROOT)} ({meta['elapsed_seconds']}s)", flush=True)


if __name__ == "__main__":
    main()
