"""
4XI Studios - ComfyUI Image Processing Watcher
Watches Google Drive raw_intake folder, processes through ComfyUI,
groups output by item into subfolders in processed_output.

Item grouping logic:
- Images sorted by filename (APC_0001, 0002, etc.)
- Tag shots (close-up of label held by hand) = end of current item
- Transition shots (bagged item) = separator, closes item, not processed
- Sequential product shots between separators = one item group
- Each item group gets its own subfolder in processed_output

Inference:
- Primary:  local CLIP fine-tuned model (instant, free, no API)
- Fallback: configurable API provider (deepseek | claude | openai)
            set INFERENCE_PROVIDER in .env to switch
"""

import json
import time
import shutil
import urllib.request
import urllib.error
import base64
import io
from pathlib import Path
from datetime import datetime

import config

# ── IMAGE CLASSIFICATION ──────────────────────────────────────────────────────

IMAGE_TYPE_PRODUCT    = "product"
IMAGE_TYPE_TAG        = "tag"
IMAGE_TYPE_TRANSITION = "transition"
LABELS                = [IMAGE_TYPE_PRODUCT, IMAGE_TYPE_TAG, IMAGE_TYPE_TRANSITION]

_CLASSIFY_PROMPT = (
    "Classify this resale clothing photo. Reply with one word only.\n\n"
    "product - garment laid flat on a surface, overhead shot\n"
    "tag - hands holding garment showing a sewn-in brand label\n"
    "transition - garment inside a clear plastic ziplock bag with a handwritten number\n\n"
    "Reply with only: product, tag, or transition"
)

# Global CLIP model (loaded once at startup)
_clip_model     = None
_clip_processor = None
_clip_device    = None

# Global Grounding DINO model (lazy-loaded on first tag image)
_dino_model     = None
_dino_processor = None

_new_dataset_samples = 0


def load_clip_model():
    global _clip_model, _clip_processor, _clip_device
    try:
        import torch
        import torch.nn as nn
        from transformers import CLIPModel, CLIPProcessor

        config_path  = Path(config.CLIP_MODEL_DIR) / "config.json"
        weights_path = Path(config.CLIP_MODEL_DIR) / "best_model.pt"

        if not config_path.exists() or not weights_path.exists():
            log("WARNING: CLIP model not found — will use API for all classifications")
            return False

        with open(config_path) as f:
            cfg = json.load(f)

        model_id     = cfg["model_id"]
        _clip_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        log(f"Loading CLIP classifier from {config.CLIP_MODEL_DIR}...")
        _clip_processor = CLIPProcessor.from_pretrained(model_id)

        class CLIPClassifier(torch.nn.Module):
            def __init__(self, model_id, num_classes):
                super().__init__()
                self.clip       = CLIPModel.from_pretrained(model_id)
                hidden_size     = self.clip.config.projection_dim
                self.classifier = torch.nn.Sequential(
                    torch.nn.Linear(hidden_size, 256),
                    torch.nn.ReLU(),
                    torch.nn.Dropout(0.2),
                    torch.nn.Linear(256, num_classes),
                )

            def forward(self, pixel_values):
                vision_out = self.clip.vision_model(pixel_values=pixel_values)
                features   = self.clip.visual_projection(vision_out.pooler_output)
                features   = features / features.norm(dim=-1, keepdim=True)
                return self.classifier(features)

        _clip_model = CLIPClassifier(model_id, num_classes=len(LABELS))
        _clip_model.load_state_dict(torch.load(weights_path, map_location=_clip_device))
        _clip_model.to(_clip_device)
        _clip_model.eval()

        log(f"CLIP ready on {_clip_device} (confidence threshold: {config.CLIP_CONFIDENCE})")
        return True

    except Exception as e:
        log(f"WARNING: Could not load CLIP model ({e}) — falling back to API")
        return False


