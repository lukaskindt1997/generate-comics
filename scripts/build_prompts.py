#!/usr/bin/env python3
"""Milestone D3: build the generation request for every panel of a page plan.

Deterministic (no AI): page plan + approved references + style -> per panel:
  - panel box on the page (pixels) and generation size (same aspect, ~0.9 MP)
  - reference image: approved references of the characters/objects in the panel,
    composed side by side into ONE image at the generation size (compose_refs.swift)
  - prompt text in a fixed order

Usage:  python3 scripts/build_prompts.py 3
Output: outputs/comic/page-<NNN>/prompts/panel-<n>.json (+ panel-<n>.ref.png)
        Existing prompt files are moved to prompts/old/<timestamp>/ first.
"""
import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMIC = ROOT / "outputs" / "comic"
LIB = ROOT / "library"
CONFIG = ROOT / "config"
SCRIPTS = Path(__file__).resolve().parent

SHOT = {"extreme_wide": "Extreme wide shot", "wide": "Wide shot", "medium_wide": "Medium-wide shot",
        "medium": "Medium shot", "medium_close": "Medium close-up", "close_up": "Close-up",
        "extreme_close_up": "Extreme close-up"}
ANGLE = {"eye_level": "eye-level view", "low": "low-angle view looking up", "high": "high-angle view looking down",
         "birds_eye": "bird's-eye view from high above", "worms_eye": "worm's-eye view from the ground",
         "over_the_shoulder": "view from just behind and over the shoulder", "dutch": "slightly tilted camera"}


# ---------- geometry ----------

def panel_boxes(plan, page):
    """Pixel box (x, y, w, h) of every panel on the page, from the tiers."""
    W, H, m, g = page["width"], page["height"], page["margin"], page["gutter"]
    inner_w, inner_h = W - 2 * m, H - 2 * m
    tiers = plan["tiers"]
    avail_h = inner_h - g * (len(tiers) - 1)
    total_h = sum(t["height"] for t in tiers)
    boxes, y = {}, m
    for t in tiers:
        th = round(avail_h * t["height"] / total_h)
        avail_w = inner_w - g * (len(t["panels"]) - 1)
        total_w = sum(p["width"] for p in t["panels"])
        x = m
        for p in t["panels"]:
            pw = round(avail_w * p["width"] / total_w)
            boxes[p["panel"]] = {"x": x, "y": y, "w": pw, "h": th}
            x += pw + g
        y += th + g
    return boxes


def generation_size(w, h, gen):
    aspect = w / h
    gh = math.sqrt(gen["target_pixels"] / aspect)
    gw = aspect * gh
    snap = lambda v: int(min(gen["max_side"], max(gen["min_side"], round(v / gen["multiple"]) * gen["multiple"])))
    return snap(gw), snap(gh)


# ---------- references ----------

def find_ref(item_id):
    for kind in ("characters", "objects", "locations"):
        f = LIB / kind / item_id / "ref.json"
        if f.exists():
            ref = json.loads(f.read_text())
            if ref.get("approved"):
                return ref, f.parent
    return None, None


KIND_OF_DIR = {"characters": "character", "objects": "object", "locations": "location"}


def library_entry(item_id):
    """(ref, kind_dir) for any library entry, approved or not."""
    for kind in ("characters", "objects", "locations"):
        f = LIB / kind / item_id / "ref.json"
        if f.exists():
            return json.loads(f.read_text()), kind
    return None, None


def brief_entities():
    out = {}
    for bf in sorted(COMIC.glob("chapter-*/brief.json")):
        b = json.loads(bf.read_text())
        for key in ("characters", "objects", "locations"):
            for item in b.get(key, []):
                out.setdefault(item["id"], {"name": item["name"], "kind": KIND_OF_DIR[key]})
    return out


def required_references(panel, object_ok):
    """Every story entity in the panel with its reference status. blocking = generation must
    wait until it is approved."""
    brief = brief_entities()
    rows = []
    def add(item_id, role):
        ref, kind_dir = library_entry(item_id)
        known = brief.get(item_id)
        if not ref and not known:
            return  # generic thing (a merlon, smoke): no reference needed
        name = ref["name"] if ref else known["name"]
        kind = KIND_OF_DIR[kind_dir] if ref else known["kind"]
        if role == "character":
            needed, why = True, "character in the panel"
        elif role == "object":
            needed = object_ok
            why = "large enough in this shot" if object_ok else f"small in a {panel['shot']} shot: text only"
        else:
            needed, why = False, "location: described in the prompt (no image)"
        status = ("approved" if ref and ref.get("approved") else "unapproved" if ref else "missing")
        if role == "location":
            status = "described" if ref else "missing"
        rows.append({"id": item_id, "name": name, "kind": kind,
                     "path": f"{kind_dir}/{item_id}" if ref else None,
                     "status": status, "needed": needed, "blocking": needed and status != "approved", "why": why})
    for c in panel["characters"]:
        add(c["id"], "character")
    for o in panel["objects"]:
        add(o, "object")
    if panel.get("location"):
        add(panel["location"], "location")
    seen, unique = set(), []
    for r in rows:
        if r["id"] not in seen:
            seen.add(r["id"]); unique.append(r)
    return unique


