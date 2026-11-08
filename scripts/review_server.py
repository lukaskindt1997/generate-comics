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
import os
import re
import shutil
import subprocess
import sys
import threading
import time
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
LIB = ROOT / "library"
# Job queue for Draw Things / Claude work on references: one job at a time, in order.
job_queue = []   # [{"key", "rel", "page", "label", "kind", "cmd", "count", "log"}]
job_current = {"key": None, "rel": None, "page": None, "label": None, "kind": None, "count": None,
               "log": None, "log_offset": 0, "started": None, "pid": None}
job_cond = threading.Condition()


JOB_STATE = ROOT / "outputs" / "job_state.json"


def save_job_state():
    """Persist queue + running job (with its pid) so a server restart loses nothing.
    Call with job_cond held."""
    JOB_STATE.parent.mkdir(parents=True, exist_ok=True)
    cur = {k: v for k, v in job_current.items() if k != "proc"} if job_current["key"] else None
    tmp = JOB_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"current": cur, "queue": job_queue}, indent=2))
    tmp.replace(JOB_STATE)


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def restore_job_state():
    """On startup: restore the queue; adopt a job that is still running from before."""
    if not JOB_STATE.exists():
        return
    data = json.loads(JOB_STATE.read_text())
    with job_cond:
        job_queue[:] = data.get("queue") or []
        cur = data.get("current")
        if cur and pid_alive(cur.get("pid")):
            job_current.update(cur)
            print(f"adopted running job: {cur['label']} (pid {cur['pid']})")
        save_job_state()


