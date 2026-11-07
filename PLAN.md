# Malazan AI Comic Studio: Single-Page Pipeline Plan

**Goal of this milestone:** take one photographed book page and turn it into one finished, lettered comic page. Make every step work reliably on that page before scaling up.

**Status:** planning. No code, folders or library yet. We'll go through the steps one at a time.

---

## Pipeline diagram

```mermaid
flowchart TD
    A[/"📷 Photo of book page"/] --> S1

    subgraph TEXT["1 · Source text"]
        S1["1. Image preprocessing<br/>crop · deskew · contrast"] --> S2["2. Text extraction (OCR)"]
        S2 --> H1{{"👤 Review & correct text"}}
    end

    subgraph STORY["2 · Story understanding"]
        H1 --> S3["3. Scene analysis<br/>characters · location · beats · dialogue + speakers"]
        S3 --> S4["4. Continuity lookup<br/>match to character/location references"]
        S4 --> H2{{"👤 Approve scene breakdown<br/>& fill missing references"}}
    end

    subgraph PLAN["3 · Page planning"]
        H2 --> S5["5. Page & panel planning<br/>layout · shots · composition · balloon space"]
        S5 --> S6["6. Pre-generation safety screening"]
        S6 -->|needs adaptation| S5
        S6 --> H3{{"👤 Approve page plan"}}
    end

    subgraph ART["4 · Artwork"]
        H3 --> S7["7. Prompt building<br/>plan + style guide + references"]
        S7 --> S8["8. Panel generation<br/>Draw Things · FLUX.2 Klein"]
        S8 --> S9["9. Panel QC<br/>deterministic checks + VLM + post-gen safety"]
        S9 -->|fail, retries left| S10["10. Targeted correction<br/>new version, never overwrite"]
        S10 --> S9
        S9 -->|pass / uncertain / retries exhausted| H4{{"👤 Select & approve panels"}}
    end

    subgraph PAGE["5 · Page"]
        H4 --> S11["11. Lettering<br/>balloons · tails · captions"]
        S11 --> S12["12. Page assembly<br/>layout · gutters · borders"]
        S12 --> S13["13. Final page QC"]
        S13 -->|issue| S11
        S13 --> H5{{"👤 Final approval"}}
    end

    H5 --> S14[/"14. Export<br/>page image + manifest"/]
```

👤 = human checkpoint. For the first page every checkpoint is manual. Later we can automate the ones that prove reliable.

---

## Steps

Each step lists its **input**, its **output**, and **open questions** to settle when we get to it.

### 1 · Source text

#### Step 1: Image preprocessing
Prepare the photo so OCR works well.
- **Input:** photo of a book page (phone or camera).
- **Output:** cleaned image (cropped to the text block, straightened, normalized contrast). The original is kept untouched.
- **Open questions:** Single page or two-page spreads? How much page curvature and shadow do your photos usually have?

#### Step 2: Text extraction (OCR)
- **Input:** cleaned image.
- **Output:** plain text with paragraph breaks preserved, plus provenance (book, chapter, page number, photo file).
- **Options to compare:** Apple Vision framework (built into macOS, local, very good on printed text), Tesseract, or a local vision-language model through Ollama.
- **Open questions:** Which engine handles your photos best? We'll test 2–3 on the same page.

#### ✅ Checkpoint 1: Review & correct text
You correct OCR mistakes. Everything downstream depends on this text, so it has to be right.

### 2 · Story understanding

#### Step 3: Scene analysis
Pull out the structured story content.
- **Input:** corrected text.
- **Output:** a scene breakdown containing:
  - characters present (and those only mentioned)
  - location, time of day, weather, lighting
  - key objects and props
  - story **beats** in order (what happens, the emotion)
  - dialogue lines with **speaker identity** stored separately from the text
  - narration that might become captions
- **Open questions:** Which local text model can do this well enough? Is it acceptable for Claude to help here as a one-off authoring step?

#### Step 4: Continuity lookup
- **Input:** scene breakdown.
- **Output:** each character/location linked to a reference entry (description, appearance state at this point in the story, reference images), or flagged as **missing**.
- **For the first page:** a small hand-written reference sheet per character instead of a real library.
- **Open questions:** How do we create the first reference portraits? (Generate candidates → you approve one.)

#### ✅ Checkpoint 2: Approve scene breakdown & fill missing references

### 3 · Page planning

#### Step 5: Page & panel planning
- **Input:** approved scene breakdown + references.
- **Output:** a page plan with:
  - page layout (number of panels, grid/arrangement, reading order)
  - per panel: beat, characters, shot type (wide/medium/close-up), camera angle, composition, expressions, props
  - per panel: dialogue/captions and a **reserved area for balloons**, so the art leaves room for text
