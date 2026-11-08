#!/usr/bin/env python3
"""Fix a reference candidate with a targeted edit instead of re-rolling.

Takes the candidate's deviations from assessment.json, lets Claude pick the ONE most
important one and write a short, positive edit instruction for it, and runs FLUX.2 Klein in edit mode (the candidate is the
reference image, strength 1.0, same size). The result is saved as a NEW candidate with its
parent and the instruction recorded; then all candidates are re-assessed. The original is
never changed. Clears the current approval, like any new candidate.

Usage:  python3 scripts/fix_reference.py characters/ganoes_paran 20261107-230957_seed1041233554.png
"""
import argparse
import json
import random
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawthings  # noqa: E402
import refprompts  # noqa: E402
from make_reference import clear_approval  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
SCRIPTS = Path(__file__).resolve().parent


def png_size(path):
    with open(path, "rb") as fh:
        return struct.unpack(">II", fh.read(24)[16:24])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("candidate", help="file name in candidates/")
    ap.add_argument("--no-assess", action="store_true")
    args = ap.parse_args()

    folder = LIB / args.ref
    ref = json.loads((folder / "ref.json").read_text())
    src = folder / "candidates" / args.candidate
    a = json.loads((folder / "assessment.json").read_text())
    entry = next((c for c in a["candidates"] if c["file"] == args.candidate), None)
    problems = entry and (entry.get("book_conflicts", []) + entry["deviations"] + entry["story_conflicts"])
    if not problems:
        sys.exit("no deviations recorded for this candidate: nothing to fix")

    print("writing edit instruction…", flush=True)
    instruction, target = refprompts.edit_instruction(ref, problems)
    print(f"fixing one point: {target}", flush=True)
    style = refprompts.style()
    prompt = f"{instruction} {style['prompt_block']} {style['always']}"
    w, h = png_size(src)
    seed = random.randint(1, 2**31 - 1)
    clear_approval(folder, ref, "new candidate made by fixing")
    print(f"[1/1] generating fix of {args.candidate}…", flush=True)
    png, record = drawthings.generate(prompt, seed, w, h, reference_png=src.read_bytes(), strength=1.0)
    parent_meta = src.with_suffix(".json")
    parent = json.loads(parent_meta.read_text()) if parent_meta.exists() else {}
    record.update({"reference_id": ref["id"], "description_used": ref["description"],
                   "view": parent.get("view", "base"), "parent": args.candidate,
                   "edit_instruction": instruction, "fix_target": target, "all_problems": problems,
                   "style": style["name"]})
    out = folder / "candidates" / f"{datetime.now():%Y%m%d-%H%M%S}_seed{seed}_fix.png"
    drawthings.save(png, record, out)
    print(f"[1/1] done in {record['elapsed_seconds']}s -> {out.name}", flush=True)

    if not args.no_assess:
        print("assessing candidates…", flush=True)
        subprocess.run([sys.executable, str(SCRIPTS / "assess_reference.py"), args.ref], check=False)


if __name__ == "__main__":
    main()