def classify_with_clip(image_path: Path) -> tuple[str, float] | None:
    if _clip_model is None:
        return None
    try:
        import torch
        from PIL import Image

        img    = Image.open(image_path).convert("RGB")
        inputs = _clip_processor(images=img, return_tensors="pt").to(_clip_device)
        with torch.no_grad():
            logits = _clip_model(inputs["pixel_values"])
            probs  = torch.softmax(logits, dim=-1)
            pred   = probs.argmax(dim=-1).item()
            conf   = probs[0][pred].item()
        return LABELS[pred], conf
    except Exception as e:
        log(f"CLIP error for {image_path.name}: {e}")
        return None


def _image_to_b64(image_path: Path) -> str:
    from PIL import Image
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def classify_with_deepseek(image_path: Path) -> str:
    """Classify via DeepSeek vision API (OpenAI-compatible)."""
    try:
        image_data = _image_to_b64(image_path)
        payload = json.dumps({
            "model": config.DEEPSEEK_MODEL,
            "max_tokens": 10,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                    {"type": "text", "text": _CLASSIFY_PROMPT},
                ]
            }]
        }).encode("utf-8")

        req = urllib.request.Request(
            config.DEEPSEEK_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            }
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            label  = result["choices"][0]["message"]["content"].strip().lower()
            return label if label in LABELS else IMAGE_TYPE_PRODUCT

    except Exception as e:
        log(f"DeepSeek classification failed for {image_path.name}: {e} — defaulting to product")
        return IMAGE_TYPE_PRODUCT


def classify_with_claude(image_path: Path) -> str:
    """Classify via Claude API."""
    try:
        image_data = _image_to_b64(image_path)
        payload = json.dumps({
            "model": config.CLAUDE_MODEL,
            "max_tokens": 10,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
                    {"type": "text", "text": _CLASSIFY_PROMPT},
                ]
            }]
        }).encode("utf-8")

        req = urllib.request.Request(
            config.CLAUDE_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": config.CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
            }
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            label  = result["content"][0]["text"].strip().lower()
            return label if label in LABELS else IMAGE_TYPE_PRODUCT

    except Exception as e:
        log(f"Claude classification failed for {image_path.name}: {e} — defaulting to product")
        return IMAGE_TYPE_PRODUCT


def classify_with_gemini(image_path: Path) -> str:
    """Classify via Google Gemini vision API (free tier available)."""
    try:
        image_data = _image_to_b64(image_path)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
        )
        payload = json.dumps({
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_data}},
                    {"text": _CLASSIFY_PROMPT},
                ]
            }],
            "generationConfig": {"maxOutputTokens": 10, "temperature": 0},
        }).encode("utf-8")

        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            label  = result["candidates"][0]["content"]["parts"][0]["text"].strip().lower()
            return label if label in LABELS else IMAGE_TYPE_PRODUCT

    except Exception as e:
        log(f"Gemini classification failed for {image_path.name}: {e} — defaulting to product")
        return IMAGE_TYPE_PRODUCT


def classify_with_openai(image_path: Path) -> str:
    """Classify via OpenAI vision API."""
    try:
        image_data = _image_to_b64(image_path)
        payload = json.dumps({
            "model": config.OPENAI_MODEL,
            "max_tokens": 10,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                    {"type": "text", "text": _CLASSIFY_PROMPT},
                ]
            }]
        }).encode("utf-8")

        req = urllib.request.Request(
            config.OPENAI_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            }
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            label  = result["choices"][0]["message"]["content"].strip().lower()
            return label if label in LABELS else IMAGE_TYPE_PRODUCT

    except Exception as e:
        log(f"OpenAI classification failed for {image_path.name}: {e} — defaulting to product")
        return IMAGE_TYPE_PRODUCT


# Dispatch table — add new providers here
_API_CLASSIFIERS = {
    "deepseek": classify_with_deepseek,
    "claude":   classify_with_claude,
    "openai":   classify_with_openai,
    "gemini":   classify_with_gemini,
}


