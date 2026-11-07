#!/usr/bin/env python3
"""Milestone C1: scene analysis for one comic page, using Claude via `claude -p`.

Reads the assembled page text (prepare_page.py) and produces a structured analysis:
characters, locations, objects, atmosphere, story beats, captions and dialogue,
each with references to the paragraphs (P1, P2, ...) it is based on.

The output is an analysis, not a reproduction of the text: captions are condensed;
only dialogue is kept word for word.

Usage:  python3 scripts/analyze_scene.py 3 [--model MODEL]
Output: outputs/comic/page-<NNN>/scene.json  (+ scene.meta.json with run details)
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMIC = ROOT / "outputs" / "comic"
FACTS = ROOT / "source" / "story_facts.md"
SOURCE = ROOT / "source"

EVIDENCE = {"type": "array", "items": {"type": "string"}, "description": "Paragraph ids, e.g. [\"P3\"]"}

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "What happens on this page, 1-3 sentences."},
        "time": {"type": "object", "properties": {
            "story_date": {"type": ["string", "null"]},
            "time_of_day": {"type": ["string", "null"]},
            "evidence": EVIDENCE}, "required": ["story_date", "time_of_day", "evidence"]},
        "locations": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "description": "snake_case, stable across pages, e.g. mocks_hold"},
            "name": {"type": "string"},
            "visual_details": {"type": "array", "items": {"type": "string"}},
            "evidence": EVIDENCE}, "required": ["id", "name", "visual_details", "evidence"]}},
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "description": "snake_case, e.g. ganoes_paran"},
            "name": {"type": "string"},
            "on_page": {"type": "boolean", "description": "Physically present (false = only mentioned)."},
            "age": {"type": ["string", "null"], "description": "Stated age, or null."},
            "age_category": {"type": "string", "enum": ["child", "teen", "adult", "unknown"]},
            "visual_details": {"type": "array", "items": {"type": "string"},
                               "description": "Only what the text or known facts state."},
            "evidence": EVIDENCE},
            "required": ["id", "name", "on_page", "age", "age_category", "visual_details", "evidence"]}},
        "objects": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "visual_details": {"type": "array", "items": {"type": "string"}},
            "evidence": EVIDENCE}, "required": ["id", "name", "visual_details", "evidence"]}},
        "atmosphere": {"type": "object", "properties": {
            "weather": {"type": "string"}, "lighting": {"type": "string"}, "mood": {"type": "string"},
            "sounds_smells": {"type": "string"}},
            "required": ["weather", "lighting", "mood", "sounds_smells"]},
        "beats": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "description": "B1, B2, ..."},
            "summary": {"type": "string"},
            "visual": {"type": "boolean", "description": "Can be shown in an image (false = backstory/inner thought)."},
            "importance": {"type": "string", "enum": ["high", "medium", "low"]},
            "characters": {"type": "array", "items": {"type": "string"}},
            "location": {"type": ["string", "null"]},
            "emotion": {"type": ["string", "null"]},
            "evidence": EVIDENCE},
            "required": ["id", "summary", "visual", "importance", "characters", "location", "emotion", "evidence"]}},
        "captions": {"type": "array", "items": {"type": "object", "properties": {
            "beat": {"type": "string"},
            "kind": {"type": "string", "enum": ["time_place", "narration", "inner_thought"]},
            "text": {"type": "string", "description": "Condensed, max ~20 words."},
            "why_needed": {"type": "string", "description": "What the image alone cannot show."}},
            "required": ["beat", "kind", "text", "why_needed"]}},
        "dialogue": {"type": "array", "items": {"type": "object", "properties": {
            "beat": {"type": "string"}, "speaker": {"type": "string"},
            "addressee": {"type": ["string", "null"]},
            "text": {"type": "string", "description": "Word for word."},
            "evidence": EVIDENCE},
            "required": ["beat", "speaker", "addressee", "text", "evidence"]}},
        "questions": {"type": "array", "items": {"type": "object", "properties": {
            "question": {"type": "string"},
            "recommendation": {"type": "string",
                               "description": "Your recommended answer: concrete enough to draw."},
            "reason": {"type": "string", "description": "One sentence: why."},
            "scope": {"type": "string", "enum": ["story", "page"],
                      "description": "story = should hold for later pages too (e.g. a character's look)."}},
            "required": ["question", "recommendation", "reason", "scope"]},
            "description": "Things the text leaves open that matter for drawing (for the human)."},
    },
    "required": ["summary", "time", "locations", "characters", "objects", "atmosphere",
                 "beats", "captions", "dialogue", "questions"],
}

PROMPT = """You are the story analyst for a personal comic adaptation of a fantasy novel.
Analyse the text of ONE comic page (it corresponds to one book page) and return JSON.