def look(folder):
    """Positive, short generation prompt of a reference (written/cached by refprompts)."""
    import refprompts
    return refprompts.ensure_generation_prompt(folder).rstrip(".")


def lc(text):
    """Lower-case the first letter, for text placed mid-sentence."""
    text = text.strip()
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text


def describe_side(i, n, vertical):
    if n == 1:
        return "the whole reference image"
    if n == 2:
        return (["the top half", "the bottom half"] if vertical else ["the left half", "the right half"])[i]
    return f"part {i + 1} of {n} from the {'top' if vertical else 'left'}"


def stack_vertically(files, gw, gh):
    """Choose the arrangement in which the reference images come out larger."""
    import struct
    def size(f):  # PNG header: width/height at bytes 16-24
        with open(f, "rb") as fh:
            head = fh.read(24)
        return struct.unpack(">II", head[16:24])
    n = len(files)
    def scale(vertical):
        cw, ch = (gw, gh / n) if vertical else (gw / n, gh)
        return min(min(cw / w, ch / h) for w, h in map(size, files))
    return n > 1 and scale(True) > scale(False)


# ---------- prompt ----------

HARD_STOP_FLAGS = {"nudity", "intimacy", "sexual_violence"}


class NeedsReview(Exception):
    pass


def safety_gate(panel):
    """Hard rule in code (not in the prompt): no nudity/intimacy/sexual content in a panel
    with a minor or a character of unknown age. Such a panel is not built."""
    flags = set(panel.get("content_flags") or [])
    if not flags & HARD_STOP_FLAGS:
        return
    for c in panel["characters"]:
        age = "unknown"
        for kind in ("characters",):  # age is known even before a reference is approved
            f = LIB / kind / c["id"] / "ref.json"
            if f.exists():
                age = json.loads(f.read_text()).get("age_category", "unknown")
        if age in ("child", "teen", "unknown"):
            raise NeedsReview(f"panel {panel['panel']}: flags {sorted(flags & HARD_STOP_FLAGS)} with "
                              f"{c['id']} (age: {age}). Not built; needs human review and a re-plan.")


