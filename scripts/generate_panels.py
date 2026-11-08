#!/usr/bin/env python3
"""Milestone D4: generate panel candidates from the prompts built by build_prompts.py.

For every panel (or the ones given with --panel): N candidates with different seeds.
Panels with a reference image use Draw Things edit mode (img2img, strength 1.0, the
composed reference at the panel's generation size); others use txt2img.
Everything needed to reproduce a candidate is stored next to it. Nothing is overwritten.

Usage:  python3 scripts/generate_panels.py 3 [--panel 2 4] [--count 2]
Output: outputs/comic/page-<NNN>/panels/panel-<n>/<timestamp>_seed<seed>.png (+ .json)
"""
import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawthings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMIC = ROOT / "outputs" / "comic"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=int)
    ap.add_argument("--panel", type=int, nargs="*", help="only these panels (default: all)")
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--no-assess", action="store_true", help="skip the Claude assessment after each panel")
    args = ap.parse_args()

    folder = COMIC / f"page-{args.page:03d}"
    prompt_files = sorted((folder / "prompts").glob("panel-*.json"),
                          key=lambda p: int(p.stem.split("-")[1]))
    if args.panel:
        prompt_files = [p for p in prompt_files if int(p.stem.split("-")[1]) in args.panel]
    if not prompt_files:
        sys.exit("no prompts found: run build_prompts.py first")

    total = len(prompt_files) * args.count
    n = 0
    for pf in prompt_files:
        spec = json.loads(pf.read_text())
        ref_png = (ROOT / spec["reference_image"]).read_bytes() if spec["reference_image"] else None
        size = spec["generation_size"]
        out_dir = folder / "panels" / f"panel-{spec['panel']}"
        for _ in range(args.count):
            n += 1
            seed = random.randint(1, 2**31 - 1)
            print(f"[{n}/{total}] panel {spec['panel']}: generating seed {seed}…", flush=True)
            png, record = drawthings.generate(spec["prompt"], seed, size["width"], size["height"],
                                              reference_png=ref_png, strength=1.0)
            record.update({"page": args.page, "panel": spec["panel"],
                           "prompt_file": str(pf.relative_to(ROOT)), "prompt_sha256": spec["prompt_sha256"],
                           "references": spec["references"], "page_box": spec["page_box"]})
            out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}_seed{seed}.png"
            drawthings.save(png, record, out)
            print(f"[{n}/{total}] done in {record['elapsed_seconds']}s -> panel-{spec['panel']}/{out.name}", flush=True)
        if not args.no_assess:
            import subprocess
            print(f"assessing panel {spec['panel']}…", flush=True)
            subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "assess_panel.py"),
                            str(args.page), str(spec["panel"])], check=False)


if __name__ == "__main__":
    main()
