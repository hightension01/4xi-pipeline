"""
4XI Studios - Dataset Formatter for LLaMA Factory
Converts the classified image folders into the JSON format
LLaMA Factory expects for vision fine-tuning.

Output:
  G:/My Drive/Archive/dataset/train.json   — training data (90%)
  G:/My Drive/Archive/dataset/val.json     — validation data (10%)
  G:/My Drive/Archive/dataset/dataset_info.json — LLaMA Factory registry
"""

import json
import random
import shutil
from pathlib import Path
from PIL import Image

# ── CONFIG ────────────────────────────────────────────────────────────────────

DATASET_DIR  = r"G:\My Drive\Archive\dataset"
RESIZED_DIR  = r"G:\My Drive\Archive\dataset\resized"
TRAIN_SPLIT  = 0.9
TARGET_SIZE  = 336  # LLaVA-1.6 native resolution

IMAGE_EXTS = {".jpg", ".jpeg", ".JPG", ".JPEG", ".png", ".PNG"}

# Label to natural language instruction mapping
LABEL_MAP = {
    "product": "product",
    "tag": "tag",
    "transition": "transition"
}

SYSTEM_PROMPT = (
    "You are classifying resale clothing photos for 4XI Studios. "
    "Classify the image into exactly one category and reply with only that word. "
    "product - garment laid flat on a white surface, overhead or angled shot. "
    "tag - hands visible holding garment to show a sewn-in or printed brand label. "
    "transition - garment inside a clear plastic ziplock bag with a handwritten number."
)

USER_PROMPT = "Classify this clothing photo. Reply with only one word: product, tag, or transition."

# ── MAIN ──────────────────────────────────────────────────────────────────────

def resize_image(src: Path, dst: Path, size: int = TARGET_SIZE):
    """Resize image to exactly size x size with letterboxing, save as JPEG."""
    img = Image.open(src).convert("RGB")
    # Resize preserving aspect ratio
    img.thumbnail((size, size), Image.LANCZOS)
    # Pad to exact square
    padded = Image.new("RGB", (size, size), (255, 255, 255))
    offset = ((size - img.width) // 2, (size - img.height) // 2)
    padded.paste(img, offset)
    dst.parent.mkdir(parents=True, exist_ok=True)
    padded.save(dst, format="JPEG", quality=90)


def main():
    dataset_path = Path(DATASET_DIR)
    resized_path = Path(RESIZED_DIR)

    samples = []
    counts = {}

    for label in ["product", "tag", "transition"]:
        label_dir = dataset_path / label
        if not label_dir.exists():
            print(f"Warning: {label} folder not found")
            continue

        images = [f for f in label_dir.iterdir() if f.suffix in IMAGE_EXTS]
        counts[label] = len(images)

        for img_path in images:
            # Resize to LLaVA native resolution
            dst = resized_path / label / (img_path.stem + ".jpg")
            if not dst.exists():
                try:
                    resize_image(img_path, dst)
                except Exception as e:
                    print(f"  Warning: could not resize {img_path.name}: {e}")
                    dst = img_path  # fall back to original

            samples.append({
                "messages": [
                    {
                        "from": "human",
                        "value": f"<image>{USER_PROMPT}"
                    },
                    {
                        "from": "gpt",
                        "value": LABEL_MAP[label]
                    }
                ],
                "images": [str(dst)],
                "system": SYSTEM_PROMPT
            })

    print(f"Total samples: {len(samples)}")
    for label, count in counts.items():
        print(f"  {label}: {count}")

    # Shuffle and split
    random.seed(42)
    random.shuffle(samples)

    split_idx = int(len(samples) * TRAIN_SPLIT)
    train_samples = samples[:split_idx]
    val_samples = samples[split_idx:]

    print(f"\nTrain: {len(train_samples)} samples")
    print(f"Val:   {len(val_samples)} samples")

    # Write train.json
    train_path = dataset_path / "train.json"
    with open(train_path, "w", encoding="utf-8") as f:
        json.dump(train_samples, f, indent=2)
    print(f"\nWritten: {train_path}")

    # Write val.json
    val_path = dataset_path / "val.json"
    with open(val_path, "w", encoding="utf-8") as f:
        json.dump(val_samples, f, indent=2)
    print(f"Written: {val_path}")

    # Write dataset_info.json for LLaMA Factory registry
    dataset_info = {
        "4xi_train": {
            "file_name": "train.json",
            "formatting": "sharegpt",
            "columns": {
                "messages": "messages",
                "images": "images",
                "system": "system"
            }
        },
        "4xi_val": {
            "file_name": "val.json",
            "formatting": "sharegpt",
            "columns": {
                "messages": "messages",
                "images": "images",
                "system": "system"
            }
        }
    }

    info_path = dataset_path / "dataset_info.json"
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(dataset_info, f, indent=2)
    print(f"Written: {info_path}")

    print("\nDataset ready for LLaMA Factory.")


if __name__ == "__main__":
    main()