def classify_with_api(image_path: Path) -> str:
    """Route to the configured inference provider."""
    provider = config.INFERENCE_PROVIDER.lower()
    fn = _API_CLASSIFIERS.get(provider)
    if fn is None:
        log(f"WARNING: Unknown INFERENCE_PROVIDER '{provider}' — falling back to deepseek")
        fn = classify_with_deepseek
    label = fn(image_path)
    log(f"  {image_path.name} -> {label} ({provider})")
    save_to_dataset(image_path, label)
    return label


def classify_image(image_path: Path) -> str:
    """CLIP first; API fallback if confidence below threshold."""
    result = classify_with_clip(image_path)
    if result is not None:
        label, conf = result
        if conf >= config.CLIP_CONFIDENCE:
            log(f"  {image_path.name} -> {label} (CLIP {conf:.2f})")
            return label
        log(f"  {image_path.name} -> CLIP uncertain ({label} {conf:.2f}), asking {config.INFERENCE_PROVIDER}...")
    return classify_with_api(image_path)


# ── DATASET / RETRAIN ─────────────────────────────────────────────────────────

def save_to_dataset(image_path: Path, label: str):
    global _new_dataset_samples
    if not config.AUTO_DATASET:
        return
    try:
        dest_dir  = Path(config.CLIP_MODEL_DIR).parent.parent / "dataset" / label
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / image_path.name
        if not dest_file.exists():
            shutil.copy2(image_path, dest_file)
            _new_dataset_samples += 1
    except Exception as e:
        log(f"  WARNING: Could not save {image_path.name} to dataset: {e}")


def trigger_retrain():
    import subprocess
    log(f"Triggering incremental retrain ({_new_dataset_samples} new samples)...")
    try:
        subprocess.Popen(
            [config.PYTHON_EXE, config.RETRAIN_SCRIPT],
            creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, "CREATE_NEW_CONSOLE") else 0,
        )
        log("Retrain launched in background.")
    except Exception as e:
        log(f"WARNING: Could not launch retrain: {e}")


# ── POST-PROCESSING (crop + square) ──────────────────────────────────────────

def load_dino_model():
    global _dino_model, _dino_processor
    if _dino_model is not None:
        return
    try:
        import torch
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        log("Loading Grounding DINO model (first run may download ~680MB)...")
        device = _clip_device or ("cuda" if torch.cuda.is_available() else "cpu")
        _dino_processor = AutoProcessor.from_pretrained("IDEA-Research/grounding-dino-base")
        _dino_model = AutoModelForZeroShotObjectDetection.from_pretrained(
            "IDEA-Research/grounding-dino-base"
        ).to(device)
        _dino_model.eval()
        log(f"Grounding DINO ready on {device}")
    except Exception as e:
        log(f"WARNING: Could not load Grounding DINO: {e}")
        _dino_model = None


def find_tag_bbox(img_path: Path):
    load_dino_model()
    if _dino_model is None:
        return None
    try:
        import torch
        from PIL import Image
        image  = Image.open(img_path).convert("RGB")
        text   = "clothing tag . garment label . price tag ."
        device = next(_dino_model.parameters()).device
        inputs = _dino_processor(images=image, text=text, return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = _dino_model(**inputs)
        results = _dino_processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids,
            box_threshold=0.25, text_threshold=0.20,
            target_sizes=[image.size[::-1]],
        )
        if results and len(results[0]["boxes"]) > 0:
            best = results[0]["scores"].argmax().item()
            box  = results[0]["boxes"][best].tolist()
            return tuple(int(x) for x in box)
    except Exception as e:
        log(f"  WARNING: DINO detection error: {e}")
    return None


