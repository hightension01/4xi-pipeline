"""
4XI Studios - CLIP Classifier Test
Runs the trained model against the dataset folders and prints accuracy.

Usage:
    py -3.12 test_clip_4xi.py
"""

import json
from pathlib import Path

import torch
import torch.nn as nn
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

# ── CONFIG ────────────────────────────────────────────────────────────────────

DATASET_DIR = r"G:\My Drive\Archive\dataset"
MODEL_DIR   = r"G:\My Drive\Archive\model\clip_4xi"
LABELS      = ["product", "tag", "transition"]
IMAGE_EXTS  = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}

# ── MODEL ─────────────────────────────────────────────────────────────────────

class CLIPClassifier(nn.Module):
    def __init__(self, model_id, num_classes):
        super().__init__()
        self.clip       = CLIPModel.from_pretrained(model_id)
        hidden_size     = self.clip.config.projection_dim
        self.classifier = nn.Sequential(
            nn.Linear(hidden_size, 256),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(256, num_classes),
        )

    def forward(self, pixel_values):
        vision_out = self.clip.vision_model(pixel_values=pixel_values)
        features   = self.clip.visual_projection(vision_out.pooler_output)
        features   = features / features.norm(dim=-1, keepdim=True)
        return self.classifier(features)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def classify_image(img_path, model, processor, device):
    img = Image.open(img_path).convert("RGB")
    inputs = processor(images=img, return_tensors="pt").to(device)
    with torch.no_grad():
        logits = model(inputs["pixel_values"])
        probs  = torch.softmax(logits, dim=-1)
        pred   = probs.argmax(dim=-1).item()
        conf   = probs[0][pred].item()
    return LABELS[pred], conf


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}\n")

    # Load config
    config_path = Path(MODEL_DIR) / "config.json"
    with open(config_path) as f:
        config = json.load(f)
    model_id = config["model_id"]

    # Load model
    print("Loading model...")
    processor = CLIPProcessor.from_pretrained(model_id)
    model     = CLIPClassifier(model_id, num_classes=len(LABELS))
    model.load_state_dict(torch.load(Path(MODEL_DIR) / "best_model.pt",
                                     map_location=device))
    model.to(device)
    model.eval()
    print("Model loaded.\n")

    # Test each class
    dataset_path = Path(DATASET_DIR)
    total_correct = 0
    total_count   = 0
    mistakes       = []

    for label in LABELS:
        label_dir = dataset_path / label
        if not label_dir.exists():
            print(f"Skipping {label} — folder not found")
            continue

        images = [f for f in label_dir.iterdir() if f.suffix in IMAGE_EXTS]
        correct = 0

        for img_path in images:
            pred, conf = classify_image(img_path, model, processor, device)
            if pred == label:
                correct += 1
            else:
                mistakes.append((img_path.name, label, pred, conf))

        acc = correct / len(images) if images else 0
        print(f"{label:<12} {correct:3d}/{len(images):3d}  ({acc:.1%})")
        total_correct += correct
        total_count   += len(images)

    overall = total_correct / total_count if total_count else 0
    print(f"\nOverall: {total_correct}/{total_count} ({overall:.1%})\n")

    if mistakes:
        print(f"Misclassified ({len(mistakes)}):")
        for name, true_label, pred, conf in sorted(mistakes, key=lambda x: -x[3]):
            print(f"  {name:<30} true={true_label:<12} pred={pred:<12} conf={conf:.2f}")
    else:
        print("No mistakes!")


if __name__ == "__main__":
    main()
