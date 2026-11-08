#!/usr/bin/env python3
"""Collect the current state of comic pages and render them to a PDF.

Per panel the image is: the chosen one (selection.json), else the best assessed, else the
newest; panels without images become grey placeholders. Captions and dialogue are drawn as
text boxes at their planned positions (simple boxes until real lettering, Milestone E).
Shared with the UI: page_state() is what the Pages view shows.

Usage:  python3 scripts/render_pages.py 3 [4 5 ...] --out outputs/export/pages.pdf
        python3 scripts/render_pages.py --all --out outputs/export/all.pdf
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_prompts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMIC = ROOT / "outputs" / "comic"
SCRIPTS = Path(__file__).resolve().parent


def page_cfg():
    return json.loads((ROOT / "config" / "page.json").read_text())


def panel_image(pdir):
    """(path, status) for the image shown for a panel."""
    sel_file = pdir.parent / "selection.json"
    if sel_file.exists():
        sel = json.loads(sel_file.read_text()).get("selected", {}).get(pdir.name.split("-")[1])
        if sel and (COMIC / sel).exists():
            return COMIC / sel, "chosen"
    a_file = pdir / "assessment.json"
    if a_file.exists():
        best = json.loads(a_file.read_text()).get("best")
        if best and (pdir / best).exists():
            return pdir / best, "best assessed"
    pngs = sorted(pdir.glob("*.png"))
    if pngs:
        return pngs[-1], "newest"
    return None, "not generated"


def page_state(page):
    folder = COMIC / f"page-{page:03d}"
    plan_file = folder / "page_plan.json"
    if not plan_file.exists():
        return None
    plan = json.loads(plan_file.read_text())
    cfg = page_cfg()
    boxes = build_prompts.panel_boxes(plan, cfg)
    panels = []
    for p in plan["panels"]:
        img, status = panel_image(folder / "panels" / f"panel-{p['panel']}")
        texts = [{"kind": "caption", "text": c["text"], "placement": c["placement"]} for c in p["captions"]]
        texts += [{"kind": "speech", "text": f"{d['speaker']}: {d['text']}", "placement": d["placement"]}
                  for d in p["dialogue"]]
        panels.append({"panel": p["panel"], "box": boxes[p["panel"]], "image": str(img) if img else None,
                       "image_rel": str(img.relative_to(COMIC)) if img else None, "status": status, "texts": texts})
    return {"page": page, "panels": panels}


def all_pages():
    return sorted(int(f.parent.name.split("-")[1]) for f in COMIC.glob("page-*/page_plan.json"))


def render(pages, out):
    cfg = page_cfg()
    states = [s for s in (page_state(p) for p in pages) if s]
    if not states:
        raise ValueError("no planned pages to render")
    spec = {"width": cfg["width"], "height": cfg["height"], "dpi": 300, "lettering": cfg["lettering"],
            "pages": [{"page": s["page"], "panels": [{k: p[k] for k in ("panel", "box", "image", "texts")}
                                                     for p in s["panels"]]} for s in states]}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(spec, fh)
    subprocess.run(["swift", str(SCRIPTS / "render_pages.swift"), fh.name, str(out)], check=True,
                   capture_output=True)
    Path(fh.name).unlink()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", type=int, nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    pages = all_pages() if args.all else args.pages
    print(render(pages, args.out))


if __name__ == "__main__":
    main()
