"""Paths and local process endpoints shared by the PoC components."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
REFS_DIR = DATA_DIR / "refs"
DEMO_TODAY = os.getenv("DEMO_TODAY", "2026-11-01")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = "qwen3.5:35b-a3b"
PORT = int(os.getenv("PORT", "8000"))
ENGINE_URLS = {
    "voxcpm2": "http://localhost:8101",
    "cosyvoice3": "http://localhost:8102",
    "qwen3tts": "http://localhost:8103",
    "kokoro": "http://localhost:8104",
}
LOCALES = ("en", "zh-CN", "zh-TW", "ja", "yue")
