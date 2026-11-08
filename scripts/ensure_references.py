#!/usr/bin/env python3
"""Create the library entries a page plan needs (description only, no images yet).

For every panel of the given pages, the references a panel would actually use (characters
in the panel; objects in medium-wide or closer shots, see config/page.json) that don't exist
in the library yet are created with describe_reference.py. Locations are created too, as
description only (their positive generation prompt goes into the panel prompts; no images).
Existing entries are never touched.

Runs automatically after plan_page.py. Usage:  python3 scripts/ensure_references.py 3 [4 5 ...]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_prompts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMIC = ROOT / "outputs" / "comic"
SCRIPTS = Path(__file__).resolve().parent


def needed_missing(page, object_shots):
    plan_file = COMIC / f"page-{page:03d}" / "page_plan.json"
    if not plan_file.exists():
        return []
    out = {}
    for panel in json.loads(plan_file.read_text())["panels"]:
        for r in build_prompts.required_references(panel, panel["shot"] in object_shots):
            # characters/objects a panel uses as image, and every location (description only)
            if r["status"] == "missing" and (r["needed"] or r["kind"] == "location"):
                out.setdefault(r["id"], r)
    return list(out.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", type=int, nargs="+")
    args = ap.parse_args()
    object_shots = json.loads((ROOT / "config" / "page.json").read_text())["reference_rules"]["object_refs_only_for_shots"]
    todo = {}
    for page in args.pages:
        for r in needed_missing(page, object_shots):
            todo.setdefault(r["id"], r)
    if not todo:
        print("all needed references exist")
        return
    for i, r in enumerate(todo.values(), 1):
        print(f"[{i}/{len(todo)}] creating {r['kind']} '{r['name']}'…", flush=True)
        subprocess.run([sys.executable, str(SCRIPTS / "describe_reference.py"), r["kind"], r["id"], r["name"]],
                       check=False)


if __name__ == "__main__":
    main()
