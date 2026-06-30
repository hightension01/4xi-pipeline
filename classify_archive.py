"""
4XI Studios - Archive Classifier
Uses Claude Haiku to classify all images in the archive and sort them
into a clean dataset folder structure for fine-tuning.

Output structure:
  G:/My Drive/archive/dataset/
    product/      — flat lay garment shots
    tag/          — label/collar shots
    transition/   — bag shots with SKU number
    log.csv       — full classification record
    progress.json — resume state if interrupted

Usage:
  python classify_archive.py

Rate limiting: ~30 images/minute to stay under Tier 1 limits (50k ITPM).
Resumable: if interrupted, re-run and it picks up where it left off.
"""

import json
import time
import csv
import base64
import shutil
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────

ARCHIVE_RAW   = r"G:\My Drive\Archive\Raw"
OUTPUT_DIR    = r"G:\My Drive\Archive\dataset"
PROGRESS_FILE = r"G:\My Drive\Archive\dataset\progress.json"
LOG_FILE      = r"G:\My Drive\Archive\dataset\log.csv"

import os as _os
CLAUDE_API_KEY = _os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-haiku-4-5-20251001"

# Rate limiting — stay comfortably under Tier 1 50k ITPM
# ~1500 tokens per image, 2 second sleep = ~30 images/min = ~45k tokens/min
SLEEP_BETWEEN_CALLS = 2.0

IMAGE_EXTS = {".jpg", ".jpeg", ".JPG", ".JPEG"}

CLASSIFICATION_PROMPT = (
    "Classify this resale clothing photo into exactly one category. "
    "Reply with only the single word.\n\n"
    "product - garment laid flat on a board or surface, overhead or angled shot. "
    "Hands may or may not be visible. Includes front shots, back shots, detail shots, "
    "and any supplementary photos of the garment itself.\n"
    "tag - close-up of a clothing label or tag, either sewn-in at collar or on a hangtag. "
    "Usually hands are visible holding the garment open to show the label.\n"
    "transition - item sealed in a clear plastic ziplock bag with a handwritten number on it. "
    "The bag may be lying flat or held up by a hand. "
    "If you see a plastic ziplock bag with a number written on it, it is ALWAYS transition.\n\n"
    "Reply with only one word: product, tag, or transition"
)

# ── HELPERS ───────────────────────────────────────────────────────────────────

def log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")


def resize_image(image_path: Path, max_size: int = 1024) -> bytes:
    """Resize image for API submission."""
    try:
        from PIL import Image
        import io
        img = Image.open(image_path)
        img.thumbnail((max_size, max_size))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except ImportError:
        with open(image_path, "rb") as f:
            return f.read()
    except Exception:
        with open(image_path, "rb") as f:
            return f.read()


def classify_image(image_path: Path) -> tuple[str, str]:
    """
    Classify image using Claude Haiku.
    Returns (classification, error_message).
    """
    try:
        image_bytes = resize_image(image_path)
        image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
        suffix = image_path.suffix.lower()
        media_type = "image/jpeg" if suffix in [".jpg", ".jpeg"] else "image/png"

        payload = json.dumps({
            "model": CLAUDE_MODEL,
            "max_tokens": 50,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data
                        }
                    },
                    {"type": "text", "text": CLASSIFICATION_PROMPT}
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

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            text = result["content"][0]["text"].strip().lower()
            if text in ["product", "tag", "transition"]:
                return text, ""
            return "product", f"unexpected response: {text}"

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        # Handle rate limiting
        if e.code == 429:
            return "", f"rate_limited: {body}"
        return "product", f"http_error_{e.code}: {body[:100]}"
    except Exception as e:
        return "product", f"error: {str(e)[:100]}"


def load_progress() -> set:
    """Load set of already-processed filenames."""
    progress_path = Path(PROGRESS_FILE)
    if progress_path.exists():
        try:
            with open(progress_path, "r") as f:
                data = json.load(f)
                return set(data.get("completed", []))
        except Exception:
            return set()
    return set()


def save_progress(completed: set):
    """Save progress to disk."""
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump({"completed": list(completed), "updated": datetime.now().isoformat()}, f)
    except Exception as e:
        log(f"Warning: could not save progress — {e}")


def append_log(log_path: Path, row: dict, write_header: bool = False):
    """Append a classification result to the log CSV."""
    fieldnames = ["timestamp", "folder", "filename", "filepath", "label", "error"]
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    archive_path = Path(ARCHIVE_RAW)
    output_path = Path(OUTPUT_DIR)

    if not archive_path.exists():
        log(f"ERROR: Archive path not found: {ARCHIVE_RAW}")
        return

    # Create output folders
    for label in ["product", "tag", "transition"]:
        (output_path / label).mkdir(parents=True, exist_ok=True)

    # Load progress for resumability
    completed = load_progress()
    if completed:
        log(f"Resuming — {len(completed)} images already classified")

    # Check if log needs header
    log_path = Path(LOG_FILE)
    write_header = not log_path.exists()

    # Collect all images
    item_folders = sorted([f for f in archive_path.iterdir() if f.is_dir()])
    all_images = []
    for folder in item_folders:
        images = sorted([
            f for f in folder.iterdir()
            if f.is_file() and f.suffix in IMAGE_EXTS
        ])
        all_images.extend(images)

    total = len(all_images)
    remaining = [img for img in all_images if img.name not in completed]

    log(f"Archive: {len(item_folders)} folders, {total} total images")
    log(f"Remaining: {len(remaining)} images to classify")
    log(f"Estimated time: ~{len(remaining) * SLEEP_BETWEEN_CALLS / 60:.0f} minutes")
    log(f"Estimated cost: ~${len(remaining) * 0.002:.2f}")
    log("")

    # Classify
    counts = {"product": 0, "tag": 0, "transition": 0, "error": 0}

    for idx, image_path in enumerate(remaining, 1):
        folder_name = image_path.parent.name
        print(f"\r  [{idx}/{len(remaining)}] {folder_name}/{image_path.name}...", end="", flush=True)

        label, error = classify_image(image_path)

        # Handle rate limiting with retry
        if label == "" and "rate_limited" in error:
            print()
            log(f"Rate limited — waiting 60 seconds...")
            time.sleep(60)
            label, error = classify_image(image_path)
            if label == "":
                label = "product"
                error = "rate_limited_twice"

        # Copy to dataset folder
        dest = output_path / label / image_path.name
        # Handle filename collisions across folders
        if dest.exists():
            dest = output_path / label / f"{folder_name}_{image_path.name}"
        shutil.copy2(image_path, dest)

        # Log result
        append_log(log_path, {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "folder": folder_name,
            "filename": image_path.name,
            "filepath": str(image_path),
            "label": label,
            "error": error
        }, write_header=write_header)
        write_header = False

        # Track progress
        completed.add(image_path.name)
        counts[label if label in counts else "error"] += 1

        # Save progress every 20 images
        if idx % 20 == 0:
            save_progress(completed)

        time.sleep(SLEEP_BETWEEN_CALLS)

    # Final save
    save_progress(completed)
    print()
    log("")
    log("Classification complete!")
    log(f"  product:    {counts['product']}")
    log(f"  tag:        {counts['tag']}")
    log(f"  transition: {counts['transition']}")
    log(f"  errors:     {counts['error']}")
    log(f"Dataset written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
