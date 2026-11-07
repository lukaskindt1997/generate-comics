#!/usr/bin/env python3
"""Milestone C2: page plan for one comic page, using Claude via `claude -p`.

Input:  outputs/comic/page-<NNN>/scene.json (analysis), source/decisions.json (answers),
        the chapter brief (characters on this page), optional --note with feedback.
Output: outputs/comic/page-<NNN>/page_plan.json (+ page_plan.meta.json). Never overwrites:
        an existing plan is renamed with a timestamp.

The plan: layout in tiers (rows of panels, as in European albums), and per panel the
beats, shot, camera angle, characters (pose, expression, position), setting, a visual
description for the image prompt, captions/dialogue and where to keep room for text.

Usage:  python3 scripts/plan_page.py 3 [--note "make the vane panel bigger"] [--model MODEL]
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_scene  # noqa: E402  (chapter brief helper)

ROOT = Path(__file__).resolve().parent.parent
COMIC = ROOT / "outputs" / "comic"
SOURCE = ROOT / "source"

PLACEMENT = {"type": "string", "enum": ["top_left", "top", "top_right", "middle_left", "middle_right",
                                        "bottom_left", "bottom", "bottom_right"]}

SCHEMA = {
    "type": "object",
    "properties": {
        "page_concept": {"type": "string", "description": "1-2 sentences: the visual idea of the page."},
        "tiers": {"type": "array", "description": "Rows of panels, top to bottom. Heights sum to 1.",
                  "items": {"type": "object", "properties": {
                      "height": {"type": "number"},
                      "panels": {"type": "array", "description": "Left to right. Widths sum to 1.",
                                 "items": {"type": "object", "properties": {
                                     "panel": {"type": "integer"}, "width": {"type": "number"}},
                                     "required": ["panel", "width"]}}},
                      "required": ["height", "panels"]}},
        "panels": {"type": "array", "items": {"type": "object", "properties": {
            "panel": {"type": "integer", "description": "1, 2, ... in reading order"},
            "beats": {"type": "array", "items": {"type": "string"}},
            "purpose": {"type": "string", "description": "What this panel does for the story."},
            "shot": {"type": "string", "enum": ["extreme_wide", "wide", "medium_wide", "medium",
                                                "medium_close", "close_up", "extreme_close_up"]},
            "angle": {"type": "string", "enum": ["eye_level", "low", "high", "birds_eye", "worms_eye",
                                                 "over_the_shoulder", "dutch"]},
            "location": {"type": ["string", "null"]},
            "lighting": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"},
                "position": {"type": "string", "description": "Where in the frame, e.g. 'right third, foreground'."},
                "pose": {"type": "string"}, "expression": {"type": "string"},
                "facing": {"type": "string"}},
                "required": ["id", "position", "pose", "expression", "facing"]}},
            "objects": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string",
                            "description": "Everything visible in the image, concrete, for the image prompt. No text in the image."},
            "captions": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "placement": PLACEMENT}, "required": ["text", "placement"]}},
            "dialogue": {"type": "array", "items": {"type": "object", "properties": {
                "speaker": {"type": "string"}, "text": {"type": "string"}, "placement": PLACEMENT},
                "required": ["speaker", "text", "placement"]}},
            "text_space": {"type": "string",
                           "description": "Where the image must leave calm, empty-ish space for captions/balloons."},
            "safety_notes": {"type": "string", "description": "Content notes, e.g. minors present. Empty if none."}},
            "required": ["panel", "beats", "purpose", "shot", "angle", "location", "lighting", "characters",
                         "objects", "description", "captions", "dialogue", "text_space", "safety_notes"]}},
        "design_choices": {"type": "array", "items": {"type": "string"},
                           "description": "Choices you made where the answers left options open (e.g. 'blue or burgundy' -> blue)."},
    },
    "required": ["page_concept", "tiers", "panels", "design_choices"],
}

PROMPT = """You are the page layout artist for a personal comic adaptation of a fantasy novel.
Plan ONE comic page from the scene analysis below. One book page = one comic page.

Visual style: realistic European dark-fantasy graphic novel (Franco-Belgian album tradition):
detailed ink, painterly colour, muted atmospheric palette, believable medieval equipment,
cinematic but readable composition. Page format: portrait album page (about 1 : 1.35).