The comic page will later be planned into panels. Your job is only the analysis.

Rules:
- Base everything on the page text, the chapter brief and the known facts. Do not add
  details from your own memory of the book or its sequels. If something needed for drawing
  is not stated anywhere, list it under "questions" instead of inventing it. For every
  question give a recommended answer that fits the text, the tone and the realistic
  European dark-fantasy style, with a one-sentence reason, and say whether it should hold
  for the whole story or only this page. The user may accept or change it.
- The chapter brief lists facts from the whole chapter with the page where they are
  revealed. Descriptive facts (appearance, age, clothing, time of day, weather) may be
  used even if revealed on a later page. Plot information from later pages must NOT be
  used or hinted at: follow the "not yet revealed" rules.
- Use the character/location/object ids from the chapter brief.
- Every item gets evidence: the paragraph ids (P1, P2, ...) it comes from.
- Use stable snake_case ids for characters, locations and objects.
- Characters: only physically present people are on_page=true. Give age and age_category
  only when stated in the text or known facts; otherwise "unknown".
- Beats: the story moments in order. Mark backstory and inner thoughts as visual=false.
- Captions: condensed narration (max ~20 words each), ONLY for information the images
  cannot show by themselves (time and place, essential backstory, inner thoughts).
  Don't caption what a picture shows (smoke, wind, a boy at a wall).
  Do not copy long passages.
- Dialogue: word for word, with speaker and addressee.

Chapter brief:
{brief}

Known facts and decisions:
{facts}

