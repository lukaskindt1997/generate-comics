"""Prompt helpers for reference generation.

Two texts per reference:
  - description:        long, precise, for humans and for the assessment
  - generation_prompt:  short, positively phrased, most important first; this is what
                        goes to the image model (FLUX tends to draw what is mentioned,
                        even after "no", and drops details from long prompts)
Edit instructions for fixing a candidate are written the same way.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import claude_cli

ROOT = Path(__file__).resolve().parent.parent
STYLE = ROOT / "config" / "style.json"

RULES = """Rules for text sent to the image model (FLUX.2 Klein):
- Positive phrasing only. Never write "no X", "without X", "not X": the model tends to draw
  every object that is named. Describe what IS there instead ("smooth bald iron head" instead
  of "no fur"; "dull dark iron eyes" instead of "no glowing eyes"; "hip-length shoulder cape"
  instead of "no hood, not long").
- Most important, distinguishing features first. Concrete, visual nouns and adjectives.
- Leave out story, personality and reasons ("plainly a noble's son"): only what is visible.
- Keep every visual fact from the description; merge and compress the wording.
- For a LOCATION: only its permanent look (no events, weather, smoke or fires, no characters
  or objects that have their own reference)."""


def style():
    return json.loads(STYLE.read_text())


def ensure_generation_prompt(folder):
    """Return the short generation prompt for a reference, (re)writing it with Claude when
    missing or when the description changed."""
    ref_file = Path(folder) / "ref.json"
    ref = json.loads(ref_file.read_text())
    gp = ref.get("generation_prompt")
    if gp and gp.get("for_description") == ref["description"]:
        return gp["text"]
    schema = {"type": "object", "properties": {
        "text": {"type": "string", "description": "The generation prompt, 30-60 words."}},
        "required": ["text"]}
    prompt = f"""Rewrite this reference description of a {ref['kind']} into a short prompt for an
image model, 30-60 words. It describes only the subject; the layout and art style are added
separately.

{RULES}

Name: {ref['name']}
Description: {ref['description']}"""
    out, meta = claude_cli.ask(prompt, schema)
    if gp:
        ref.setdefault("generation_prompt_history", []).append(gp)
    ref["generation_prompt"] = {"text": out["text"].strip(), "for_description": ref["description"],
                                "created_utc": datetime.now(timezone.utc).isoformat(),
                                "models": meta["models"]}
    ref_file.write_text(json.dumps(ref, indent=2, ensure_ascii=False))
    return ref["generation_prompt"]["text"]


def edit_instruction(ref, deviations):
    """Pick the single most important deviation and write one short, positive edit
    instruction for it. One change per fix: several at once are mostly ignored.
    Returns (instruction, target_deviation)."""
    schema = {"type": "object", "properties": {
        "target": {"type": "string", "description": "The one deviation chosen, copied from the list."},
        "instruction": {"type": "string", "description": "The edit instruction, max ~30 words."}},
        "required": ["target", "instruction"]}
    prompt = f"""An image of a {ref['kind']} needs a targeted correction with an image-editing model
(FLUX.2 Klein edit mode: it redraws the image from it and follows a short instruction).
Choose the ONE most important deviation below and write ONE short edit instruction that fixes
only that. Contradictions with the book come first (they are listed first); after that, what
matters most for recognising the {ref['kind']} in later panels.
Several changes at once are mostly ignored, so fix just this one.

{RULES}
- Start with the one change ("Make the cloak short, ending at the hips"), then say what must
  stay identical ("Keep everything else exactly the same: face, pose, colours, plain light
  grey background, art style"). Max ~30 words.

{ref['kind'].capitalize()}: {ref['name']}
Target description: {ref['description']}
Deviations to fix:
""" + "\n".join(f"- {d}" for d in deviations)
    out, _ = claude_cli.ask(prompt, schema)
    return out["instruction"].strip(), out["target"].strip()


def components(folder, lib_root):
    """Objects from the library that this character wears or carries, decided by Claude from
    the description. Cached in ref.json ("components") until the description changes.
    Returns a list of object ids."""
    ref_file = Path(folder) / "ref.json"
    ref = json.loads(ref_file.read_text())
    if ref["kind"] != "character":
        return []
    objects = []
    for f in sorted((Path(lib_root) / "objects").glob("*/ref.json")):
        o = json.loads(f.read_text())
        objects.append({"id": o["id"], "name": o["name"], "description": o["description"][:300]})
    cached = ref.get("components")
    known = sorted(o["id"] for o in objects)
    if cached and cached.get("for_description") == ref["description"] and cached.get("objects_known") == known:
        return cached["ids"]
    if not objects:
        ids = []
    else:
        schema = {"type": "object", "properties": {
            "ids": {"type": "array", "items": {"type": "string", "enum": known}}}, "required": ["ids"]}
        prompt = (f"Which of these objects does this character wear or carry, according to the character "
                  f"description? Only objects that are clearly part of the character's look.\n\n"
                  f"CHARACTER: {ref['name']}: {ref['description']}\n\nOBJECTS:\n"
                  + "\n".join(f"- {o['id']}: {o['name']}: {o['description']}" for o in objects))
        out, _ = claude_cli.ask(prompt, schema)
        ids = out["ids"]
    ref = json.loads(ref_file.read_text())
    ref["components"] = {"ids": ids, "for_description": ref["description"], "objects_known": known,
                         "created_utc": datetime.now(timezone.utc).isoformat()}
    ref_file.write_text(json.dumps(ref, indent=2, ensure_ascii=False))
    return ids
