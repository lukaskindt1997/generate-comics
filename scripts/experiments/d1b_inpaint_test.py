#!/usr/bin/env python3
"""Experiment D1b: does the Draw Things HTTP API accept an inpainting mask?

Sends img2img with init_images + "mask" (white = redraw, black = keep). If the mask is
honoured, pixels OUTSIDE the mask stay (nearly) unchanged; if it is ignored, the whole
image changes. Measures that by comparing the outside region pixel by pixel.

Usage:  python3 scripts/experiments/d1b_inpaint_test.py <image.png> x0 y0 x1 y1 --prompt "..." [--strength 1.0]
        (rectangle in pixels of the input image)
Output: outputs/experiments/d1b/<timestamp>_s<strength>.png + _mask.png + .json
"""
import argparse
import base64
import json
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "experiments" / "d1b"
URL = "http://127.0.0.1:7860"


def png_size(path):
    with open(path, "rb") as fh:
        return struct.unpack(">II", fh.read(24)[16:24])


def gray_png(w, h, rect):
    """Grayscale PNG: 255 inside rect (x0, y0, x1, y1), 0 elsewhere. Standard library only."""
    x0, y0, x1, y1 = rect
    rows = bytearray()
    for y in range(h):
        rows.append(0)  # filter type: none
        inside = y0 <= y < y1
        rows += bytes(255 if inside and x0 <= x < x1 else 0 for x in range(w))
    def chunk(t, data):
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def bmp_pixels(png_path, tmp):
    subprocess.run(["sips", "-s", "format", "bmp", str(png_path), "--out", str(tmp)], check=True, capture_output=True)
    b = tmp.read_bytes()
    off = int.from_bytes(b[10:14], "little")
    w = int.from_bytes(b[18:22], "little", signed=True)
    h = int.from_bytes(b[22:26], "little", signed=True)
    bpp = int.from_bytes(b[28:30], "little") // 8
    return b[off:], w, abs(h), bpp, h > 0  # bottom-up if h > 0


def outside_change(a_png, b_png, rect, tmpdir):
    pa, w, h, bpp, bottom_up = bmp_pixels(a_png, tmpdir / "a.bmp")
    pb, *_ = bmp_pixels(b_png, tmpdir / "b.bmp")
    stride = ((w * bpp + 3) // 4) * 4
    x0, y0, x1, y1 = rect
    changed = total = 0
    diff_sum = 0
    for y in range(h):
        row = (h - 1 - y) if bottom_up else y
        for x in range(0, w, 2):  # every 2nd pixel is plenty
            if y0 <= y < y1 and x0 <= x < x1:
                continue
            i = row * stride + x * bpp
            d = max(abs(pa[i + k] - pb[i + k]) for k in range(3))
            total += 1
            diff_sum += d
            changed += d > 12
    return changed / total, diff_sum / total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("rect", type=int, nargs=4)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    img = Path(args.image)
    w, h = png_size(img)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    mask = gray_png(w, h, args.rect)
    (OUT / f"{stamp}_mask.png").write_bytes(mask)
    payload = {"prompt": args.prompt, "negative_prompt": "", "seed": args.seed, "width": w, "height": h,
               "strength": args.strength, "batch_count": 1,
               "init_images": [base64.b64encode(img.read_bytes()).decode()],
               "mask": base64.b64encode(mask).decode(), "mask_blur": 4}
    t0 = time.time()
    req = urllib.request.Request(URL + "/sdapi/v1/img2img", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1200) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read()[:1500]!r}  -> the API rejects this request")
    out = OUT / f"{stamp}_s{args.strength}.png"
    out.write_bytes(base64.b64decode(res["images"][0].split(",", 1)[-1]))
    frac, mean = outside_change(img, out, args.rect, OUT)
    meta = {k: v for k, v in payload.items() if k not in ("init_images", "mask")}
    meta.update({"input": str(img), "rect": args.rect, "elapsed_seconds": round(time.time() - t0, 1),
                 "outside_pixels_changed": round(frac, 4), "outside_mean_diff": round(mean, 2)})
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    print(f"{meta['elapsed_seconds']}s -> {out.name}")
    print(f"outside the mask: {frac:.1%} of pixels changed noticeably, mean difference {mean:.1f}/255")
    print("=> mask HONOURED" if frac < 0.05 else "=> mask IGNORED (whole image redrawn)")


if __name__ == "__main__":
    main()