- **Open questions:** How many panels per page? How much of the excerpt fits on one page? Fixed layout templates first, or free layouts?

#### Step 6: Pre-generation safety screening
- **Input:** page plan.
- **Output:** pass / adapt / escalate for each panel, with reasons. "Adapt" sends the panel back to planning with a safer framing (implication, reaction, aftermath).
- **Open questions:** Write the first version of the content policy as a short rules file.

#### ✅ Checkpoint 3: Approve page plan

### 4 · Artwork

#### Step 7: Prompt building
Turn each panel into a generation request, **in code**, not by hand.
- **Input:** panel plan + style guide + character/location descriptions + reference images.
- **Output:** prompt, negative cues, size/aspect ratio, reference images, seed.
- **Open questions:** What prompt structure works best for FLUX.2 Klein? Does it use reference images for consistency, and does Draw Things expose that through its API?

#### Step 8: Panel generation
- **Input:** generation request.
- **Output:** one or more candidate images per panel, plus a record of every parameter (model, seed, steps, prompt, references) so it can be reproduced.
- **Open questions:** Which Draw Things API (HTTP vs gRPC) and which parameters it supports. This is the Phase 1 connectivity test.

#### Step 9: Panel QC
- **Input:** candidate image + its panel plan.
- **Output:** a QC report: pass / fail / uncertain per check, with details.
  - *Deterministic:* size/aspect ratio, balloon area not too busy, basic image sanity.
  - *VLM:* right characters present, matches references, expression/action match, no stray text, anatomy, style.
  - *Post-generation safety check.*
- **Open questions:** Which Qwen3-VL size fits in RAM next to Draw Things? (Probably run them one after the other.)

#### Step 10: Targeted correction
- **Input:** failed QC report.
- **Output:** a new image version linked to its parent (regenerate with a new seed, adjust the prompt, or edit a region). Never overwrites.
- **Retry limit:** configurable (e.g. 3). After that it goes to you.

#### ✅ Checkpoint 4: Select & approve panels

### 5 · Page

#### Step 11: Lettering
- **Input:** approved panels + dialogue/captions from the plan.
- **Output:** balloons and caption boxes drawn **in code** (Pillow/SVG), with tails pointing at the speaker. Text stays editable.
- **Open questions:** How do we know where the speaker is in the image? (Plan hint → VLM/face detection → manual click.) Which font? (A free comic lettering font.)

#### Step 12: Page assembly
- **Input:** lettered panels + layout.
- **Output:** full page at print-ready resolution, with gutters and borders.

#### Step 13: Final page QC
- **Output:** checks that the text matches the script exactly, reading order is clear, balloons don't cover faces or each other, tails point to the right speakers, and no panel is missing.

#### ✅ Checkpoint 5: Final approval

#### Step 14: Export
- **Output:** page PNG (and/or PDF) + a manifest that records the source page, plan, chosen panel versions, parameters and QC results.

---

## Build milestones

*Steps* are the order the pipeline runs in. *Milestones* (lettered, so they don't get confused with step numbers) are the order we build things in, starting with the riskiest parts:

- **Milestone A:** Draw Things connectivity → Step 8 *(current)*
- **Milestone B:** photo → text → Steps 1–2
- **Milestone C:** scene analysis → page plan, semi-manual at first → Steps 3, 5
- **Milestone D:** prompt building + first real panels → Step 7
- **Milestone E:** lettering & assembly → first complete page → Steps 11–12
- **Milestone F:** automated QC and correction → Steps 9–10
- **Milestone G:** stricter continuity, safety and final QC → Steps 4, 6, 13

## Environment (checked 2026-11-07)

- Apple M4, 24 GB unified memory, macOS 15.7.3
- Draw Things installed and running; HTTP API enabled on `127.0.0.1:7860` (localhost only)

## Milestone A findings (2026-11-07) ✅

- `GET /` returns the app's full current config as JSON (model, sampler, steps, etc.).
- `POST /sdapi/v1/txt2img` works; we send only the overrides, the app supplies the rest.
- Current setup: `flux_2_klein_9b_q8p.ckpt`, DDIM Trailing, 4 steps, guidance 1, shift 3, seed mode "Scale Alike".
- 768×1024 takes **~94 s** per image on the M4.
- **Same seed + same settings → pixel-identical image.** File hashes differ (PNG metadata), so later version tracking should hash pixel data, not file bytes.
- Script: `scripts/dt_txt2img.py` (stdlib only), outputs to `outputs/milestone_a/` with a JSON sidecar.
- Not yet tested: choosing the model per request, img2img / reference images (needed for Milestone D).
- System Python is 3.9.6 (old; we may want a newer Python)

## Still needed

- A sample photo of the book page to start with
