"""
4XI Studios - CLIP Fine-Tune Classifier
Trains a 3-class image classifier (product / tag / transition)
on top of CLIP ViT-B/32. Runs fully local on GPU.

Usage:
    py -3.12 train_clip_4xi.py

Output:
    G:/My Drive/Archive/model/clip_4xi/best_model.pt
    G:/My Drive/Archive/model/clip_4xi/config.json
"""

import json
import os
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
EPOCHS      = 10
LR          = 3e-5
IMAGE_EXTS  = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
MODEL_ID    = "openai/clip-vit-base-patch32"

# ── DATASET ───────────────────────────────────────────────────────────────────

class ClothingDataset(Dataset):
    def __init__(self, samples, processor):
        self.samples   = samples  # list of (path, label_idx)
        self.processor = processor

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB")
        inputs = self.processor(images=img, return_tensors="pt", padding=True)
        pixel_values = inputs["pixel_values"].squeeze(0)
        return pixel_values, label


# ── MODEL ─────────────────────────────────────────────────────────────────────

class CLIPClassifier(nn.Module):
    def __init__(self, model_id, num_classes):
        super().__init__()
        self.clip       = CLIPModel.from_pretrained(model_id)
        hidden_size     = self.clip.config.projection_dim  # 512 for ViT-B/32
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

    # Load processor
    print(f"Loading CLIP processor from {MODEL_ID}...")
    processor = CLIPProcessor.from_pretrained(MODEL_ID)

    # Collect samples
    dataset_path = Path(DATASET_DIR)
    all_samples = []
    counts = {}
    for label_idx, label in enumerate(LABELS):
        label_dir = dataset_path / label
        if not label_dir.exists():
            print(f"Warning: {label} folder not found, skipping")
            continue
        images = [f for f in label_dir.iterdir() if f.suffix in IMAGE_EXTS]
        counts[label] = len(images)
        for img_path in images:
            all_samples.append((img_path, label_idx))

    print(f"\nDataset:")
    for label, count in counts.items():
        print(f"  {label}: {count}")
    print(f"  Total: {len(all_samples)}")

    # Split
    random.seed(42)
    random.shuffle(all_samples)
    split = int(len(all_samples) * TRAIN_SPLIT)
    train_samples = all_samples[:split]
    val_samples   = all_samples[split:]
    print(f"\nTrain: {len(train_samples)} | Val: {len(val_samples)}")

    # Dataloaders
    train_ds = ClothingDataset(train_samples, processor)
    val_ds   = ClothingDataset(val_samples, processor)
    train_dl = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,
                          num_workers=0, pin_memory=(device.type == "cuda"))
    val_dl   = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False,
                          num_workers=0, pin_memory=(device.type == "cuda"))

    # Model
    print(f"\nLoading CLIP model...")
    model = CLIPClassifier(MODEL_ID, num_classes=len(LABELS)).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=0.01)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)

    # Weight each class inversely by frequency so rare classes (tag) aren't ignored
    class_counts = [counts.get(label, 1) for label in LABELS]
    total        = sum(class_counts)
    weights      = torch.tensor([total / c for c in class_counts], dtype=torch.float).to(device)
    weights      = weights / weights.sum() * len(LABELS)  # normalise so mean weight = 1
    print(f"\nClass weights: { {l: f'{w:.2f}' for l, w in zip(LABELS, weights.tolist())} }")
    criterion = nn.CrossEntropyLoss(weight=weights)

    # Output dir
    out_path = Path(OUTPUT_DIR)
    out_path.mkdir(parents=True, exist_ok=True)

    best_val_acc = 0.0

    print(f"\nTraining for {EPOCHS} epochs...\n")
    for epoch in range(1, EPOCHS + 1):
        # ── Train ──
        model.train()
        train_loss = 0.0
        train_correct = 0
        for pixel_values, labels in train_dl:
            pixel_values = pixel_values.to(device)
            labels       = labels.to(device)
            optimizer.zero_grad()
            logits = model(pixel_values)
            loss   = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            train_loss    += loss.item() * len(labels)
            train_correct += (logits.argmax(dim=-1) == labels).sum().item()

        scheduler.step()
        train_loss /= len(train_samples)
        train_acc   = train_correct / len(train_samples)

        # ── Val ──
        model.eval()
        val_loss = 0.0
        val_correct = 0
        per_class_correct = [0] * len(LABELS)
        per_class_total   = [0] * len(LABELS)

        with torch.no_grad():
            for pixel_values, labels in val_dl:
                pixel_values = pixel_values.to(device)
                labels       = labels.to(device)
                logits = model(pixel_values)
                loss   = criterion(logits, labels)
                val_loss    += loss.item() * len(labels)
                preds        = logits.argmax(dim=-1)
                val_correct += (preds == labels).sum().item()
                for pred, label in zip(preds.cpu(), labels.cpu()):
                    per_class_total[label] += 1
                    if pred == label:
                        per_class_correct[label] += 1

        val_loss /= len(val_samples)
        val_acc   = val_correct / len(val_samples)

        print(f"Epoch {epoch:2d}/{EPOCHS} | "
              f"train loss {train_loss:.4f} acc {train_acc:.3f} | "
              f"val loss {val_loss:.4f} acc {val_acc:.3f}")

        for i, label in enumerate(LABELS):
            if per_class_total[i] > 0:
                acc = per_class_correct[i] / per_class_total[i]
                print(f"         {label:<12} {per_class_correct[i]}/{per_class_total[i]} ({acc:.0%})")

        # Save best
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), out_path / "best_model.pt")
            print(f"         -> Saved best model (val acc {val_acc:.3f})")

    # Save config
    config = {
        "model_id": MODEL_ID,
        "labels": LABELS,
        "val_acc": best_val_acc,
    }
    with open(out_path / "config.json", "w") as f:
        json.dump(config, f, indent=2)

    print(f"\nDone. Best val accuracy: {best_val_acc:.1%}")
    print(f"Model saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
