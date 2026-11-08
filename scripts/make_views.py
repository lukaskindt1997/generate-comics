#!/usr/bin/env python3
"""Make the extra views of an APPROVED reference and combine them into a sheet.

The approved image is the single source of truth: every extra view (characters:
three-quarter view and portrait; objects: side view; see config/style.json "views") is
derived from it in FLUX.2 Klein edit mode, so face and clothing stay the same. The
approved view and the extra views are then composed side by side into one sheet
(compose_refs.swift), which panels use as their reference.

Output: library/<ref>/approved/v<N>_views/<view>.png (+ .json), approved/v<N>_sheet.png;
        ref.json "approved.sheet" points to the sheet.

Usage:  python3 scripts/make_views.py characters/ganoes_paran
"""
import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawthings  # noqa: E402
import refprompts  # noqa: E402
from fix_reference import png_size  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
SCRIPTS = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    args = ap.parse_args()

    folder = LIB / args.ref
    ref = json.loads((folder / "ref.json").read_text())
    if not ref.get("approved"):
        sys.exit("approve a candidate first")
    if ref["approved"].get("multi_view_candidate"):
        sys.exit("the approved candidate is already a multi-view sheet: no extra views needed")
    base = folder / ref["approved"]["file"]
    style = refprompts.style()
    views = style["views"].get(ref["kind"], [])
    v = ref["approved"]["version"]
    out_dir = folder / "approved" / f"v{v}_views"
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = png_size(base)
    files = [str(base)]
    for i, view in enumerate(views):
        out = out_dir / f"{view['name']}.png"
        if out.exists():
            out.rename(out_dir / f"{view['name']}.{out.stat().st_mtime_ns}.png")
        prompt = f"{view['prompt']} {style['prompt_block']} {style['always']}"
        seed = random.randint(1, 2**31 - 1)
        print(f"[{i + 1}/{len(views)}] generating view '{view['name']}'…", flush=True)
        png, record = drawthings.generate(prompt, seed, w, h, reference_png=base.read_bytes(), strength=1.0)
        record.update({"reference_id": ref["id"], "view": view["name"], "from_approved": ref["approved"]["file"]})
        drawthings.save(png, record, out)
        files.append(str(out))
        print(f"[{i + 1}/{len(views)}] done in {record['elapsed_seconds']}s -> {out.name}", flush=True)

    sheet = folder / "approved" / f"v{v}_sheet.png"
    sw = 1280 if len(files) > 1 else w
    subprocess.run(["swift", str(SCRIPTS / "compose_refs.swift"), str(sheet), str(sw), "768", *files],
                   check=True, capture_output=True)
    ref = json.loads((folder / "ref.json").read_text())  # re-read: may have changed meanwhile
    if ref.get("approved") and ref["approved"]["version"] == v:
        ref["approved"]["sheet"] = f"approved/v{v}_sheet.png"
        ref["approved"]["views"] = [f"approved/v{v}_views/{x['name']}.png" for x in views]
        (folder / "ref.json").write_text(json.dumps(ref, indent=2, ensure_ascii=False))
    print(f"sheet -> {sheet.name}", flush=True)


if __name__ == "__main__":
    main()