def job_worker():
    while True:
        # A job adopted from a previous server run: wait until its process ends.
        while job_current["key"] and job_current.get("pid") and pid_alive(job_current["pid"]):
            time.sleep(2)
        with job_cond:
            if job_current["key"]:
                job_current.update(key=None, rel=None, page=None, label=None, kind=None, count=None,
                                   log=None, log_offset=0, started=None, pid=None)
                save_job_state()
            while not job_queue:
                job_cond.wait()
            job = job_queue.pop(0)
            log_path = Path(job["log"])
            log_path.parent.mkdir(parents=True, exist_ok=True)
            job_current.update(key=job["key"], rel=job.get("rel"), page=job.get("page"), label=job["label"],
                               kind=job["kind"], count=job.get("count"), log=str(log_path),
                               log_offset=log_path.stat().st_size if log_path.exists() else 0,
                               started=time.time(), pid=None, cmd=job["cmd"])
            log = open(log_path, "a")
            # Own session: the job keeps running if the server is stopped or restarted.
            proc = subprocess.Popen(job["cmd"], cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
            job_current["pid"] = proc.pid
            save_job_state()
        proc.wait()
        log.close()
        with job_cond:
            job_current.update(key=None, rel=None, page=None, label=None, kind=None, count=None,
                               log=None, log_offset=0, started=None, pid=None)
            save_job_state()


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


# ---------- reference library ----------

def safe_lib_path(rel):
    p = (LIB / rel).resolve()
    if not str(p).startswith(str(LIB.resolve()) + "/"):
        raise ValueError("path outside library")
    return p


def list_refs():
    refs = []
    for ref_file in sorted(LIB.glob("*/*/ref.json")):
        folder = ref_file.parent
        rel = str(folder.relative_to(LIB))
        ref = json.loads(ref_file.read_text())
        cands = []
        for png in sorted((folder / "candidates").glob("*.png"), reverse=True):
            meta = png.with_suffix(".json")
            m = json.loads(meta.read_text()) if meta.exists() else {}
            cands.append({"file": f"{rel}/candidates/{png.name}", "seed": m.get("request", {}).get("seed"),
                          "description_used": m.get("description_used"), "parent": m.get("parent"),
                          "edit_instruction": m.get("edit_instruction"), "view": m.get("view"),
                          "used_components": m.get("components"), "parent_components": None})
        assessment_file = folder / "assessment.json"
        assessment = json.loads(assessment_file.read_text()) if assessment_file.exists() else None
        if assessment:
            by_file = {c["file"]: c for c in assessment["candidates"]}
            for c in cands:
                c["assessment"] = by_file.get(Path(c["file"]).name)
            handled = set(ref.get("accepted_model_habits", [])) | set(ref.get("insisted", []))
            assessment = {k: assessment.get(k) for k in ("best", "best_reason", "description_issues",
                                                          "suggested_description", "description_assessed",
                                                          "created_utc")}
            assessment["persistent_issues"] = [
                p for p in (json.loads(assessment_file.read_text()).get("persistent_issues") or [])
                if p["issue"] not in handled]
        status = ""
        log_file = folder / "generation.log"
        running = job_current["rel"] == rel
        if running and log_file.exists():
            lines = [l for l in log_file.read_text().splitlines() if l.strip()]
            status = lines[-1] if lines else ""
        components = []
        for cid in (ref.get("components") or {}).get("ids") or []:
            cf = LIB / "objects" / cid / "ref.json"
            if cf.exists():
                o = json.loads(cf.read_text())
                components.append({"path": f"objects/{cid}", "name": o["name"], "approved": bool(o.get("approved")),
                                   "has_images": any((cf.parent / "candidates").glob("*.png"))})
        refs.append({"path": rel, "ref": ref, "candidates": cands, "assessment": assessment,
                     "components": components,
                     "running": running, "status": status,
                     "queued": [{"position": i, "kind": j["kind"]} for i, j in
                                ((i + 1, j) for i, j in enumerate(job_queue)) if j.get("rel") == rel],
                     "busy": job_current["key"] is not None or bool(job_queue)})
    return refs


def update_ref_description(rel, description):
    path = safe_lib_path(rel) / "ref.json"
    ref = json.loads(path.read_text())
    if ref["description"] != description:
        ref.setdefault("description_history", []).append(ref["description"])
        ref["description"] = description
        path.write_text(json.dumps(ref, indent=2, ensure_ascii=False))


def ref_name(rel):
    try:
        return json.loads((LIB / rel / "ref.json").read_text())["name"]
    except Exception:
        return rel


def job_overview():
    """What Draw Things / Claude is doing now, and what is queued (for the status box)."""
    with job_cond:
        cur = dict(job_current)
        queue = [dict(j) for j in job_queue]
    current = None
    if cur["key"]:
        step, detail, done = None, "", 0
        log_path = Path(cur["log"])
        if log_path.exists():
            with open(log_path) as fh:
                fh.seek(cur["log_offset"])
                lines = [l.strip() for l in fh.read().splitlines() if l.strip()]
            for l in lines:
                m = re.match(r"\[(\d+)/(\d+)\] (?:panel \d+: )?(generating|done)", l)
                if m:
                    step = int(m.group(1))
                    if m.group(3) == "done":
                        done = step
                elif l.startswith("assessing"):
                    step = "assess" if cur["kind"] != "panels" else step
            detail = lines[-1] if lines else ""
        current = {"rel": cur["rel"], "page": cur["page"], "name": cur["label"], "kind": cur["kind"],
                   "count": cur["count"], "step": step, "done": done, "detail": detail,
                   "elapsed": round(time.time() - cur["started"]) if cur["started"] else 0}
    return {"current": current,
            "queue": [{"rel": j.get("rel"), "page": j.get("page"), "name": j["label"], "kind": j["kind"],
                       "count": j.get("count")} for j in queue]}


def add_job(job, allow_duplicate=False):
    with job_cond:
        if not allow_duplicate and any(j["key"] == job["key"] and j["kind"] == job["kind"] for j in job_queue):
            return False, "This is already in the queue."
        job_queue.append(job)
        save_job_state()
        job_cond.notify()
        waiting = len(job_queue) - 1 + (1 if job_current["key"] else 0)
    return True, "started" if waiting == 0 else f"queued ({waiting} job(s) ahead)"


def start_job(rel, kind, cmd, count=None):
    safe_lib_path(rel)
    return add_job({"key": rel, "rel": rel, "label": ref_name(rel), "kind": kind, "cmd": cmd, "count": count,
                    "log": str(LIB / rel / "generation.log")}, allow_duplicate=(kind == "fix"))


def waiting_components(rel):
    """Names of worn/carried objects of this reference that are not approved yet."""
    ref = json.loads((safe_lib_path(rel) / "ref.json").read_text())
    waiting = []
    for cid in (ref.get("components") or {}).get("ids") or []:
        cf = LIB / "objects" / cid / "ref.json"
        if cf.exists() and not json.loads(cf.read_text()).get("approved"):
            waiting.append(json.loads(cf.read_text())["name"])
    return waiting


def blocked_message(rel):
    w = waiting_components(rel)
    return f"Can't generate yet: approve {', '.join(w)} first (worn/carried by this character)." if w else None


def start_generation(rel, count):
    msg = blocked_message(rel)
    if msg:
        return False, msg
    return start_job(rel, "generate",
                     [sys.executable, str(SCRIPTS / "make_reference.py"), rel, "--count", str(int(count))],
                     count=int(count))


def decide_habit(rel, issue, accept, description=None):
    """Accept a model habit (description adjusted, re-assessment queued) or insist on it."""
    path = safe_lib_path(rel) / "ref.json"
    ref = json.loads(path.read_text())
    if accept:
        ref.setdefault("accepted_model_habits", []).append(issue)
        if description and description != ref["description"]:
            ref.setdefault("description_history", []).append(ref["description"])
            ref["description"] = description
    else:
        ref.setdefault("insisted", []).append(issue)
    path.write_text(json.dumps(ref, indent=2, ensure_ascii=False))
    return start_assessment(rel) if accept else (True, "kept")


def adopt_candidate(rel, candidate_file):
    """Synchronous (Claude only, ~30 s): adapt the description to the candidate; then re-assess."""
    proc = subprocess.run([sys.executable, str(SCRIPTS / "adopt_candidate.py"), rel, Path(candidate_file).name],
                          cwd=ROOT, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        return False, (proc.stdout + proc.stderr).strip()[-1500:], None
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    start_assessment(rel)
    return True, "ok", result


def start_fix(rel, candidate_file):
    msg = blocked_message(rel)
    if msg:
        return False, msg
    name = Path(candidate_file).name
    return start_job(rel, "fix", [sys.executable, str(SCRIPTS / "fix_reference.py"), rel, name], count=1)


def start_views(rel):
    msg = blocked_message(rel)
    if msg:
        return False, msg
    ref = json.loads((safe_lib_path(rel) / "ref.json").read_text())
    n = len(json.loads((ROOT / "config" / "style.json").read_text())["views"].get(ref["kind"], []))
    return start_job(rel, "views", [sys.executable, str(SCRIPTS / "make_views.py"), rel], count=n)


def start_assessment(rel):
    return start_job(rel, "assess", [sys.executable, str(SCRIPTS / "assess_reference.py"), rel])


def unqueue(rel):
    with job_cond:
        before = len(job_queue)
        job_queue[:] = [j for j in job_queue if j.get("rel") != rel and j["key"] != rel]
        save_job_state()
        return before - len(job_queue)


def approve_candidate(rel, file_rel):
    """Copy the chosen candidate to approved/v<N>.png (+ sidecar) and point ref.json at it."""
    from datetime import datetime, timezone
    folder = safe_lib_path(rel)
    src = safe_lib_path(file_rel)
    approved = folder / "approved"
    approved.mkdir(exist_ok=True)
    n = len(list(approved.glob("v*.png"))) + 1
    dst = approved / f"v{n}.png"
    shutil.copy(src, dst)
    if src.with_suffix(".json").exists():
        shutil.copy(src.with_suffix(".json"), dst.with_suffix(".json"))
    ref_file = folder / "ref.json"
    ref = json.loads(ref_file.read_text())
    if ref.get("approved"):
        ref.setdefault("approved_history", []).append(ref["approved"])
    ref["approved"] = {"version": n, "file": f"approved/v{n}.png", "from_candidate": src.name,
                       "description": ref["description"],
                       "approved_utc": datetime.now(timezone.utc).isoformat()}
    meta = json.loads(src.with_suffix(".json").read_text()) if src.with_suffix(".json").exists() else {}
    if not meta.get("view"):
        # Older candidates are already multi-view sheets: use them as the sheet directly;
        # deriving extra views from a sheet would make no sense.
        ref["approved"]["sheet"] = f"approved/v{n}.png"
        ref["approved"]["multi_view_candidate"] = True
    ref_file.write_text(json.dumps(ref, indent=2, ensure_ascii=False))


# ---------- panels (Milestone D4) ----------

def page_folder(page):
    return COMIC / f"page-{int(page):03d}"


def safe_comic_path(rel):
    p = (COMIC / rel).resolve()
    if not str(p).startswith(str(COMIC.resolve()) + "/"):
        raise ValueError("path outside outputs/comic")
    return p


def panels_info(page):
    folder = page_folder(page)
    plan_file = folder / "page_plan.json"
    if not plan_file.exists():
        return {"exists": False}
    plan = json.loads(plan_file.read_text())
    sel_file = folder / "panels" / "selection.json"
    selection = json.loads(sel_file.read_text()).get("selected", {}) if sel_file.exists() else {}
    panels = []
    for p in plan["panels"]:
        n = p["panel"]
        pf = folder / "prompts" / f"panel-{n}.json"
        spec = json.loads(pf.read_text()) if pf.exists() else None
        a_file = folder / "panels" / f"panel-{n}" / "assessment.json"
        assessment = json.loads(a_file.read_text()) if a_file.exists() else None
        by_file = {c["file"]: c for c in (assessment or {}).get("candidates", [])}
        cands = []
        for png in sorted((folder / "panels" / f"panel-{n}").glob("*.png"), reverse=True):
            m = json.loads(png.with_suffix(".json").read_text()) if png.with_suffix(".json").exists() else {}
            cands.append({"file": str(png.relative_to(COMIC)), "seed": m.get("request", {}).get("seed"),
                          "prompt_sha256": m.get("prompt_sha256"), "assessment": by_file.get(png.name)})
        panels.append({"panel": n, "shot": p["shot"], "angle": p["angle"], "purpose": p["purpose"],
                       "description": p["description"], "captions": p["captions"], "dialogue": p["dialogue"],
                       "text_space": p.get("text_space", ""),
                       "spec": spec, "candidates": cands, "selected": selection.get(str(n)),
                       "assessment": {k: assessment.get(k) for k in ("best", "best_reason", "created_utc")}
                                     if assessment else None})
    key = f"page-{int(page)}"
    with job_cond:
        running = job_current["key"] == key
        queued = any(j["key"] == key for j in job_queue)
    for p in panels:
        reqs = (p["spec"] or {}).get("required_references") or []
        used = {r["id"]: r["version"] for r in (p["spec"] or {}).get("references") or []}
        stale = False
        for r in reqs:
            # Live status: approvals may have changed since the prompts were built.
            cur = None
            if not r.get("path"):   # created after the prompt was built?
                kdir = {"character": "characters", "object": "objects", "location": "locations"}[r["kind"]]
                if (LIB / kdir / r["id"] / "ref.json").exists():
                    r["path"] = f"{kdir}/{r['id']}"
            if r.get("path") and (LIB / r["path"] / "ref.json").exists():
                cur = json.loads((LIB / r["path"] / "ref.json").read_text()).get("approved")
            if r["kind"] == "location":
                r["status"] = "described" if r.get("path") else "missing"
            else:
                r["status"] = "approved" if cur else ("unapproved" if r.get("path") else "missing")
            r["blocking"] = r["needed"] and r["status"] != "approved"
            if r["needed"] and cur and used.get(r["id"]) != cur["version"]:
                stale = True   # approved (or re-approved) after the prompt was built
        p["blocked"] = [r for r in reqs if r["blocking"]]
        # Location described in the prompt: outdated if its description changed or it was added later.
        loc_id = next((r["id"] for r in reqs if r["kind"] == "location"), None)
        if loc_id and (LIB / "locations" / loc_id / "ref.json").exists():
            import hashlib
            d = json.loads((LIB / "locations" / loc_id / "ref.json").read_text())["description"]
            used_loc = (p["spec"] or {}).get("location") or {}
            if used_loc.get("description_sha256") != hashlib.sha256(d.encode()).hexdigest():
                stale = True
        p["prompt_outdated"] = stale
    missing = {}
    for p in panels:
        for r in p["blocked"]:
            missing.setdefault(r["id"], {"name": r["name"], "path": r["path"], "status": r["status"],
                                         "kind": r["kind"], "id": r["id"], "panels": []})["panels"].append(p["panel"])
    missing = sorted(missing.values(), key=lambda m: m["name"])
    return {"exists": True, "tiers": plan["tiers"], "panels": panels, "running": running, "queued": queued,
            "prompts_outdated": any(p["prompt_outdated"] for p in panels),
            "lettering": json.loads((ROOT / "config" / "page.json").read_text()).get("lettering"),
            "page_size": {k: json.loads((ROOT / "config" / "page.json").read_text())[k] for k in ("width", "height")},
            "missing_references": missing,
            "prompts_built": all(p["spec"] for p in panels)}


def create_reference(kind, item_id, name):
    item_id = re.sub(r"[^a-z0-9_]+", "_", item_id.lower()).strip("_")
    if not item_id or kind not in ("character", "object", "location"):
        return False, "invalid id or kind", None
    proc = subprocess.run([sys.executable, str(SCRIPTS / "describe_reference.py"), kind, item_id, name],
                          cwd=ROOT, capture_output=True, text=True, timeout=600)
    path = {"character": "characters", "object": "objects", "location": "locations"}[kind] + "/" + item_id
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip(), path


def build_page_prompts(page):
    proc = subprocess.run([sys.executable, str(SCRIPTS / "build_prompts.py"), str(int(page))],
                          cwd=ROOT, capture_output=True, text=True, timeout=600)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def start_panel_generation(page, panels, count):
    info = panels_info(page)
    chosen = [p for p in info["panels"] if not panels or p["panel"] in [int(x) for x in panels]]
    blocked = sorted({r["name"] for p in chosen for r in p["blocked"]})
    if blocked:
        return False, f"Can't generate yet: approve {', '.join(blocked)} first."
    if any(p["prompt_outdated"] for p in chosen) or not all(p["spec"] for p in chosen):
        ok, log = build_page_prompts(page)   # never generate from an outdated prompt
        if not ok:
            return False, "Rebuilding the prompts failed:\n" + log
    folder = page_folder(page)
    cmd = [sys.executable, str(SCRIPTS / "generate_panels.py"), str(int(page)), "--count", str(int(count))]
    n_panels = len(panels) if panels else len(json.loads((folder / "page_plan.json").read_text())["panels"])
    if panels:
        cmd += ["--panel", *[str(int(x)) for x in panels]]
    label = f"Page {int(page)}, panel{'s' if n_panels > 1 else ''} " + (
        ", ".join(map(str, panels)) if panels else "all")
    return add_job({"key": f"page-{int(page)}", "page": int(page), "label": label, "kind": "panels",
                    "cmd": cmd, "count": n_panels * int(count),
                    "log": str(folder / "panels" / "generation.log")}, allow_duplicate=True)


def start_panel_assessment(page, panel):
    folder = page_folder(page)
    return add_job({"key": f"page-{int(page)}", "page": int(page), "label": f"Page {int(page)}, panel {int(panel)}",
                    "kind": "panel_assess",
                    "cmd": [sys.executable, str(SCRIPTS / "assess_panel.py"), str(int(page)), str(int(panel))],
                    "log": str(folder / "panels" / "generation.log")}, allow_duplicate=True)


def select_panel(page, panel, file):
    from datetime import datetime, timezone
    safe_comic_path(file)
    sel_file = page_folder(page) / "panels" / "selection.json"
    data = json.loads(sel_file.read_text()) if sel_file.exists() else {"selected": {}, "history": []}
    data["history"].append({"panel": int(panel), "file": file, "utc": datetime.now(timezone.utc).isoformat()})
    data["selected"][str(int(panel))] = file
    sel_file.parent.mkdir(parents=True, exist_ok=True)
    sel_file.write_text(json.dumps(data, indent=2))


# ---------- pages view & PDF export ----------

def book_info():
    import render_pages
    cfg = json.loads((ROOT / "config" / "page.json").read_text())
    pages = [render_pages.page_state(p) for p in render_pages.all_pages()]
    for s in pages:
        for p in s["panels"]:
            p.pop("image", None)  # absolute path: the browser uses image_rel via /comic
    return {"width": cfg["width"], "height": cfg["height"], "lettering": cfg.get("lettering"), "pages": pages}


def export_pdf(pages):
    import render_pages
    from datetime import datetime
    pages = sorted(set(pages))
    name = (f"page-{pages[0]:03d}" if len(pages) == 1 else f"pages-{pages[0]:03d}-{pages[-1]:03d}")
    out = ROOT / "outputs" / "export" / f"{name}_{datetime.now():%Y%m%d-%H%M%S}.pdf"
    render_pages.render(pages, out)
    return out


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
            elif url.path == "/api/panels":
                self.send(200, json.dumps(panels_info(int(q["page"]))).encode(), "application/json")
            elif url.path == "/comic":
                self.send(200, safe_comic_path(q["path"]).read_bytes(), "image/png")
            elif url.path == "/api/book":
                self.send(200, json.dumps(book_info()).encode(), "application/json")
            elif url.path == "/export.pdf":
                import render_pages
                pages = render_pages.all_pages() if q.get("all") else [int(x) for x in q["pages"].split(",")]
                out = export_pdf(pages)
                body = out.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f'attachment; filename="{out.name}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/jobs":
                self.send(200, json.dumps(job_overview()).encode(), "application/json")
            elif url.path == "/api/refs":
                self.send(200, json.dumps(list_refs()).encode(), "application/json")
            elif url.path == "/lib":
                p = safe_lib_path(q["path"])
                self.send(200, p.read_bytes(), "image/png")
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
        if path == "/api/ref/update":
            d = json.loads(body)
            update_ref_description(d["path"], d["description"])
            return self.send(200, b'{"ok": true}', "application/json")
        if path == "/api/ref/unqueue":
            n = unqueue(json.loads(body)["path"])
            return self.send(200, json.dumps({"ok": True, "removed": n}).encode(), "application/json")
        if path == "/api/ref/create":
            d = json.loads(body)
            ok, log, rel = create_reference(d["kind"], d["id"], d["name"])
            return self.send(200, json.dumps({"ok": ok, "log": log, "path": rel}).encode(), "application/json")
        if path == "/api/panels/build":
            ok, log = build_page_prompts(json.loads(body)["page"])
            return self.send(200, json.dumps({"ok": ok, "log": log}).encode(), "application/json")
        if path == "/api/panels/generate":
            d = json.loads(body)
            ok, msg = start_panel_generation(d["page"], d.get("panels"), d.get("count", 2))
            return self.send(200, json.dumps({"ok": ok, "message": msg}).encode(), "application/json")
        if path == "/api/panels/assess":
            d = json.loads(body)
            ok, msg = start_panel_assessment(d["page"], d["panel"])
            return self.send(200, json.dumps({"ok": ok, "message": msg}).encode(), "application/json")
        if path == "/api/panels/select":
            d = json.loads(body)
            select_panel(d["page"], d["panel"], d["file"])
            return self.send(200, b'{"ok": true}', "application/json")
        if path == "/api/ref/habit":
            d = json.loads(body)
            ok, msg = decide_habit(d["path"], d["issue"], d["accept"], d.get("description"))
            return self.send(200, json.dumps({"ok": ok, "message": msg}).encode(), "application/json")
        if path == "/api/ref/adopt":
            d = json.loads(body)
            ok, msg, result = adopt_candidate(d["path"], d["file"])
            return self.send(200, json.dumps({"ok": ok, "message": msg, "result": result}).encode(),
                             "application/json")
        if path == "/api/ref/fix":
            d = json.loads(body)
            ok, msg = start_fix(d["path"], d["file"])
            return self.send(200, json.dumps({"ok": ok, "message": msg}).encode(), "application/json")
        if path == "/api/ref/views":
            ok, msg = start_views(json.loads(body)["path"])
            return self.send(200, json.dumps({"ok": ok, "message": msg}).encode(), "application/json")
        if path == "/api/ref/assess":
            ok, msg = start_assessment(json.loads(body)["path"])
            return self.send(200, json.dumps({"ok": ok, "message": msg}).encode(), "application/json")
        if path == "/api/ref/generate":
            d = json.loads(body)
            if d.get("description"):
                update_ref_description(d["path"], d["description"])
            ok, msg = start_generation(d["path"], d.get("count", 4))
            return self.send(200, json.dumps({"ok": ok, "message": msg}).encode(), "application/json")
        if path == "/api/ref/approve":
            d = json.loads(body)
            approve_candidate(d["path"], d["file"])
            return self.send(200, b'{"ok": true}', "application/json")
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
    restore_job_state()
    threading.Thread(target=job_worker, daemon=True).start()
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