def _save_square_jpeg(cropped_rgba, img_path: Path):
    from PIL import Image
    cw, ch = cropped_rgba.size
    size   = max(cw, ch)
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    flat   = Image.new("RGB", (cw, ch), (255, 255, 255))
    flat.paste(cropped_rgba, mask=cropped_rgba.split()[3])
    canvas.paste(flat, ((size - cw) // 2, (size - ch) // 2))
    jpg_path = img_path.with_suffix(".jpg")
    canvas.save(jpg_path, "JPEG", quality=92)
    if img_path.suffix.lower() != ".jpg" and img_path.exists():
        img_path.unlink()
    return jpg_path


def crop_to_square_product(img_path: Path) -> bool:
    try:
        from PIL import Image
        img  = Image.open(img_path).convert("RGBA")
        bbox = img.split()[3].getbbox()
        if not bbox:
            return False
        left, top, right, bottom = bbox
        bw, bh = right - left, bottom - top
        px, py = int(bw * 0.08), int(bh * 0.08)
        W, H   = img.size
        box    = (max(0, left-px), max(0, top-py), min(W, right+px), min(H, bottom+py))
        _save_square_jpeg(img.crop(box), img_path)
        log(f"  Cropped (product): {img_path.stem}")
        return True
    except Exception as e:
        log(f"  WARNING: product crop failed for {img_path.name}: {e}")
        return False


def crop_to_square_tag(img_path: Path) -> bool:
    try:
        from PIL import Image
        img  = Image.open(img_path).convert("RGBA")
        W, H = img.size
        bbox = find_tag_bbox(img_path)
        if bbox:
            x1, y1, x2, y2 = bbox
            log(f"  DINO tag bbox: ({x1},{y1})-({x2},{y2})")
        else:
            log(f"  DINO: no tag found — falling back to alpha bbox")
            ab = img.split()[3].getbbox()
            if not ab:
                return False
            x1, y1, x2, y2 = ab
        bw, bh = x2 - x1, y2 - y1
        px, py = int(bw * 0.10), int(bh * 0.10)
        box    = (max(0, x1-px), max(0, y1-py), min(W, x2+px), min(H, y2+py))
        _save_square_jpeg(img.crop(box), img_path)
        log(f"  Cropped (tag): {img_path.stem}")
        return True
    except Exception as e:
        log(f"  WARNING: tag crop failed for {img_path.name}: {e}")
        return False


def post_process_output_image(img_path: Path, classification: str):
    if not img_path.exists():
        return
    if classification == IMAGE_TYPE_TAG:
        crop_to_square_tag(img_path)
    else:
        crop_to_square_product(img_path)


# ── ITEM GROUPING ─────────────────────────────────────────────────────────────

def group_images_into_items(images: list[Path]) -> list[dict]:
    items              = []
    current_listing    = []
    current_all        = []
    current_transition = None
    current_classifs   = {}

    log(f"Classifying {len(images)} images into item groups...")

    for img in images:
        classification = classify_image(img)

        if classification == IMAGE_TYPE_TRANSITION:
            current_all.append(img)
            current_transition = img
            if current_all:
                items.append({
                    "listing":         current_listing[:],
                    "all":             current_all[:],
                    "transition":      current_transition,
                    "classifications": current_classifs.copy(),
                })
            current_listing    = []
            current_all        = []
            current_transition = None
            current_classifs   = {}
        elif classification == IMAGE_TYPE_TAG:
            current_listing.append(img)
            current_all.append(img)
            current_classifs[img.stem] = IMAGE_TYPE_TAG
        else:
            current_listing.append(img)
            current_all.append(img)
            current_classifs[img.stem] = IMAGE_TYPE_PRODUCT

    if current_all:
        items.append({
            "listing":         current_listing[:],
            "all":             current_all[:],
            "transition":      current_transition,
            "classifications": current_classifs.copy(),
        })

    log(f"Grouped into {len(items)} item(s)")
    return items


# ── WORKFLOW ──────────────────────────────────────────────────────────────────

def build_workflow(filename: str, output_prefix: str) -> dict:
    return {
        "1":  {"inputs": {"image": filename}, "class_type": "LoadImage"},
        "2":  {
            "inputs": {
                "model": "BiRefNet-general",
                "mask_blur": 0, "mask_offset": -3,
                "invert_output": False, "refine_foreground": True,
                "background": "Color", "background_color": "#ffffff",
                "image": ["1", 0],
            },
            "class_type": "BiRefNetRMBG",
        },
        "4":  {"inputs": {"dilation": -10, "mask": ["2", 1]}, "class_type": "ImpactDilateMask"},
        "5":  {
            "inputs": {
                "combined": True, "crop_factor": 1.2, "bbox_fill": False,
                "drop_size": 10, "contour_fill": False, "mask": ["4", 0],
            },
            "class_type": "MaskToSEGS",
        },
        "8":  {"inputs": {"override": True, "segs": ["5", 0], "image": ["2", 0]},
               "class_type": "SetDefaultImageForSEGS"},
        "10": {"inputs": {"segs": ["8", 0]}, "class_type": "ImpactDecomposeSEGS"},
        "9":  {"inputs": {"seg_elt": ["10", 1]}, "class_type": "ImpactFrom_SEG_ELT"},
        "11": {"inputs": {"filename_prefix": output_prefix, "images": ["9", 1]},
               "class_type": "SaveImage"},
    }


# ── COMFYUI API ───────────────────────────────────────────────────────────────

def preflight_check() -> bool:
    """
    Verify ComfyUI is up and the nodes our workflow needs are registered.
    Returns True if safe to proceed, False if we should abort.
    """
    required_nodes = [
        "BiRefNetRMBG",
        "ImpactDilateMask",
        "MaskToSEGS",
        "SetDefaultImageForSEGS",
        "ImpactDecomposeSEGS",
        "ImpactFrom_SEG_ELT",
    ]

    # 1. Is ComfyUI reachable?
    try:
        with urllib.request.urlopen(f"{config.COMFYUI_API}/system_stats", timeout=5) as resp:
            resp.read()
    except Exception as e:
        log(f"PREFLIGHT FAIL: ComfyUI not reachable at {config.COMFYUI_API} — {e}")
        log("  Make sure ComfyUI is running before starting the pipeline.")
        return False

    # 2. Are required nodes registered?
    try:
        with urllib.request.urlopen(f"{config.COMFYUI_API}/object_info", timeout=10) as resp:
            node_defs = json.loads(resp.read())
    except Exception as e:
        log(f"PREFLIGHT FAIL: Could not fetch node list from ComfyUI — {e}")
        return False

    missing = [n for n in required_nodes if n not in node_defs]
    if missing:
        log(f"PREFLIGHT FAIL: Required ComfyUI nodes are missing:")
        for n in missing:
            log(f"  ✗ {n}")
        log("  These nodes come from ComfyUI-Impact-Pack and ComfyUI-RMBG.")
        log("  Check that those custom nodes loaded without errors in ComfyUI.")
        return False

    log(f"Preflight OK — ComfyUI up, all {len(required_nodes)} required nodes present.")
    return True


def queue_prompt(workflow: dict) -> str | None:
    payload = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(
        f"{config.COMFYUI_API}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())

        # ComfyUI returns node_errors when validation fails — no prompt_id is issued
        if "node_errors" in result and result["node_errors"]:
            log(f"ERROR: ComfyUI workflow validation failed:")
            for node_id, err in result["node_errors"].items():
                log(f"  Node {node_id}: {err.get('errors', err)}")
            return None

        prompt_id = result.get("prompt_id")
        if not prompt_id:
            log(f"ERROR: ComfyUI returned no prompt_id. Response: {result}")
        return prompt_id

    except urllib.error.URLError as e:
        log(f"ERROR: Could not reach ComfyUI API — {e}")
        return None


def get_output_files(prompt_id: str) -> list[str]:
    try:
        with urllib.request.urlopen(f"{config.COMFYUI_API}/history/{prompt_id}") as resp:
            history = json.loads(resp.read())
            entry   = history.get(prompt_id, {})

            # Check if ComfyUI reported an execution error
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = status.get("messages", [])
                for kind, detail in msgs:
                    if kind == "execution_error":
                        log(f"  ERROR in ComfyUI execution: {detail.get('exception_message', detail)}")
                        log(f"    Node: {detail.get('node_type', '?')} (id {detail.get('node_id', '?')})")

            outputs = entry.get("outputs", {})
            return [img["filename"] for node in outputs.values()
                    for img in node.get("images", [])]
    except Exception as e:
        log(f"  WARNING: Could not fetch output history: {e}")
        return []


# ── HELPERS ───────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def ensure_dirs():
    for d in [config.GDRIVE_RAW, config.GDRIVE_PROCESSED, config.GDRIVE_DONE,
              config.COMFYUI_INPUT, config.COMFYUI_OUTPUT]:
        Path(d).mkdir(parents=True, exist_ok=True)


def get_pending_images() -> list[Path]:
    raw  = Path(config.GDRIVE_RAW)
    done = Path(config.GDRIVE_DONE)
    done_names_lower = {f.name.lower() for f in done.iterdir()} if done.exists() else set()
    seen = {}
    for ext in ["*.jpg", "*.jpeg", "*.JPG", "*.JPEG"]:
        for f in raw.glob(ext):
            if f.name.lower() not in done_names_lower and not f.name.startswith("_"):
                seen[f.name.lower()] = f
    return sorted(seen.values())


def process_item(item_images: list[Path], item_folder: Path,
                 all_item_images: list[Path],
                 transition_image: Path | None = None,
                 classifications: dict | None = None) -> bool:
    if config.DRY_RUN:
        log(f"  [DRY RUN] Would create: {item_folder}")
        for p in item_images:
            log(f"    - {p.name}")
        return True

    item_folder.mkdir(parents=True, exist_ok=True)
    item_name     = item_folder.name
    success_count = 0
    total         = len(item_images)

    if transition_image and transition_image.exists():
        shutil.copy2(transition_image, item_folder / transition_image.name)
        log(f"  Copied transition/SKU shot: {transition_image.name}")

    archive_folder = Path(config.GDRIVE_DONE) / item_name
    archive_folder.mkdir(parents=True, exist_ok=True)

    for idx, image_path in enumerate(item_images, 1):
        filename      = image_path.name
        output_prefix = f"{item_name}/{Path(filename).stem}_processed"
        log(f"  [{idx}/{total}] Processing {filename}...")
        start = time.time()

        shutil.copy2(image_path, Path(config.COMFYUI_INPUT) / filename)
        prompt_id = queue_prompt(build_workflow(filename, output_prefix))
        if not prompt_id:
            log(f"  [{idx}/{total}] FAILED to queue {filename}")
            continue

        deadline  = time.time() + config.PROCESS_TIMEOUT
        completed = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{config.COMFYUI_API}/history/{prompt_id}") as resp:
                    if prompt_id in json.loads(resp.read()):
                        completed = True
                        break
            except urllib.error.URLError:
                pass
            elapsed = int(time.time() - start)
            print(f"\r  [{idx}/{total}] {filename} — {elapsed}s elapsed...", end="", flush=True)
            time.sleep(3)

        print()
        if not completed:
            log(f"  [{idx}/{total}] TIMEOUT on {filename}")
            continue

        log(f"  [{idx}/{total}] Done in {int(time.time()-start)}s — saving output...")
        img_class = (classifications or {}).get(image_path.stem, IMAGE_TYPE_PRODUCT)

        for fname in get_output_files(prompt_id):
            src = Path(config.COMFYUI_OUTPUT) / fname
            if not src.exists():
                src = Path(config.COMFYUI_OUTPUT) / item_name / Path(fname).name
            if not src.exists():
                src = Path(config.COMFYUI_OUTPUT) / item_name
                if src.is_dir():
                    for f in src.glob("*.png"):
                        dest = item_folder / f.name
                        shutil.copy2(f, dest)
                        log(f"  Saved: {f.name}")
                        post_process_output_image(dest, img_class)
                        success_count += 1
                    continue
            if src.exists() and src.is_file():
                dest = item_folder / Path(fname).name
                shutil.copy2(src, dest)
                log(f"  Saved: {Path(fname).name}")
                post_process_output_image(dest, img_class)
                success_count += 1

    # Safety: only archive originals if at least one image was successfully processed.
    # If ComfyUI failed on everything, leave originals in place so they can be retried.
    if success_count == 0 and total > 0:
        log(f"  SAFETY: No outputs produced for {item_name} — originals NOT archived.")
        log(f"  Fix the ComfyUI issue and re-run to process this item.")
        return False

    for image_path in all_item_images:
        dest = archive_folder / image_path.name
        if image_path.exists():
            shutil.move(str(image_path), dest)
            log(f"  Archived: {image_path.name}")

    log(f"Item {item_name}: {success_count}/{total} listing photos processed")
    return success_count > 0


# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

def main(single_pass: bool = False, count: int = None):
    log("4XI Watcher started")
    log(f"Inference provider: {config.INFERENCE_PROVIDER}")
    log(f"Watching:  {config.GDRIVE_RAW}")
    log(f"Output:    {config.GDRIVE_PROCESSED}")
    log(f"ComfyUI:   {config.COMFYUI_API}")

    if config.DRY_RUN:
        log("*** DRY RUN MODE — no files will be moved or sent to ComfyUI ***")
    else:
        ensure_dirs()
        if not preflight_check():
            log("Aborting — fix the issues above before running the pipeline.")
            return
    load_clip_model()

    while True:
        pending = get_pending_images()

        if pending:
            log(f"Found {len(pending)} image(s) — grouping into items...")
            item_groups = group_images_into_items(pending)

            if count and count > 0 and count < len(item_groups):
                log(f"Limiting to {count} item(s) ({len(item_groups)} available)")
                item_groups = item_groups[:count]

            for i, item in enumerate(item_groups):
                listing_images = item["listing"]
                all_images     = item["all"]
                item_name      = all_images[0].stem if all_images else f"item_{i+1}"
                item_folder    = Path(config.GDRIVE_PROCESSED) / item_name
                archive_folder = Path(config.GDRIVE_DONE) / item_name

                if not listing_images:
                    log(f"Skipping item {i+1} — no listing photos, archiving originals")
                    archive_folder.mkdir(parents=True, exist_ok=True)
                    for image_path in all_images:
                        dest = archive_folder / image_path.name
                        if image_path.exists():
                            shutil.move(str(image_path), dest)
                    continue

                transition_image = item.get("transition")
                log(f"Processing item {i+1}/{len(item_groups)}: {item_name} "
                    f"({len(listing_images)} listing, {len(all_images)} total"
                    + (f", SKU: {transition_image.name}" if transition_image else ", no transition")
                    + ")")
                process_item(listing_images, item_folder, all_images, transition_image,
                             classifications=item.get("classifications", {}))

            if not config.DRY_RUN and config.AUTO_DATASET and _new_dataset_samples >= config.RETRAIN_THRESHOLD:
                trigger_retrain()

            if not config.DRY_RUN and single_pass:
                processed_items = [
                    item["all"][0].stem
                    for item in item_groups
                    if item["listing"] and item["all"]
                ]
                session = {
                    "items":     processed_items,
                    "count":     len(processed_items),
                    "timestamp": datetime.now().isoformat(),
                }
                with open(config.SESSION_FILE, "w") as f:
                    json.dump(session, f, indent=2)
                log(f"Session file written: {len(processed_items)} item(s) -> {config.SESSION_FILE}")
        else:
            log("No new images — waiting...")

        if config.DRY_RUN or single_pass:
            log("*** Complete — exiting ***")
            break
        time.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    import sys
    _single_pass = "--single-pass" in sys.argv
    _count = None
    if "--count" in sys.argv:
        _idx = sys.argv.index("--count")
        try:
            _count = int(sys.argv[_idx + 1])
        except (IndexError, ValueError):
            pass
    main(_single_pass, _count)
