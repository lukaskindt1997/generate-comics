#!/usr/bin/env python3
"""Assess the candidates of one panel with Claude (via `claude -p`, reads the images).

Each candidate is checked against
  - the BOOK: the original paragraphs behind the panel's story beats (most important)
  - the PLAN: shot, angle, characters (position, pose, expression), objects, lighting
  - the approved REFERENCES of the characters/objects in the panel (do they look the same?)
  - the TEXT SPACE: is the area for captions/balloons calm enough?
  - technical defects: anatomy, duplicated figures, stray lettering, artefacts
and gets a score 1-5, pros and cons. Per panel: the best candidate.

Usage:  python3 scripts/assess_panel.py 3 4
Output: outputs/comic/page-<NNN>/panels/panel-<n>/assessment.json (older one kept with a timestamp)
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_cli  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMIC = ROOT / "outputs" / "comic"

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string", "description": "File name exactly as given."},
            "score": {"type": "integer", "minimum": 1, "maximum": 5},
            "matches_book": {"type": "boolean"},
            "book_conflicts": {"type": "array", "items": {"type": "string"},
                               "description": "Contradictions with the BOOK passage (serious)."},
            "plan_deviations": {"type": "array", "items": {"type": "string"},
                                "description": "Differences from the PANEL PLAN (shot, angle, pose, position, light...)."},
            "reference_mismatches": {"type": "array", "items": {"type": "string"},
                                     "description": "Where a character/object looks different from its approved reference."},
            "text_space_problems": {"type": "array", "items": {"type": "string"},
                                    "description": "Where the planned caption/balloon area is busy or covers something important."},
            "defects": {"type": "array", "items": {"type": "string"},
                        "description": "Anatomy errors, duplicated figures, stray letters/text, artefacts, style breaks."},
            "pros": {"type": "array", "items": {"type": "string"}},
            "cons": {"type": "array", "items": {"type": "string"}}},
            "required": ["file", "score", "matches_book", "book_conflicts", "plan_deviations",
                         "reference_mismatches", "text_space_problems", "defects", "pros", "cons"]}},
        "best": {"type": ["string", "null"]},
        "best_reason": {"type": "string"},
    },
    "required": ["candidates", "best", "best_reason"],
}

PROMPT = """You are the quality checker for a personal comic adaptation of a fantasy novel
(realistic European dark-fantasy album style). Assess the candidate images for ONE panel so the
user can pick the best one. Read every image with the Read tool, including the reference images.

ORDER OF TRUTH: 1. the BOOK passage, 2. the PANEL PLAN, 3. the approved REFERENCES.
For each candidate check:
1. book_conflicts: anything that contradicts the book passage (serious).
2. plan_deviations: shot size, camera angle, who is where, pose, expression, lighting, objects.
3. reference_mismatches: do characters/objects look like their approved reference images
   (face, hair, clothing, colours, shape)? Only for references listed below.
4. text_space_problems: the caption(s)/balloon(s) will be placed as listed; is that area calm
   enough (sky, wall, shadow) and clear of faces and key details?
5. defects: anatomy (hands, limbs), duplicated or merged figures, stray text or letters, panel
   borders, artefacts, a style that breaks from detailed ink + muted painterly colour.
Score 1-5: 5 = faithful to the book, follows the plan, references recognisable, room for the
text, no defects. Book conflicts and bad defects pull the score down hard; small plan
deviations that still tell the moment well are minor. Short pros and cons.
Then pick the best candidate (or null if none is usable).

BOOK PASSAGE (what this panel shows):
{book}

PANEL PLAN (panel {panel}: {shot}, {angle}):
Purpose: {purpose}
Description: {description}
Characters: {characters}
Objects: {objects}
Lighting: {lighting}
Text on this panel: {text}
Space kept free for text: {text_space}

APPROVED REFERENCES (read these images):
{references}

CANDIDATES (file name: path to read):
{files}
"""


def book_passage(folder, panel):
    """Original paragraphs behind the panel's story beats (via the scene analysis)."""
    scene = json.loads((folder / "scene.json").read_text())
    inp = json.loads((folder / "input.json").read_text())
    paras = {p["id"]: p["text"] for p in inp["paragraphs"]}
    ids = []
    for b in scene["beats"]:
        if b["id"] in panel["beats"]:
            ids += [e for e in b["evidence"] if e not in ids]
    return "\n\n".join(f"[{i}] {paras[i]}" for i in ids if i in paras) or "(no passage found)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=int)
    ap.add_argument("panel", type=int)
    args = ap.parse_args()

    folder = COMIC / f"page-{args.page:03d}"
    plan = json.loads((folder / "page_plan.json").read_text())
    panel = next(p for p in plan["panels"] if p["panel"] == args.panel)
    spec = json.loads((folder / "prompts" / f"panel-{args.panel}.json").read_text())
    pdir = folder / "panels" / f"panel-{args.panel}"
    cands = sorted(pdir.glob("*.png"))
    if not cands:
        sys.exit("no candidates")

    chars = "; ".join(f"{c['id']}: {c['position']}, {c['pose']}, expression {c['expression']}, facing {c['facing']}"
                      for c in panel["characters"]) or "(none)"
    texts = [f"caption at {c['placement']}: {c['text']}" for c in panel["captions"]]
    texts += [f"balloon at {d['placement']}, {d['speaker']}: {d['text']}" for d in panel["dialogue"]]
    refs = "\n".join(f"- {r['name']} (v{r['version']}): {r['file']}" for r in spec["references"]) or "(none)"
    files = "\n".join(f"- {c.name}: {c.relative_to(ROOT)}" for c in cands)
    prompt = PROMPT.format(book=book_passage(folder, panel), panel=args.panel, shot=panel["shot"],
                           angle=panel["angle"], purpose=panel["purpose"], description=panel["description"],
                           characters=chars, objects=", ".join(panel["objects"]) or "(none)",
                           lighting=panel["lighting"], text="; ".join(texts) or "(none)",
                           text_space=panel["text_space"] or "(none)", references=refs, files=files)
    result, meta = claude_cli.ask(prompt, SCHEMA, read_files=True, timeout=1800)
    out = pdir / "assessment.json"
    if out.exists():
        out.rename(pdir / f"assessment.{datetime.now():%Y%m%d-%H%M%S}.json")
    result.update({"created_utc": datetime.now(timezone.utc).isoformat(), "prompt_sha256": spec["prompt_sha256"],
                   **meta})
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"assessed {len(result['candidates'])} candidates of page {args.page} panel {args.panel}; "
          f"best: {result['best']} ({meta['elapsed_seconds']}s)", flush=True)


if __name__ == "__main__":
    main()
