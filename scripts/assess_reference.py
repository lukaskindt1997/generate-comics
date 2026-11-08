#!/usr/bin/env python3
"""Milestone D2b: assess reference candidates with Claude (via `claude -p`, reads the images).

Compares every candidate of a reference with
  1. its description (ref.json), and
  2. the story context: chapter-brief facts, user decisions, and how page plans use it
and returns per candidate: score, pros, cons, deviations from the description,
conflicts with the story. Per reference: the best candidate and, if useful, a
suggested description change.

Usage:  python3 scripts/assess_reference.py characters/ganoes_paran
Output: library/<ref>/assessment.json (an older one is kept as assessment.<timestamp>.json)
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
COMIC = ROOT / "outputs" / "comic"
SOURCE = ROOT / "source"

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string", "description": "File name exactly as given."},
            "score": {"type": "integer", "minimum": 1, "maximum": 5,
                      "description": "5 = matches description and story, clean, usable as reference."},
            "matches_book": {"type": "boolean",
                             "description": "No contradiction with BOOK FACTS or USER DECISIONS."},
            "book_conflicts": {"type": "array", "items": {"type": "string"},
                               "description": "Contradictions with BOOK FACTS or USER DECISIONS (serious)."},
            "matches_description": {"type": "boolean"},
            "deviations": {"type": "array", "items": {"type": "string"},
                           "description": "Differences from the DESCRIPTION in details the book does NOT "
                                          "specify (free choices; minor). Never repeat book_conflicts here."},
            "story_conflicts": {"type": "array", "items": {"type": "string"},
                                "description": "Conflicts with how the PAGE PLANS show it (not book facts)."},
            "pros": {"type": "array", "items": {"type": "string"}},
            "cons": {"type": "array", "items": {"type": "string"}}},
            "required": ["file", "score", "matches_book", "book_conflicts", "matches_description", "deviations",
                         "story_conflicts", "pros", "cons"]}},
        "best": {"type": ["string", "null"], "description": "File name of the best candidate, or null if none is usable."},
        "best_reason": {"type": "string"},
        "description_issues": {"type": "array", "items": {"type": "string"},
                               "description": "Where the DESCRIPTION itself is missing or contradicts the story context."},
        "suggested_description": {"type": ["string", "null"],
                                  "description": "Improved full description, or null if the current one is fine."},
        "persistent_issues": {"type": "array", "description":
            "Deviations the image model keeps making across MANY candidates (incl. failed fixes): "
            "a sign it won't draw this reliably.", "items": {"type": "object", "properties": {
                "issue": {"type": "string", "description": "What FLUX keeps doing, e.g. 'draws the cloak knee-length'."},
                "seen_in": {"type": "integer", "description": "Number of candidates showing it."},
                "importance": {"type": "string", "enum": ["low", "medium", "high"],
                               "description": "How much it matters for recognising the subject in panels and for the story."},
                "deviates_from": {"type": "string", "enum": ["book", "decision", "free_choice"],
                                  "description": "book = contradicts a BOOK FACT; decision = contradicts a USER "
                                                 "DECISION; free_choice = only differs from a detail the "
                                                 "description chose where the book is silent."},
                "source_quote": {"type": "string",
                                 "description": "The book fact or decision it contradicts (with page), or "
                                                "'' for free_choice."},
                "description_if_accepted": {"type": "string",
                                            "description": "Full description, changed only so that this is accepted."}},
                "required": ["issue", "seen_in", "importance", "deviates_from", "source_quote",
                             "description_if_accepted"]}},
    },
    "required": ["candidates", "best", "best_reason", "description_issues", "suggested_description",
                 "persistent_issues"],
}

PROMPT = """You are the continuity supervisor for a personal comic adaptation of a fantasy novel.
A reference sheet fixes how a recurring {kind} looks in every panel. Assess the candidate
reference images below, so the user can make an informed choice.

