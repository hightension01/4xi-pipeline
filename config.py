"""
4XI Pipeline — central configuration.
All tunable settings live here. Secrets are loaded from .env (never hardcoded).
"""

import os
from pathlib import Path

# Load .env if present (no dependency on python-dotenv)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Inference provider ────────────────────────────────────────────────────────
# Set INFERENCE_PROVIDER in .env to switch: "deepseek" | "claude" | "openai"
INFERENCE_PROVIDER = os.environ.get("INFERENCE_PROVIDER", "deepseek")

DEEPSEEK_API_KEY   = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL   = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL     = "deepseek-chat"          # supports vision via image_url

CLAUDE_API_KEY     = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_API_URL     = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL       = "claude-haiku-4-5-20251001"

OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_URL     = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL       = "gpt-4o-mini"

# ── Paths ─────────────────────────────────────────────────────────────────────
GDRIVE_RAW         = r"G:\My Drive\raw_intake"
GDRIVE_PROCESSED   = r"G:\My Drive\processed_output"
GDRIVE_DONE        = r"G:\My Drive\raw_intake\_done"

COMFYUI_INPUT      = r"C:\Users\garre\Documents\ComfyUI\input"
COMFYUI_OUTPUT     = r"C:\Users\garre\Documents\ComfyUI\output"
COMFYUI_API        = "http://127.0.0.1:8000"

CLIP_MODEL_DIR     = r"G:\My Drive\Archive\model\clip_4xi"

PYTHON_EXE         = r"C:\Users\garre\AppData\Local\Programs\Python\Python312\python.exe"
REPO_ROOT          = Path(__file__).parent
RETRAIN_SCRIPT     = str(REPO_ROOT / "retrain_clip_4xi.py")
SESSION_FILE       = str(REPO_ROOT / "nifty-uploader" / "pipeline_session.json")

# ── Classifier tuning ─────────────────────────────────────────────────────────
CLIP_CONFIDENCE    = 0.45   # below this -> fall back to API classifier
POLL_INTERVAL      = 10     # seconds between folder scans
PROCESS_TIMEOUT    = 300    # seconds to wait for ComfyUI per image
DRY_RUN            = False  # True = classify only, no file moves

# ── Auto-retrain ──────────────────────────────────────────────────────────────
AUTO_DATASET       = True
RETRAIN_THRESHOLD  = 1      # new samples before triggering incremental retrain
