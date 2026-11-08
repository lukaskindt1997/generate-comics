#!/usr/bin/env python3
"""Milestone D2: generate reference candidates for a character, object or location.

Each candidate is ONE view (characters: full body from the front; objects: three-quarter
front view). The prompt sent to Draw Things is the short, positively phrased
generation_prompt (refprompts.py), not the long description.

Steps:
  1. clear the current approval (kept in approved_history): the user picks again
  2. generate N candidates with different seeds
  3. assess all candidates (assess_reference.py)
  4. auto-fix: the best NEW candidate gets a targeted edit pass for its deviations
     (fix_reference.py), config/style.json "auto_fix_rounds" times (0 = off)

Extra views (three-quarter, portrait, side) are made AFTER approval by make_views.py.

Usage:  python3 scripts/make_reference.py characters/ganoes_paran [--count 4] [--no-assess]
"""
import argparse
import json
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawthings  # noqa: E402
import refprompts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
SCRIPTS = Path(__file__).resolve().parent


def build_prompt(ref, gen_prompt, style, comps=(), vertical=False):
    view = style["base_view"][ref["kind"]]
    lead = ""
    if comps:
        import build_prompts
        names = [f"{build_prompts.describe_side(i, len(comps), vertical)} shows {c['name']}"
                 for i, c in enumerate(comps)]
        lead = ("Reference image: " + "; ".join(names) + " (object references). Draw the person described "
                "below wearing or carrying exactly these objects as shown: same shape, colours and details. "
                "Use the reference only for the objects; draw the person as a new single figure on a plain "
                "light grey background. ")
    return f"{lead}{view['layout']} {gen_prompt} {style['prompt_block']} {style['always']}", view


def component_refs(folder):
    """Approved object references this character wears/carries, or exit if some are not approved."""
    comps, waiting = [], []
    for cid in refprompts.components(folder, LIB):
        f = LIB / "objects" / cid / "ref.json"
        if not f.exists():
            continue
        o = json.loads(f.read_text())
        if o.get("approved"):
            o["_image"] = str(f.parent / (o["approved"].get("sheet") or o["approved"]["file"]))
            comps.append(o)
        else:
            waiting.append(o["name"])
    if waiting:
        sys.exit(f"waiting for approved references of what this character wears/carries: {', '.join(waiting)}")
    return comps


def clear_approval(folder, ref, reason):
    if not ref.get("approved"):
        return
    ref.setdefault("approved_history", []).append(
        {**ref["approved"], "cleared_utc": datetime.now(timezone.utc).isoformat(), "cleared_because": reason})
    ref["approved"] = None
    (folder / "ref.json").write_text(json.dumps(ref, indent=2, ensure_ascii=False))
    print(f"approval cleared: {reason}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="e.g. characters/ganoes_paran")
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--no-assess", action="store_true", help="skip assessment and auto-fix")
    args = ap.parse_args()

    folder = LIB / args.ref
    style = refprompts.style()
    print("writing generation prompt…", flush=True)
    gen_prompt = refprompts.ensure_generation_prompt(folder)
    print("checking worn/carried objects…", flush=True)
    comps = component_refs(folder)
    ref = json.loads((folder / "ref.json").read_text())
    view = style["base_view"][ref["kind"]]
    ref_png, vertical = None, False
    if comps:
        import build_prompts
        files = [c["_image"] for c in comps]
        vertical = build_prompts.stack_vertically(files, view["width"], view["height"])
        collage = folder / "candidates" / f"components_{datetime.now():%Y%m%d-%H%M%S}.ref.png"
        collage.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swift", str(SCRIPTS / "compose_refs.swift"), str(collage), str(view["width"]),
                        str(view["height"]), *files] + (["--vertical"] if vertical else []),
                       check=True, capture_output=True)
        ref_png = collage.read_bytes()
        print("using approved references: " + ", ".join(f"{c['name']} v{c['approved']['version']}" for c in comps),
              flush=True)
    clear_approval(folder, ref, "new candidates generated")
    prompt, view = build_prompt(ref, gen_prompt, style, comps, vertical)

    new_files = []
    for i in range(args.count):
        seed = random.randint(1, 2**31 - 1)
        print(f"[{i + 1}/{args.count}] generating seed {seed}…", flush=True)
        png, record = drawthings.generate(prompt, seed, view["width"], view["height"],
                                          reference_png=ref_png, strength=1.0)
        record.update({"reference_id": ref["id"], "description_used": ref["description"],
                       "generation_prompt": gen_prompt, "view": "base", "style": style["name"],
                       "components": [{"id": c["id"], "version": c["approved"]["version"]} for c in comps]})
        out = folder / "candidates" / f"{datetime.now():%Y%m%d-%H%M%S}_seed{seed}.png"
        drawthings.save(png, record, out)
        new_files.append(out.name)
        print(f"[{i + 1}/{args.count}] done in {record['elapsed_seconds']}s -> {out.name}", flush=True)

    if args.no_assess:
        return
    print("assessing candidates…", flush=True)
    subprocess.run([sys.executable, str(SCRIPTS / "assess_reference.py"), args.ref], check=False)

    for _ in range(int(style.get("auto_fix_rounds", 0))):
        a_file = folder / "assessment.json"
        if not a_file.exists():
            break
        a = json.loads(a_file.read_text())
        mine = [c for c in a["candidates"] if c["file"] in new_files and "_fix" not in c["file"]]
        if not mine:
            break
        best = max(mine, key=lambda c: c["score"])
        if not (best.get("book_conflicts") or best["deviations"] or best["story_conflicts"]):
            print(f"best new candidate {best['file']} has no deviations: no fix needed", flush=True)
            break
        print(f"auto-fix of the best new candidate {best['file']}…", flush=True)
        subprocess.run([sys.executable, str(SCRIPTS / "fix_reference.py"), args.ref, best["file"]], check=False)
        fixed = sorted((folder / "candidates").glob("*_fix.png"))
        if fixed:
            new_files.append(fixed[-1].name)


if __name__ == "__main__":
    main()