Comic page {page} text (paragraph ids in brackets):
{text}
"""


def chapter_brief(page):
    """Brief as text: all descriptive facts, plus reveals split into known / not yet."""
    import re
    m = re.search(r"^section:\s*(.+)$", (SOURCE / "manifest.yaml").read_text(), re.M)
    slug = re.sub(r"[^a-z0-9]+", "-", (m.group(1) if m else "chapter").strip().lower()).strip("-")
    path = COMIC / f"chapter-{slug}" / "brief.json"
    if not path.exists():
        return "(no chapter brief)", None
    b = json.loads(path.read_text())
    out = ["Characters:"]
    for c in b["characters"]:
        present = ", ".join(map(str, c["on_pages"])) or "only mentioned"
        out.append(f"- {c['name']} [id {c['id']}], {c['age_category']}; aliases: "
                   f"{', '.join(c['aliases']) or '-'}; present on pages: {present}")
        out += [f"    p{f['page']} ({f['kind']}): {f['fact']}" for f in c["facts"]]
    for key, title in (("locations", "Locations"), ("objects", "Objects")):
        out.append(f"{title}:")
        for o in b[key]:
            out.append(f"- {o['name']} [id {o['id']}]")
            out += [f"    p{f['page']}: {f['fact']}" for f in o["facts"]]
    out.append("Timeline (time, weather, background events):")
    out += [f"- p{t['page']}: {t['fact']}" for t in b["timeline"]]
    known = [r for r in b["reveals"] if r["revealed_on_page"] <= page]
    later = [r for r in b["reveals"] if r["revealed_on_page"] > page]
    out.append("Plot reveals already known to the reader on this page:")
    out += [f"- {r['fact']}" for r in known] or ["- (none)"]
    out.append("Not yet revealed on this page: do NOT show or hint at these:")
    out += [f"- (revealed p{r['revealed_on_page']}) {r['note']}" for r in later] or ["- (none)"]
    return "\n".join(out), str(path.relative_to(ROOT))


def record_recommendations(page, questions):
    """Store recommendations as this page's answers, unless the user already answered."""
    path = SOURCE / "decisions.json"
    decisions = json.loads(path.read_text()) if path.exists() else []
    decisions = [d for d in decisions if not (d["page"] == page and d.get("source") == "recommended")]
    answered = {d["question"] for d in decisions if d["page"] == page}
    now = datetime.now(timezone.utc).isoformat()
    for q in questions:
        if q["question"] not in answered:
            decisions.append({"page": page, "question": q["question"], "answer": q["recommendation"],
                              "scope": q["scope"], "source": "recommended", "reason": q["reason"],
                              "updated_utc": now})
    path.write_text(json.dumps(decisions, indent=2, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=int)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    folder = COMIC / f"page-{args.page:03d}"
    inp = json.loads((folder / "input.json").read_text())
    text = "\n\n".join(f"[{p['id']}] {p['text']}" for p in inp["paragraphs"])
    facts = FACTS.read_text() if FACTS.exists() else "(none)"
    decisions_file = SOURCE / "decisions.json"
    if decisions_file.exists():
        # Skip this page's own unconfirmed recommendations: they are being re-made now.
        ds = [d for d in json.loads(decisions_file.read_text())
              if (d["scope"] == "story" or d["page"] == args.page)
              and not (d["page"] == args.page and d.get("source") == "recommended")]
        if ds:
            facts += "\n\nDecisions by the user (follow these):\n" + "\n".join(
                f"- {d['answer']} (answer to: {d['question']}; page {d['page']}, {d['scope']})" for d in ds)
    brief, brief_path = chapter_brief(args.page)
    prompt = PROMPT.format(brief=brief, facts=facts, page=args.page, text=text)

    cmd = ["claude", "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--disallowedTools", "Bash", "Edit", "Write", "Read", "WebFetch", "WebSearch",
           "--no-session-persistence"]
    if args.model:
        cmd += ["--model", args.model]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900)
    elapsed = round(time.time() - t0, 1)
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"claude failed ({proc.returncode}): {proc.stderr[-1500:]} {proc.stdout[-1500:]}")
    if raw.get("is_error") or "structured_output" not in raw:
        sys.exit(f"claude returned an error: {raw.get('result')}")

    scene = raw["structured_output"]
    out = folder / "scene.json"
    if out.exists():  # never overwrite a previous analysis
        out.rename(folder / f"scene.{datetime.now():%Y%m%d-%H%M%S}.json")
    out.write_text(json.dumps(scene, indent=2, ensure_ascii=False))
    (folder / "scene.meta.json").write_text(json.dumps({
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "engine": "claude-cli",
        "models": list((raw.get("modelUsage") or {}).keys()),
        "elapsed_seconds": elapsed,
        "cost_usd_list_price": raw.get("total_cost_usd"),
        "input": "input.json",
        "facts_file": str(FACTS.relative_to(ROOT)),
        "chapter_brief": brief_path,
        "prompt": prompt,
    }, indent=2, ensure_ascii=False))
    record_recommendations(args.page, scene["questions"])
    print(f"scene analysis for page {args.page}: {len(scene['beats'])} beats, "
          f"{len(scene['characters'])} characters, {len(scene['captions'])} captions, "
          f"{len(scene['questions'])} questions ({elapsed}s) -> {out}")


if __name__ == "__main__":
    main()
