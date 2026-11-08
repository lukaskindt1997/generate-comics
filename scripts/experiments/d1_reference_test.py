#!/usr/bin/env python3
"""Experiment D1: does Draw Things (HTTP API) use an input image as a *reference*
for FLUX.2 Klein, or only as an img2img starting point?

Sends the same input image + a prompt describing a NEW scene to /sdapi/v1/img2img
at several strengths. Interpretation:
  - strength 1.0 output keeps the person's identity in a new pose/scene -> reference conditioning
  - strength 1.0 output ignores the input completely                    -> plain img2img only

Usage:  python3 scripts/experiments/d1_reference_test.py <ref1.png> [ref2.png ...] --strengths 1.0 [--width W --height H]
Output: outputs/experiments/d1/<timestamp>_s<strength>.png + .json
"""
import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "experiments" / "d1"
URL = "http://127.0.0.1:7860"

PROMPT = ("The same soldier from the reference image, now sitting alone at a rough wooden table "
          "in a dim tavern, helmet off and resting on the table, holding a clay cup, seen from the side. "
          "Realistic European dark fantasy graphic novel illustration, detailed ink linework, "
          "painterly textures, muted atmospheric colours. No text, no lettering.")


def post(path, payload, timeout=900):
    req = urllib.request.Request(URL + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read()[:2000]!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+", help="one or more reference images (order matters)")
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--tag", default="")
    ap.add_argument("--strengths", type=float, nargs="+", default=[1.0, 0.6])
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--prompt", default=PROMPT)
    args = ap.parse_args()

    imgs_b64 = [base64.b64encode(Path(p).read_bytes()).decode() for p in args.images]
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for s in args.strengths:
        payload = {"prompt": args.prompt, "negative_prompt": "", "seed": args.seed,
                   "width": args.width, "height": args.height, "strength": s, "batch_count": 1,
                   "init_images": imgs_b64}
        t0 = time.time()
        res = post("/sdapi/v1/img2img", payload)
        dt = round(time.time() - t0, 1)
        imgs = res.get("images") or []
        if not imgs:
            print(f"strength {s}: no image returned; keys={list(res)}")
            continue
        out = OUT / f"{stamp}{'_' + args.tag if args.tag else ''}_s{s}.png"
        out.write_bytes(base64.b64decode(imgs[0].split(",", 1)[-1]))
        meta = {k: v for k, v in payload.items() if k != "init_images"}
        meta.update({"input_images": args.images, "elapsed_seconds": dt,
                     "response_keys": list(res)})
        out.with_suffix(".json").write_text(json.dumps(meta, indent=2))
        print(f"strength {s}: {dt}s -> {out}")


if __name__ == "__main__":
    main()
