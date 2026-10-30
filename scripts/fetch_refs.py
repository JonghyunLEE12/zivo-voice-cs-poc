"""Stream FLEURS test archives; retain one 8–12 second female utterance per language."""
import csv
import hashlib
import io
import json
import tarfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = {"en": "en_us", "zh-CN": "cmn_hans_cn", "ja": "ja_jp", "yue": "yue_hant_hk"}


def get(url):
    return urllib.request.urlopen(url, timeout=120)


def fetch(locale, config, revision):
    base = f"https://huggingface.co/datasets/google/fleurs/resolve/{revision}/data/{config}"
    metadata_url = base + "/test.tsv"
    with get(metadata_url) as response:
        records = list(csv.reader(io.StringIO(response.read().decode()), delimiter="\t", quoting=csv.QUOTE_NONE))
    candidates = {r[1]: r for r in records if 8 <= int(r[5]) / 16000 <= 12 and r[6] == "FEMALE"}
    archive_url = base + "/audio/test.tar.gz"
    with get(archive_url) as response, tarfile.open(fileobj=response, mode="r|gz") as archive:
        for member in archive:
            name = Path(member.name).name
            if name not in candidates or not member.isfile():
                continue
            record = candidates[name]
            blob = archive.extractfile(member).read()
            audio, rate = sf.read(io.BytesIO(blob), dtype="float32")
            duration = len(audio) / rate
            peak = float(np.max(np.abs(audio)))
            rms = float(np.sqrt(np.mean(audio ** 2)))
            clipped = float(np.mean(np.abs(audio) >= 0.999))
            frame_size = rate // 50
            frames = audio[:len(audio) // frame_size * frame_size].reshape(-1, frame_size)
            deciles = np.quantile(np.sqrt(np.mean(frames ** 2, axis=1)), [.1, .9])
            contrast_db = float(20 * np.log10(deciles[1] / max(deciles[0], 1e-8)))
            if not (8 <= duration <= 12 and audio.ndim == 1 and rate == 16000 and rms > .0001 and clipped < .0001 and contrast_db >= 20):
                continue
            folder = ROOT / "data/refs" / locale
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "ref.wav").write_bytes(blob)
            (folder / "ref.txt").write_text(record[2] + "\n")
            result = dict(locale=locale, dataset="google/fleurs", config=config, split="test", revision=revision,
                          id=record[0], original_filename=name, archive_member=member.name,
                          source_url=archive_url, metadata_url=metadata_url,
                          license="CC-BY-4.0", license_url="https://creativecommons.org/licenses/by/4.0/",
                          attribution="Google FLEURS / FLEURS contributors", gender=record[6],
                          raw_transcription=record[2], transcription=record[3],
                          audio_path=str((folder / "ref.wav").relative_to(ROOT)),
                          text_path=str((folder / "ref.txt").relative_to(ROOT)),
                          duration_s=duration, sample_rate=rate, channels=1, peak=peak, rms=rms,
                          clipping_fraction=clipped, frame_rms_contrast_db=contrast_db, sha256=hashlib.sha256(blob).hexdigest(),
                          processing="Original complete WAV, no trimming/resampling/normalization",
                          quality_check="8–12 s, female, mono 16 kHz, RMS > 0.0001, clipped fraction < 0.0001, 90th/10th percentile 20 ms frame RMS contrast >= 20 dB; objective screening only")
            print(f"{locale}: {name}, {duration:.3f} s, rms={rms:.4f}, clipped={clipped:.6f}", flush=True)
            return result
    raise RuntimeError(f"No suitable reference for {config}")


def main():
    with get("https://huggingface.co/api/datasets/google/fleurs") as response:
        revision = json.load(response)["sha"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(lambda pair: fetch(*pair, revision), LANGUAGES.items()))
    aliases = {**{locale: locale for locale in LANGUAGES}, "zh-TW": "zh-CN"}
    for locale, target in aliases.items():
        folder = ROOT / "data/ref_voices" / locale
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("ref.wav", "ref.txt"):
            link = folder / name
            if not link.exists() and not link.is_symlink():
                link.symlink_to(f"../../refs/{target}/{name}")
    manifest = dict(dataset="google/fleurs", dataset_url="https://huggingface.co/datasets/google/fleurs",
                    license="CC-BY-4.0", revision=revision, references=refs,
                    locale_reference=aliases, shared_engines=["voxcpm2", "cosyvoice3", "qwen3tts"],
                    limitations=["대만 억양 참조 없음: zh-TW shares cmn_hans_cn with zh-CN", "Kokoro uses female presets; speaker identity differs"])
    (ROOT / "data/refs/MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
