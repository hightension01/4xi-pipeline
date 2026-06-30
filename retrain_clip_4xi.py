"""
4XI Studios - CLIP Incremental Retrainer
Fine-tunes the existing classifier on the full (growing) dataset.
Lighter than a full retrain: fewer epochs, lower LR, starts from saved weights.

Called automatically by the watcher after each batch, or run manually any time.

Usage:
    python retrain_clip_4xi.py
"""

import json
import random
from pathlib import Path

import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from transformers import CLIPModel, CLIPProcessor

# ── CONFIG ────────────────────────────────────────────────────────────────────

DATASET_DIR = r"G:\My Drive\Archive\dataset"
OUTPUT_DIR  = r"G:\My Drive\Archive\model\clip_4xi"
LABELS      = ["product", "tag", "transition"]
TRAIN_SPLIT = 0.9
BATCH_SIZE  = 16
EPOCHS      = 5       # lighter than the 10-epoch full train
LR          = 5e-6   # lower LR for fine-tuning from existing weights
IMAGE_EXTS  = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
MODEL_ID    = "openai/clip-vit-base-patch32"

# ── MODEL / DATASET ───────────────────────────────────────────────────────────

class ClothingDataset(Dataset):
    def __init__(self, samples, processor):
        self.samples   = samples
        self.processor = processor

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        img    = Image.open(path).convert("RGB")
        inputs = self.processor(images=img, return_tensors="pt", padding=True)
        return inputs["pixel_values"].squeeze(0), label


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

def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if device.type == "cuda":
        print(f"GPU: {torch.cuda.get_device_name(0)}")

    out_path     = Path(OUTPUT_DIR)
    weights_path = out_path / "best_model.pt"
    config_path  = out_path / "config.json"

    if not weights_path.exists():
        print("No existing model found — run train_clip_4xi.py for the initial training first.")
        return

    print("Loading CLIP processor...")
    processor = CLIPProcessor.from_pretrained(MODEL_ID)

    # Collect dataset
    dataset_path = Path(DATASET_DIR)
    all_samples  = []
    counts       = {}
    for label_idx, label in enumerate(LABELS):
        label_dir = dataset_path / label
        if not label_dir.exists():
            continue
        images = [f for f in label_dir.iterdir() if f.suffix in IMAGE_EXTS]
        counts[label] = len(images)
        for img_path in images:
            all_samples.append((img_path, label_idx))

    print(f"\nDataset:")
    for label, count in counts.items():
        print(f"  {label}: {count}")
    print(f"  Total: {len(all_samples)}")

    random.seed(42)
    random.shuffle(all_samples)
    split         = int(len(all_samples) * TRAIN_SPLIT)
    train_samples = all_samples[:split]
    val_samples   = all_samples[split:]
    print(f"Train: {len(train_samples)} | Val: {len(val_samples)}")

    # num_workers=0 avoids Windows multiprocessing issues
    train_dl = DataLoader(ClothingDataset(train_samples, processor),
                          batch_size=BATCH_SIZE, shuffle=True, num_workers=0)
    val_dl   = DataLoader(ClothingDataset(val_samples, processor),
                          batch_size=BATCH_SIZE, shuffle=False, num_workers=0)

    print(f"\nLoading existing weights from {weights_path}...")
    model = CLIPClassifier(MODEL_ID, num_classes=len(LABELS)).to(device)
    model.load_state_dict(torch.load(weights_path, map_location=device))

    prev_best = 0.0
    if config_path.exists():
        with open(config_path) as f:
            prev_best = json.load(f).get("val_acc", 0.0)
    print(f"Previous best val acc: {prev_best:.1%}")

    optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=0.01)

    class_counts = [counts.get(label, 1) for label in LABELS]
    total        = sum(class_counts)
    weights      = torch.tensor([total / c for c in class_counts], dtype=torch.float).to(device)
    weights      = weights / weights.sum() * len(LABELS)
    print(f"Class weights: { {l: f'{w:.2f}' for l, w in zip(LABELS, weights.tolist())} }")
    criterion    = nn.CrossEntropyLoss(weight=weights)
    best_val_acc = prev_best

    print(f"\nFine-tuning {EPOCHS} epochs at LR={LR}...\n")

    for epoch in range(1, EPOCHS + 1):
        # Train
        model.train()
        train_loss, train_correct = 0.0, 0
        for pixel_values, labels in train_dl:
            pixel_values, labels = pixel_values.to(device), labels.to(device)
            optimizer.zero_grad()
            logits = model(pixel_values)
            loss   = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            train_loss    += loss.item() * len(labels)
            train_correct += (logits.argmax(dim=-1) == labels).sum().item()

        train_loss /= len(train_samples)
        train_acc   = train_correct / len(train_samples)

        # Validate
        model.eval()
        val_loss, val_correct = 0.0, 0
        per_class_correct = [0] * len(LABELS)
        per_class_total   = [0] * len(LABELS)

        with torch.no_grad():
            for pixel_values, labels in val_dl:
                pixel_values, labels = pixel_values.to(device), labels.to(device)
                logits       = model(pixel_values)
                val_loss    += criterion(logits, labels).item() * len(labels)
                preds        = logits.argmax(dim=-1)
                val_correct += (preds == labels).sum().item()
                for pred, label in zip(preds.cpu(), labels.cpu()):
                    per_class_total[label] += 1
                    if pred == label:
                        per_class_correct[label] += 1

        val_loss /= len(val_samples)
        val_acc   = val_correct / len(val_samples)

        print(f"Epoch {epoch}/{EPOCHS} | "
              f"train loss {train_loss:.4f} acc {train_acc:.3f} | "
              f"val loss {val_loss:.4f} acc {val_acc:.3f}")
        for i, label in enumerate(LABELS):
            if per_class_total[i] > 0:
                acc = per_class_correct[i] / per_class_total[i]
                print(f"  {label:<12} {per_class_correct[i]}/{per_class_total[i]} ({acc:.0%})")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), out_path / "best_model.pt")
            config = {"model_id": MODEL_ID, "labels": LABELS, "val_acc": best_val_acc}
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            print(f"  -> Saved new best model (val acc {val_acc:.1%})"  )

    if best_val_acc > prev_best:
        print(f"\nImproved: {prev_best:.1%} -> {best_val_acc:.1%}")
    else:
        print(f"\nNo improvement over {prev_best:.1%} — existing weights kept.")


if __name__ == "__main__":
    main()
