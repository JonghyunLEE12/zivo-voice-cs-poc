"""Plan and resume a revision-pinned, size-limited download into models/ only."""
import argparse
import fnmatch
import fcntl
import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "models"
# Set before importing the hub: keep blobs and transfer cache within the project.
os.environ["HF_HOME"] = str(MODEL_ROOT)
os.environ["HF_HUB_CACHE"] = str(MODEL_ROOT / "hub")
os.environ["HF_XET_CACHE"] = str(MODEL_ROOT / "xet")
os.environ["HF_XET_CHUNK_CACHE_SIZE_BYTES"] = "0"
from huggingface_hub import HfApi, snapshot_download

LIMIT = 20_000_000_000
SPECS = [
    ("whisper", "stt", "mlx-community/whisper-large-v3-turbo", ["*"], 1.61),
    ("voxcpm2", "mlx", "mlx-community/VoxCPM2-bf16", ["*"], 4.96),
    ("cosyvoice3", "cosyvoice3", "FunAudioLLM/Fun-CosyVoice3-0.5B-2512", ["llm.pt", "flow.pt", "hift.pt", "speech_tokenizer_v3.onnx", "campplus.onnx", "CosyVoice-BlankEN/*", "*.json", "*.yaml", "README.md", ".gitattributes"], 5.42),
    ("qwen3tts", "mlx", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16", ["*"], 4.54),
    ("kokoro", "mlx", "mlx-community/Kokoro-82M-bf16", ["*"], 0.39),
    ("smartturn", "turn", "pipecat-ai/smart-turn-v3", ["smart-turn-v3.2-cpu.onnx"], 0.01),
    ("qwen3asr", "optional", "mlx-community/Qwen3-ASR-0.6B-bf16", ["*"], 1.57),
]


def model_plan(spec):
    key, group, repo, patterns, expected = spec
    info = HfApi().model_info(repo, files_metadata=True)
    files = [{"path": f.rfilename, "size": f.size,
              "blob_id": f.lfs.sha256 if f.lfs else f.blob_id,
              "sha256": f.lfs.sha256 if f.lfs else None} for f in info.siblings
             if any(fnmatch.fnmatchcase(f.rfilename, p) for p in patterns)]
    if not files or any(f["size"] is None for f in files):
        raise RuntimeError(f"Missing file size metadata: {repo}")
    return dict(key=key, group=group, repo_id=repo, revision=info.sha,
                allow_patterns=patterns, expected_GB=expected, files=files,
                bytes=sum(f["size"] for f in files))


def existing_size():
    # Count physical regular files only; snapshot symlinks must not double count blobs.
    return sum(p.stat().st_size for p in MODEL_ROOT.rglob("*")
               if p.is_file() and not p.is_symlink())


def cached_file(model, file):
    repo_dir = "models--" + model["repo_id"].replace("/", "--")
    return MODEL_ROOT / "hub" / repo_dir / "snapshots" / model["revision"] / file["path"]


def remaining_bytes(model, file):
    cached = cached_file(model, file)
    if cached.is_file() and cached.stat().st_size == file["size"]:
        return 0
    repo_dir = "models--" + model["repo_id"].replace("/", "--")
    blob = MODEL_ROOT / "hub" / repo_dir / "blobs" / file["blob_id"]
    if blob.is_file() and blob.stat().st_size == file["size"]:
        return 0
    partial = blob.with_name(blob.name + ".incomplete")
    return max(0, file["size"] - (partial.stat().st_size if partial.is_file() else 0))


def validate_budget(plan, existing, missing, free):
    if plan > LIMIT or existing + missing > LIMIT:
        raise RuntimeError(f"20 GB model limit exceeded: plan={plan}, existing={existing}, missing={missing}")
    if free < missing + 2_000_000_000:
        raise RuntimeError(f"Not enough free disk: free={free}, missing={missing}, reserve=2000000000")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--download", action="store_true")
    parser.add_argument("--only", default="stt,mlx,cosyvoice3", help="groups or model keys; required Smart Turn is always included")
    parser.add_argument("--include-optional", action="store_true")
    args = parser.parse_args()
    MODEL_ROOT.mkdir(exist_ok=True)
    lock = None
    if args.download:
        lock = (MODEL_ROOT / ".fetch.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another model download is running; wait for it to finish")
    requested = set(args.only.split(","))
    known = {s[0] for s in SPECS} | {s[1] for s in SPECS}
    if requested - known:
        parser.error(f"Unknown selection: {requested - known}")
    specs = [s for s in SPECS if s[0] == "smartturn" or s[0] in requested or s[1] in requested or (args.include_optional and s[1] == "optional")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        models = list(pool.map(model_plan, specs))
    total = sum(m["bytes"] for m in models)
    missing = sum(remaining_bytes(m, f) for m in models for f in m["files"])
    existing = existing_size()
    free = shutil.disk_usage(ROOT).free
    validate_budget(total, existing, missing, free)
    for m in models:
        print(f"{m['key']:12} {m['bytes']/1e9:.6f} GB (DESIGN {m['expected_GB']:.2f}) {m['repo_id']} @{m['revision']}", flush=True)
        for f in m["files"]:
            print(f"  {f['size']:12} {f['path']}", flush=True)
    print(f"TOTAL {total} bytes = {total/1e9:.6f} GB; DESIGN rounded sum {sum(m['expected_GB'] for m in models):.2f} GB; LIMIT 20.00 GB", flush=True)
    print(f"Missing {missing/1e9:.6f} GB; existing {existing/1e9:.6f} GB; free {free/1e9:.3f} GB", flush=True)
    report = dict(total_bytes=total, missing_bytes=missing, existing_bytes=existing, free_bytes=free, models=models)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/models-plan.json").write_text(json.dumps(report, indent=2))
    if args.download:
        paths_file = MODEL_ROOT / "paths.json"
        paths = json.loads(paths_file.read_text()) if paths_file.exists() else {}
        for m in models:
            print(f"Downloading {m['key']}...", flush=True)
            path = snapshot_download(m["repo_id"], revision=m["revision"], allow_patterns=m["allow_patterns"], cache_dir=str(MODEL_ROOT / "hub"), max_workers=4)
            for f in m["files"]:
                p = Path(path) / f["path"]
                if not p.is_file() or p.stat().st_size != f["size"]:
                    raise RuntimeError(f"Downloaded file size mismatch: {p}")
                if f["sha256"]:
                    with p.open("rb") as stream:
                        actual = hashlib.file_digest(stream, "sha256").hexdigest()
                    if actual != f["sha256"]:
                        raise RuntimeError(f"Downloaded file SHA256 mismatch: {p}")
            paths[m["key"]] = str(Path(path).relative_to(ROOT))
            (MODEL_ROOT / "paths.json").write_text(json.dumps(paths, indent=2))
            print(f"Verified {m['key']}: {len(m['files'])} files, {m['bytes']} bytes", flush=True)
        print("ALL DOWNLOADS VERIFIED", flush=True)


if __name__ == "__main__":
    main()