ORDER OF TRUTH: 1. BOOK FACTS, 2. USER DECISIONS, 3. the DESCRIPTION. The description was
written to match the book, but where the book is silent it contains free choices (hair colour,
clothing colours, exact shapes...). Those are flexible.

Read every image with the Read tool, then judge each candidate on:
1. book_conflicts: does anything contradict the BOOK FACTS or USER DECISIONS? (serious)
2. deviations: where does it differ from the DESCRIPTION in details the book does not specify?
   (minor: free choices; a different but plausible choice is acceptable)
3. story_conflicts: does it conflict with how the PAGE PLANS show it?
4. Is it a good reference: complete, clean, readable, neutral pose? (Most candidates are a
   single view; older ones may be multi-view sheets: check the views agree.)
Score 1-5, driven mainly by the book and the decisions: a candidate that fits the book and the
decisions and is a good reference scores 4-5 even if it differs in free choices (mention in
pros/cons that the description could be updated to match it). Book conflicts pull the score
down hard. Give short pros and cons.

Also look across ALL candidates: if the same deviation shows up again and again (in many
candidates, including fixes that tried to correct it), the image model probably can't or won't
draw it. List those under persistent_issues, rate how much they matter (low = doesn't affect
recognising the subject or the story), say whether it contradicts the BOOK, a USER DECISION or
only a free choice of the description (quote the book fact or decision), and give the
description as it would read if the user simply accepts what the model draws.
Do not list points the user already decided on (see ACCEPTED / KEEP REQUIRING below).

Then pick the best candidate (or null if none is usable). If the description itself is
incomplete or contradicts the story context, list that under description_issues and write a
suggested_description (full text, same style, keep everything that is right). Do not add
details that neither the description nor the context supports.

{kind_upper}: {name}
DESCRIPTION:
{description}