Layout rules:
- Arrange panels in 2-4 tiers (rows), each with 1-3 panels. Typical page: 4-7 panels.
  Little content (e.g. a chapter end) -> fewer, larger panels, even a single splash panel.
- Open a new location with an establishing shot. Vary shot sizes and angles.
- Give the most important beat the largest panel.
- Reading order: tiers top to bottom, panels left to right.
- Every beat marked visual should appear; non-visual beats go into captions only if the
  scene analysis proposes a caption for them. You may merge or shorten captions.
- Text: dialogue word for word; captions short. Never put text in the image itself.
  Plan where each caption/balloon goes and keep that area calm in the image (sky, wall,
  shadow), never over faces.
- Follow the user's decisions exactly. Where a decision leaves options open
  ("blue or burgundy"), choose one and list it under design_choices.
- Characters: use ids. For children: ordinary, non-sexualised, age-appropriate depiction;
  note it in safety_notes.
- Do not show or hint at plot information that is not yet revealed (see brief).

Chapter brief (context):
{brief}

User decisions:
{decisions}

Scene analysis for page {page}:
{scene}
{note}"""


def decisions_text(page):
    path = SOURCE / "decisions.json"
    if not path.exists():
        return "(none)"
    ds = [d for d in json.loads(path.read_text()) if d["scope"] == "story" or d["page"] == page]
    return "\n".join(f"- {d['question']} -> {d['answer']}" for d in ds) or "(none)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=int)
    ap.add_argument("--note", default="", help="feedback for this (re)plan")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    folder = COMIC / f"page-{args.page:03d}"
    scene = json.loads((folder / "scene.json").read_text())
    scene.pop("questions", None)  # answered via decisions
    brief, brief_path = analyze_scene.chapter_brief(args.page)
    note = f"\nFeedback from the user on the previous plan (follow it):\n{args.note}\n" if args.note else ""
    prev = folder / "page_plan.json"
    if args.note and prev.exists():
        note += "\nPrevious plan:\n" + prev.read_text()
    prompt = PROMPT.format(brief=brief, decisions=decisions_text(args.page), page=args.page,
                           scene=json.dumps(scene, indent=1, ensure_ascii=False), note=note)

    cmd = ["claude", "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--disallowedTools", "Bash", "Edit", "Write", "Read", "WebFetch", "WebSearch",
           "--no-session-persistence"]
    if args.model:
        cmd += ["--model", args.model]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1200)
    elapsed = round(time.time() - t0, 1)
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"claude failed ({proc.returncode}): {proc.stderr[-1500:]} {proc.stdout[-1500:]}")
    if raw.get("is_error") or "structured_output" not in raw:
        sys.exit(f"claude returned an error: {raw.get('result')}")

    plan = raw["structured_output"]
    plan["page"] = args.page
    problems = validate(plan)
    if prev.exists():
        prev.rename(folder / f"page_plan.{datetime.now():%Y%m%d-%H%M%S}.json")
    prev.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    (folder / "page_plan.meta.json").write_text(json.dumps({
        "created_utc": datetime.now(timezone.utc).isoformat(), "engine": "claude-cli",
        "models": list((raw.get("modelUsage") or {}).keys()), "elapsed_seconds": elapsed,
        "cost_usd_list_price": raw.get("total_cost_usd"), "note": args.note,
        "chapter_brief": brief_path, "problems": problems, "prompt": prompt}, indent=2, ensure_ascii=False))
    print(f"page plan for page {args.page}: {len(plan['panels'])} panels in {len(plan['tiers'])} tiers "
          f"({elapsed}s) -> {prev}")
    for p in problems:
        print("  warning:", p)


def validate(plan):
    """Deterministic checks of the layout; problems are reported, not fixed."""
    problems = []
    if abs(sum(t["height"] for t in plan["tiers"]) - 1) > 0.02:
        problems.append("tier heights do not sum to 1")
    in_layout = []
    for i, t in enumerate(plan["tiers"], 1):
        if abs(sum(p["width"] for p in t["panels"]) - 1) > 0.02:
            problems.append(f"tier {i}: panel widths do not sum to 1")
        in_layout += [p["panel"] for p in t["panels"]]
    described = [p["panel"] for p in plan["panels"]]
    if sorted(in_layout) != sorted(described):
        problems.append(f"layout panels {in_layout} do not match described panels {described}")
    if in_layout != sorted(in_layout):
        problems.append("panel numbers are not in reading order")
    return problems


if __name__ == "__main__":
    main()
