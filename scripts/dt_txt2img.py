#!/usr/bin/env python3
"""Milestone A: Draw Things connectivity test.

Sends one txt2img request to the local Draw Things HTTP API and saves the
image plus a JSON sidecar with everything needed to reproduce it.
Uses only the Python standard library.

Usage:
    python3 scripts/dt_txt2img.py --prompt "..." [--seed 1234] [--width 768] [--height 1024]
"""
import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:7860"
OUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "milestone_a"


def http_json(url, payload=None, timeout=600):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--prompt", required=True)
    p.add_argument("--negative", default="")
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--width", type=int, default=768)
    p.add_argument("--height", type=int, default=1024)
    p.add_argument("--steps", type=int, default=None, help="default: whatever the app is set to")
    p.add_argument("--tag", default="test", help="short label used in the filename")
    args = p.parse_args()

    # 1. Read the app's current settings (model, sampler, etc.) for the record.
    try:
        app_config = http_json(args.url + "/", timeout=10)
    except Exception as e:
        sys.exit(f"Cannot reach Draw Things at {args.url}: {e}")

    # 2. Build the request: only override what we control; the app supplies the rest.
    request = {
        "prompt": args.prompt,
        "negative_prompt": args.negative,
        "seed": args.seed,
        "width": args.width,
        "height": args.height,
        "batch_count": 1,
    }
    if args.steps is not None:
        request["steps"] = args.steps

    # 3. Generate.
    t0 = time.time()
    try:
        result = http_json(args.url + "/sdapi/v1/txt2img", request)
    except Exception as e:
        sys.exit(f"Generation failed: {e}")
    elapsed = round(time.time() - t0, 1)

    images = result.get("images") or []
    if not images:
        sys.exit(f"No images returned. Response keys: {list(result)}")

    # 4. Save image(s) + sidecar. Timestamped names, never overwrite.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}_{args.tag}_seed{args.seed}"
    saved = []
    for i, b64 in enumerate(images):
        raw = base64.b64decode(b64.split(",", 1)[-1])
        path = OUT_DIR / f"{base}_{i}.png"
        if path.exists():
            sys.exit(f"Refusing to overwrite {path}")
        path.write_bytes(raw)
        saved.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})

    sidecar = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "backend": "drawthings-http",
        "endpoint": args.url + "/sdapi/v1/txt2img",
        "model": app_config.get("model"),
        "request": request,
        "effective_settings": {k: app_config.get(k) for k in
                               ("sampler", "steps", "guidance_scale", "shift", "seed_mode", "loras", "controls")},
        "elapsed_seconds": elapsed,
        "images": saved,
        "response_extra_keys": [k for k in result if k != "images"],
        "app_config_snapshot": app_config,
    }
    sidecar_path = OUT_DIR / f"{base}.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False))

    print(f"OK in {elapsed}s, model={sidecar['model']}")
    for s in saved:
        print(f"  {OUT_DIR / s['file']}  sha256={s['sha256'][:16]}")
    print(f"  {sidecar_path}")


if __name__ == "__main__":
    main()