ACCEPTED (the user accepted these model habits; they are fine, never count them as deviations):
{accepted}
KEEP REQUIRING (the user insists on these; keep counting them, but don't list them as persistent):
{insisted}

BOOK FACTS (the truth):
{book}

USER DECISIONS (also binding):
{decisions}

PAGE PLANS (how panels use it):
{plans}

{components}
CANDIDATES (file name: path to read):
{files}
"""


def chapter_context(ref):
    lines = []
    for brief_file in sorted(COMIC.glob("chapter-*/brief.json")):
        b = json.loads(brief_file.read_text())
        for key in ("characters", "objects", "locations"):
            for item in b.get(key, []):
                if item["id"] == ref["id"] or item["name"].lower() == ref["name"].lower():
                    lines += [f"- book p{f['page']}: {f['fact']}" for f in item["facts"]]
    return lines


def decisions_context(ref):
    path = SOURCE / "decisions.json"
    if not path.exists():
        return []
    words = {w.lower() for w in re.findall(r"[A-Za-z']{4,}", ref["name"])}
    return [f"- decision (page {d['page']}): {d['question']} -> {d['answer']}"
            for d in json.loads(path.read_text())
            if words & {w.lower() for w in re.findall(r"[A-Za-z']{4,}", d["question"] + " " + d["answer"])}]


def plan_context(ref):
    lines = []
    for plan_file in sorted(COMIC.glob("page-*/page_plan.json")):
        plan = json.loads(plan_file.read_text())
        for p in plan["panels"]:
            ids = [c["id"] for c in p["characters"]] + p["objects"]
            if ref["id"] in ids:
                chars = [c for c in p["characters"] if c["id"] == ref["id"]]
                extra = f" Pose: {chars[0]['pose']}" if chars else ""
                lines.append(f"- page {plan['page']} panel {p['panel']} ({p['shot']}): {p['description']}{extra}")
    return lines


def regression_check(folder, result):
    """Compare every fixed candidate with its parent (scores from this same assessment run).
    A fix that scores lower than its parent is marked failed and can't be 'best'."""
    by_file = {c["file"]: c for c in result["candidates"]}
    for c in result["candidates"]:
        meta = folder / "candidates" / (Path(c["file"]).stem + ".json")
        parent = json.loads(meta.read_text()).get("parent") if meta.exists() else None
        if not parent or parent not in by_file:
            continue
        p = by_file[parent]
        c["parent"] = parent
        c["fix_target"] = json.loads(meta.read_text()).get("fix_target")
        c["score_change"] = c["score"] - p["score"]
        c["fix_result"] = ("improved" if c["score"] > p["score"]
                           else "failed" if c["score"] < p["score"] else "no better")
    best = by_file.get(result.get("best") or "")
    if best and best.get("fix_result") == "failed":
        ok = [c for c in result["candidates"] if c.get("fix_result") != "failed"]
        result["best"] = max(ok, key=lambda c: c["score"])["file"] if ok else None
        result["best_reason"] = ("(The assessment's first choice was a fix that scored lower than its "
                                 "original, so the best remaining candidate is shown.) " + result["best_reason"])


def component_block(ref):
    """Approved references of objects this character wears/carries: the candidates must match them."""
    ids = (ref.get("components") or {}).get("ids") or []
    lines = []
    for cid in ids:
        f = LIB / "objects" / cid / "ref.json"
        if f.exists():
            o = json.loads(f.read_text())
            if o.get("approved"):
                img = f.parent / (o["approved"].get("sheet") or o["approved"]["file"])
                lines.append(f"- {o['name']}: {img.relative_to(ROOT)}")
    if not lines:
        return ""
    return ("APPROVED OBJECT REFERENCES this character wears/carries (read these images too; a candidate "
            "whose version of the object differs from them has a deviation):\n" + "\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="e.g. characters/ganoes_paran")
    args = ap.parse_args()

    folder = LIB / args.ref
    ref = json.loads((folder / "ref.json").read_text())
    cands = sorted((folder / "candidates").glob("*.png"))
    if not cands:
        sys.exit("no candidates")
    book, decisions, plans = chapter_context(ref), decisions_context(ref), plan_context(ref)
    context = (book + decisions + plans) or ["(none)"]
    def label(c):
        meta = c.with_suffix(".json")
        m = json.loads(meta.read_text()) if meta.exists() else {}
        if m.get("parent"):
            return f" (fix of {m['parent']}: \"{m.get('edit_instruction', '')}\")"
        return " (single view)" if m.get("view") == "base" else " (older multi-view sheet)"
    files = "\n".join(f"- {c.name}: {c.relative_to(ROOT)}{label(c)}" for c in cands)
    prompt = PROMPT.format(kind=ref["kind"], kind_upper=ref["kind"].upper(), name=ref["name"],
                           description=ref["description"], files=files,
                           book="\n".join(book) or "(none)", decisions="\n".join(decisions) or "(none)",
                           plans="\n".join(plans) or "(none)",
                           accepted="\n".join(f"- {x}" for x in ref.get("accepted_model_habits", [])) or "(none)",
                           insisted="\n".join(f"- {x}" for x in ref.get("insisted", [])) or "(none)",
                           components=component_block(ref))

    cmd = ["claude", "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--allowedTools", "Read", "--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch",
           "--no-session-persistence"]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800)
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"claude failed ({proc.returncode}): {proc.stderr[-1500:]} {proc.stdout[-1500:]}")
    if raw.get("is_error") or "structured_output" not in raw:
        sys.exit(f"claude returned an error: {raw.get('result')}")

    result = raw["structured_output"]
    regression_check(folder, result)
    out = folder / "assessment.json"
    if out.exists():
        out.rename(folder / f"assessment.{datetime.now():%Y%m%d-%H%M%S}.json")
    result.update({
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "description_assessed": ref["description"],
        "models": list((raw.get("modelUsage") or {}).keys()),
        "elapsed_seconds": round(time.time() - t0, 1),
        "context": context,
    })
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"assessed {len(result['candidates'])} candidates of {ref['name']}; best: {result['best']} "
          f"({result['elapsed_seconds']}s)", flush=True)


if __name__ == "__main__":
    main()
