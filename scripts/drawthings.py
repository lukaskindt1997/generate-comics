"""Small client for the Draw Things HTTP API (standard library only).

Findings (PLAN.md, Milestone A and D1):
  - GET  /                    -> the app's current settings (model, sampler, steps, ...)
  - POST /sdapi/v1/txt2img    -> text to image
  - POST /sdapi/v1/img2img    -> with strength 1.0 and ONE init image, FLUX.2 Klein uses
                                 the image as a reference (edit mode). Several images do not
                                 work: combine them first (compose_refs.swift).
Responses contain base64 PNGs in "images".
"""
import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "http://127.0.0.1:7860"


class DrawThingsError(RuntimeError):
    pass


def _call(path, payload=None, timeout=1200):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise DrawThingsError(f"HTTP {e.code} on {path}: {e.read()[:500]!r}") from e
    except urllib.error.URLError as e:
        raise DrawThingsError(f"Draw Things not reachable at {URL} (is the app open with the API server on?): {e}") from e


def settings():
    return _call("/", timeout=10)


def generate(prompt, seed, width, height, reference_png=None, strength=1.0):
    """Generate one image. With reference_png (bytes) it uses edit/reference mode.

    Returns (png_bytes, record) where record holds everything needed to reproduce it.
    """
    app = settings()
    request = {"prompt": prompt, "negative_prompt": "", "seed": int(seed),
               "width": int(width), "height": int(height), "batch_count": 1}
    path = "/sdapi/v1/txt2img"
    if reference_png is not None:
        path = "/sdapi/v1/img2img"
        request["strength"] = strength
        request["init_images"] = [base64.b64encode(reference_png).decode()]
    t0 = time.time()
    res = _call(path, request)
    images = res.get("images") or []
    if not images:
        raise DrawThingsError(f"no image returned; response keys: {list(res)}")
    png = base64.b64decode(images[0].split(",", 1)[-1])
    req_record = {k: v for k, v in request.items() if k != "init_images"}
    if reference_png is not None:
        req_record["reference_sha256"] = hashlib.sha256(reference_png).hexdigest()
    record = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "backend": "drawthings-http",
        "endpoint": path,
        "model": app.get("model"),
        "request": req_record,
        "effective_settings": {k: app.get(k) for k in
                               ("sampler", "steps", "guidance_scale", "shift", "seed_mode", "loras")},
        "elapsed_seconds": round(time.time() - t0, 1),
        "image_sha256": hashlib.sha256(png).hexdigest(),
    }
    return png, record


def save(png, record, path):
    """Write image + JSON sidecar. Never overwrites."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    path.with_suffix(".json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return path