def build_panel(panel, box, page_cfg, style, out_dir):
    safety_gate(panel)
    gw, gh = generation_size(box["w"], box["h"], page_cfg["generation"])
    refs, skipped = [], []
    for c in panel["characters"]:
        ref, folder = find_ref(c["id"])
        (refs if ref else skipped).append({"id": c["id"], "ref": ref, "folder": folder, "kind": "character",
                                           "why": None if ref else "no approved reference"})
    object_ok = panel["shot"] in page_cfg["reference_rules"]["object_refs_only_for_shots"]
    for o in panel["objects"]:
        ref, folder = find_ref(o)
        if not ref:
            if any((LIB / k / o / "ref.json").exists() for k in ("objects", "locations")):
                skipped.append({"id": o, "why": "no approved reference"})
            continue  # objects without a library entry are just described in the text
        if object_ok:
            refs.append({"id": o, "ref": ref, "folder": folder, "kind": "object"})
        else:
            skipped.append({"id": o, "why": f"object small in a {panel['shot']} shot; described in text only"})

    parts = []
    ref_png = None
    if refs:
        files = [str(r["folder"] / (r["ref"]["approved"].get("sheet") or r["ref"]["approved"]["file"])) for r in refs]
        vertical = stack_vertically(files, gw, gh)
        names = [f"{describe_side(i, len(refs), vertical)} shows {r['ref']['name']} "
                 f"({'character' if r['kind'] == 'character' else 'object'} reference sheet)"
                 for i, r in enumerate(refs)]
        parts.append("Reference image: " + "; ".join(names) + ". Use the reference only for how they look "
                     "(face, hair, clothing, shape, colours). Draw ONE single continuous scene with its own "
                     "background, each subject appearing once.")
        ref_png = out_dir / f"panel-{panel['panel']}.ref.png"
        subprocess.run(["swift", str(SCRIPTS / "compose_refs.swift"), str(ref_png), str(gw), str(gh), *files]
                       + (["--vertical"] if vertical else []), check=True, capture_output=True)

    parts.append(f"{SHOT[panel['shot']]}, {ANGLE[panel['angle']]}. {panel['description']}")
    described = []
    for c in panel["characters"]:
        ref, folder = find_ref(c["id"])
        if ref:
            described.append(ref["name"])
        # The short, positive generation prompt, not the long description (which may contain
        # "no X" phrases: FLUX tends to draw what is named).
        who = f"{ref['name']} ({look(folder)})" if ref else c["id"].replace("_", " ")
        parts.append(f"{who}: {lc(c['position'])}; {lc(c['pose'])}; expression: {lc(c['expression'])}; "
                     f"facing {lc(c['facing'])}.")
    for o in panel["objects"]:
        ref, folder = find_ref(o)
        if ref:
            parts.append(f"{ref['name']}: {look(folder)}")
    if described:
        parts.append(f"Appearance of {', '.join(described)}: follow the descriptions above and the reference "
                     "image exactly; they override any other mention of hair, clothing or accessories.")
    location_used = None
    if panel.get("location"):
        loc, kind_dir = library_entry(panel["location"])
        if loc and kind_dir == "locations":
            parts.append(f"Location, {loc['name']}: {look(LIB / 'locations' / loc['id'])}.")
            location_used = {"id": loc["id"], "name": loc["name"],
                             "description_sha256": hashlib.sha256(loc["description"].encode()).hexdigest()}
    parts.append(f"Lighting: {panel['lighting'].rstrip('.')}.")
    if panel["text_space"] and (panel["captions"] or panel["dialogue"]):
        parts.append(f"Keep this area calm and simple (plain sky, wall or shadow), ready for lettering added "
                     f"later: {lc(panel['text_space']).rstrip('.')}.")
    parts.append(style["prompt_block"])
    parts.append(style["always"])
    prompt = "\n".join(parts)

    record = {
        "panel": panel["panel"],
        "page_box": box,
        "generation_size": {"width": gw, "height": gh},
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "references": [{"id": r["id"], "name": r["ref"]["name"], "version": r["ref"]["approved"]["version"],
                        "file": str((r["folder"] / (r["ref"]["approved"].get("sheet") or r["ref"]["approved"]["file"])).relative_to(ROOT))}
                       for r in refs],
        "reference_image": str(ref_png.relative_to(ROOT)) if ref_png else None,
        "skipped_references": [{"id": s["id"], "why": s["why"]} for s in skipped],
        "required_references": required_references(panel, object_ok),
        "location": location_used,
        "style": style["name"],
    }
    (out_dir / f"panel-{panel['panel']}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=int)
    args = ap.parse_args()

    folder = COMIC / f"page-{args.page:03d}"
    plan = json.loads((folder / "page_plan.json").read_text())
    page_cfg = json.loads((CONFIG / "page.json").read_text())
    style = json.loads((CONFIG / "style.json").read_text())
    out_dir = folder / "prompts"
    if out_dir.exists() and any(out_dir.glob("panel-*")):
        old = out_dir / "old" / datetime.now().strftime("%Y%m%d-%H%M%S")
        old.mkdir(parents=True)
        for f in out_dir.glob("panel-*"):
            shutil.move(str(f), old / f.name)
    out_dir.mkdir(parents=True, exist_ok=True)

    boxes = panel_boxes(plan, page_cfg)
    blocked = []
    for panel in plan["panels"]:
        try:
            r = build_panel(panel, boxes[panel["panel"]], page_cfg, style, out_dir)
        except NeedsReview as e:
            blocked.append(str(e))
            print("BLOCKED:", e)
            continue
        refs = ", ".join(f"{x['id']} v{x['version']}" for x in r["references"]) or "none"
        print(f"panel {r['panel']}: page box {r['page_box']['w']}x{r['page_box']['h']} -> generate "
              f"{r['generation_size']['width']}x{r['generation_size']['height']}, refs: {refs}"
              + (f", skipped: {[s['id'] for s in r['skipped_references']]}" if r["skipped_references"] else ""))
    print(f"-> {out_dir}")
    if blocked:
        sys.exit(f"{len(blocked)} panel(s) blocked by the safety rule; see above.")


if __name__ == "__main__":
    main()
