"""Read-only local prerequisites; never starts services or downloads Ollama models."""
import argparse
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check():
    rows = []
    def add(name, ok, detail):
        rows.append(dict(name=name, ok=bool(ok), detail=detail))
    add("python", sys.version_info[:2] == (3, 12), sys.version.split()[0])
    add("apple_silicon", platform.system() == "Darwin" and platform.machine() == "arm64", platform.platform())
    for executable in ("ffmpeg", "ollama", "uv", "pnpm"):
        path = shutil.which(executable)
        add(executable, path, path or "not found")
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=10) as response:
            models = json.load(response)["models"]
        target = next((m for m in models if m["name"] == "qwen3.5:35b-a3b"), None)
        add("ollama_model", target is not None, target or [m["name"] for m in models])
    except Exception as exc:
        add("ollama_model", False, str(exc))
    free = shutil.disk_usage(ROOT).free
    add("disk_free", free >= 2_000_000_000, {"bytes": free, "GB": round(free / 1e9, 3), "note": "fetch_models checks remaining download + 2 GB reserve"})
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = check()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{'PASS' if row['ok'] else 'FAIL'} {row['name']}: {row['detail']}")
    sys.exit(0 if all(r["ok"] for r in rows) else 1)
