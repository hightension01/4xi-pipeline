"""
4XI Studios - Dataset Backfill
Scans raw_intake/_done, classifies each image with Claude,
and copies into Archive/dataset/{product,tag,transition}.

Run once to backfill, then re-run any time to pick up new batches.
"""

import base64
import io
import json
import shutil
import urllib.request
from pathlib import Path
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────

DONE_DIR       = r"G:\My Drive\raw_intake\_done"
DATASET_DIR    = r"G:\My Drive\Archive\dataset"
import os as _os
CLAUDE_API_KEY = _os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
LABELS         = ["product", "tag", "transition"]
IMAGE_EXTS     = {".jpg", ".jpeg", ".JPG", ".JPEG"}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def classify_with_claude(image_path: Path) -> str:
    from PIL import Image
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    image_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 10,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}
                },
                {
                    "type": "text",
                    "text": (
                        "Classify this resale clothing photo. Reply with one word only.\n\n"
                        "product - garment laid flat on a surface, overhead shot\n"
                        "tag - hands holding garment showing a sewn-in brand label\n"
                        "transition - garment inside a clear plastic ziplock bag with a handwritten number\n\n"
                        "Reply with only: product, tag, or transition"
                    )
                }
            ]
        }]
    }).encode("utf-8")

    req = urllib.request.Request(
        CLAUDE_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01"
        }
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        label  = result["content"][0]["text"].strip().lower()
        return label if label in LABELS else "product"


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    done_path    = Path(DONE_DIR)
    dataset_path = Path(DATASET_DIR)

    for label in LABELS:
        (dataset_path / label).mkdir(parents=True, exist_ok=True)

    # Build set of filenames already in the dataset (skip re-classifying)
    existing = set()
    for label in LABELS:
        for f in (dataset_path / label).iterdir():
            existing.add(f.name.lower())

    log(f"Dataset already contains {len(existing)} images")

    # Collect new images from _done subfolders
    candidates = []
    for item_folder in sorted(done_path.iterdir()):
        if not item_folder.is_dir():
            continue
        for f in sorted(item_folder.iterdir()):
            if f.suffix in IMAGE_EXTS and f.name.lower() not in existing:
                candidates.append(f)

    log(f"Found {len(candidates)} new images to classify")

    if not candidates:
        log("Nothing to do — dataset is up to date.")
        return

    counts = {label: 0 for label in LABELS}
    errors = 0

    for i, img_path in enumerate(candidates, 1):
        try:
            label = classify_with_claude(img_path)
            dest  = dataset_path / label / img_path.name
            shutil.copy2(img_path, dest)
            counts[label] += 1
            log(f"  [{i}/{len(candidates)}] {img_path.name} -> {label}")
        except Exception as e:
            log(f"  [{i}/{len(candidates)}] ERROR on {img_path.name}: {e}")
            errors += 1

    log("\nDone!")
    log(f"  Added {sum(counts.values())} images:")
    for label, count in counts.items():
        log(f"    {label}: +{count}")
    if errors:
        log(f"  Errors: {errors}")

    log("\nUpdated dataset totals:")
    for label in LABELS:
        total = len([f for f in (dataset_path / label).iterdir() if f.suffix in IMAGE_EXTS])
        log(f"  {label}: {total}")


if __name__ == "__main__":
    main()
