#!/usr/bin/env python3
"""Milestone B4: local review UI for the transcribed text.

Works page by page: every paragraph is shown next to a crop of the photo it came from,
with the flags from check_text.py; "Finish page" moves on to the next page.
Edits are saved automatically to outputs/text/review.json (previous version kept as
review.json.bak) and exported to outputs/text/prologue.reviewed.md and, per page, to
outputs/text/reviewed/p<N>.md (+ status.json saying which pages are finished).
Nothing else is modified. Standard library only; runs on 127.0.0.1 only.

Usage:  python3 scripts/review_server.py [--page 4]   (opens http://127.0.0.1:8765)
        python3 scripts/review_server.py --reset   (start over from the OCR output)
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clean_ocr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source"
OCR_DIR = ROOT / "outputs" / "ocr"
TEXT_DIR = ROOT / "outputs" / "text"
CACHE = ROOT / "outputs" / "review_cache"
STATE = TEXT_DIR / "review.json"
EXPORT = TEXT_DIR / "prologue.reviewed.md"
HTML = Path(__file__).resolve().parent / "review.html"
SCRIPTS = Path(__file__).resolve().parent
COMIC = ROOT / "outputs" / "comic"
DECISIONS = SOURCE / "decisions.json"
PORT = 8765
ROTATION = {"up": 0, "right": 90, "down": 180, "left": 270}

lock = threading.Lock()


# ---------- state ----------

def union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["w"] for b in boxes)
    y1 = max(b["y"] + b["h"] for b in boxes)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def initial_state():
    pages = []
    for n, entry in enumerate(clean_ocr.read_manifest(SOURCE / "manifest.yaml")):
        stem = Path(entry["photo"]).stem
        paras = [p for p in (TEXT_DIR / f"{stem}.clean.txt").read_text().split("\n\n") if p.strip()]
        meta = json.loads((TEXT_DIR / f"{stem}.lines.json").read_text())
        lines = meta["lines"]
        checks = json.loads((TEXT_DIR / f"{stem}.checks.json").read_text())["flags"]
        items = [{"text": t.strip(), "box": union([l for l in lines if l["para"] == i]),
                  "flags": [], "done": False} for i, t in enumerate(paras)]
        for f in checks:
            pi = f.get("paragraph")
            if pi is None and f.get("line_text"):
                pi = next((l["para"] for l in lines if l["text"] == f["line_text"]), None)
            pi = pi if pi is not None and pi < len(items) else 0
            flag = {k: f.get(k) for k in ("type", "word", "suggestion", "context", "other_reading")}
            flag["status"] = "open"
            items[pi]["flags"].append(flag)
        pages.append({"stem": stem, "page": entry["page"], "photo": entry["photo"],
                      "continues_previous": n > 0 and not meta["starts_with_indent"],
                      "paragraphs": items})
    return {"version": 1, "pages": pages}


def load_state(reset=False):
    if STATE.exists() and not reset:
        return json.loads(STATE.read_text())
    if STATE.exists():
        shutil.copy(STATE, STATE.with_suffix(".json.bak"))
    state = initial_state()
    save_state(state)
    return state


def save_state(state):
    if STATE.exists():
        shutil.copy(STATE, STATE.with_suffix(".json.bak"))
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(STATE)
    export(state)


def export(state):
    """Write the combined text, plus one file per page so later steps can work page by page."""
    out = []
    page_dir = TEXT_DIR / "reviewed"
    page_dir.mkdir(exist_ok=True)
    status = {}
    for pg in state["pages"]:
        paras = []
        for p in pg["paragraphs"]:
            # A blank line inside a text box splits it into two paragraphs.
            for part in re.split(r"\n\s*\n", p["text"]):
                part = re.sub(r"\s*\n\s*", " ", part).strip()
                if part:
                    paras.append(part)
        (page_dir / f"p{pg['page']}.md").write_text("\n\n".join(paras) + "\n")
        status[pg["page"]] = {"photo": pg["photo"], "finished": bool(pg.get("finished")),
                              "continues_previous": pg["continues_previous"],
                              "file": f"p{pg['page']}.md"}
        if not paras:
            continue
        marker = f"⟨p{pg['page']}⟩"
        if out and pg["continues_previous"]:
            out[-1] += f" {marker} " + paras.pop(0)
        else:
            paras[0] = f"{marker} " + paras[0]
        out.extend(paras)
    EXPORT.write_text("\n\n".join(out) + "\n")
    (page_dir / "status.json").write_text(json.dumps(status, indent=2))


# ---------- questions & decisions ----------

def load_decisions():
    return json.loads(DECISIONS.read_text()) if DECISIONS.exists() else []


def scene_info(page):
    scene_file = COMIC / f"page-{page:03d}" / "scene.json"
    if not scene_file.exists():
        return {"exists": False}
    scene = json.loads(scene_file.read_text())
    answers = {d["question"]: d for d in load_decisions() if d["page"] == page}
    questions = []
    for q in scene["questions"]:
        if isinstance(q, str):  # older analyses: plain questions without recommendation
            q = {"question": q, "recommendation": "", "reason": "", "scope": "story"}
        saved = answers.get(q["question"])
        questions.append({
            "question": q["question"], "recommendation": q["recommendation"], "reason": q["reason"],
            "default_scope": q["scope"],
            "answer": saved["answer"] if saved else "",
            "scope": saved["scope"] if saved else q["scope"],
            "source": saved.get("source", "user") if saved else "none"})
    return {"exists": True, "summary": scene["summary"], "captions": scene["captions"],
            "questions": questions}


def save_answers(page, answers):
    """Replace this page's answers in decisions.json. An answer equal to the
    recommendation is stored as source "recommended"; an empty answer is dropped
    (= let the planner decide)."""
    from datetime import datetime, timezone
    keep = [d for d in load_decisions() if d["page"] != page]
    now = datetime.now(timezone.utc).isoformat()
    for a in answers:
        if a["answer"].strip():
            is_rec = a["answer"].strip() == (a.get("recommendation") or "").strip()
            keep.append({"page": page, "question": a["question"], "answer": a["answer"].strip(),
                         "scope": a["scope"] if a["scope"] in ("story", "page") else "story",
                         "source": "recommended" if is_rec else "user",
                         "reason": a.get("reason", ""), "updated_utc": now})
    if DECISIONS.exists():
        shutil.copy(DECISIONS, DECISIONS.with_suffix(".json.bak"))
    DECISIONS.write_text(json.dumps(keep, indent=2, ensure_ascii=False))


def run_analysis(page):
    log = []
    for script in ("prepare_page.py", "analyze_scene.py"):
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), str(page)],
                              cwd=ROOT, capture_output=True, text=True, timeout=1200)
        log.append((proc.stdout + proc.stderr).strip())
        if proc.returncode != 0:
            return False, "\n".join(log)
    return True, "\n".join(log)


def plan_info(page):
    folder = COMIC / f"page-{page:03d}"
    plan_file = folder / "page_plan.json"
    if not plan_file.exists():
        return {"exists": False}
    meta_file = folder / "page_plan.meta.json"
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    return {"exists": True, "plan": json.loads(plan_file.read_text()),
            "problems": meta.get("problems", []), "created_utc": meta.get("created_utc")}


def run_plan(page, note=""):
    cmd = [sys.executable, str(SCRIPTS / "plan_page.py"), str(page)]
    if note.strip():
        cmd += ["--note", note.strip()]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1500)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


# ---------- images ----------

def sips(*args):
    subprocess.run(["sips", *map(str, args)], check=True, capture_output=True)


def upright(stem):
    path = CACHE / "upright" / f"{stem}.jpg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        vision = json.loads((OCR_DIR / f"{stem}.vision.json").read_text())
        deg = ROTATION[vision["orientation"]]
        if deg:
            sips("-r", deg, SOURCE / vision["file"], "--out", path)
        else:
            sips("-s", "format", "jpeg", SOURCE / vision["file"], "--out", path)
    return path


def crop(stem, x, y, w, h):
    key = f"{stem}_{x:.4f}_{y:.4f}_{w:.4f}_{h:.4f}"
    path = CACHE / "crops" / f"{key}.jpg"
    if path.exists():
        return path
    vision = json.loads((OCR_DIR / f"{stem}.vision.json").read_text())
    W, H = vision["imageWidth"], vision["imageHeight"]
    padx, pady = 0.03, 0.012
    x0, y0 = max(0.0, x - padx), max(0.0, y - pady)
    x1, y1 = min(1.0, x + w + padx), min(1.0, y + h + pady)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.jpg")
    sips("-c", int((y1 - y0) * H), int((x1 - x0) * W), "--cropOffset", int(y0 * H), int(x0 * W),
         upright(stem), "--out", tmp)
    sips("--resampleWidth", 1100, tmp, "--out", path)
    tmp.unlink()
    return path


# ---------- server ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith(("text/html", "application/json")) else "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                self.send(200, HTML.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                with lock:
                    self.send(200, json.dumps(load_state()).encode(), "application/json")
            elif url.path == "/api/scene":
                self.send(200, json.dumps(scene_info(int(q["page"]))).encode(), "application/json")
            elif url.path == "/api/plan":
                self.send(200, json.dumps(plan_info(int(q["page"]))).encode(), "application/json")
            elif url.path == "/crop":
                with lock:
                    p = crop(q["stem"], float(q["x"]), float(q["y"]), float(q["w"]), float(q["h"]))
                self.send(200, p.read_bytes(), "image/jpeg")
            elif url.path == "/page":
                with lock:
                    p = upright(q["stem"])
                self.send(200, p.read_bytes(), "image/jpeg")
            else:
                self.send(404, b"not found", "text/plain")
        except Exception as e:  # show errors in the browser instead of hanging
            self.send(500, str(e).encode(), "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if path == "/api/answers":
            data = json.loads(body)
            with lock:
                save_answers(int(data["page"]), data["answers"])
            return self.send(200, b'{"ok": true}', "application/json")
        if path == "/api/analyse":
            ok, log = run_analysis(int(json.loads(body)["page"]))
            return self.send(200, json.dumps({"ok": ok, "log": log}).encode(), "application/json")
        if path == "/api/plan":
            data = json.loads(body)
            ok, log = run_plan(int(data["page"]), data.get("note", ""))
            return self.send(200, json.dumps({"ok": ok, "log": log}).encode(), "application/json")
        if path != "/api/state":
            return self.send(404, b"not found", "text/plain")
        try:
            state = json.loads(body)
            assert isinstance(state.get("pages"), list)
            with lock:
                save_state(state)
            self.send(200, b'{"ok": true}', "application/json")
        except Exception as e:
            self.send(400, str(e).encode(), "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="discard review.json (backed up) and start over")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--page", type=int, help="open this book page (default: first unfinished)")
    args = ap.parse_args()
    load_state(reset=args.reset)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}" + (f"/#p{args.page}" if args.page else "")
    print(f"Review UI on {url}  (Ctrl+C to stop)\nSaving to {STATE}\nExport:   {EXPORT}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
