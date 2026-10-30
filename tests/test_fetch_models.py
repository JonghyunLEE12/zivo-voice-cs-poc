"""Safety checks for download limits, independent of network/model availability."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("fetch_models", Path(__file__).parents[1] / "scripts/fetch_models.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


def test_over_limit_is_rejected():
    with pytest.raises(RuntimeError, match="20 GB"):
        fetch.validate_budget(20_000_000_001, 0, 0, 30_000_000_000)


def test_existing_other_models_count_towards_limit():
    with pytest.raises(RuntimeError, match="20 GB"):
        fetch.validate_budget(17_000_000_000, 4_000_000_000, 17_000_000_000, 30_000_000_000)


def test_free_disk_includes_reserve():
    with pytest.raises(RuntimeError, match="free disk"):
        fetch.validate_budget(10, 0, 10, 2_000_000_009)


def test_already_cached_download_fits():
    fetch.validate_budget(17_000_000_000, 17_000_000_000, 0, 2_000_000_000)


def test_cosyvoice_excludes_duplicate_weights():
    import fnmatch
    patterns = next(s[3] for s in fetch.SPECS if s[0] == "cosyvoice3")
    for name in ["llm.rl.pt", "speech_tokenizer_v3.batch.onnx", "flow.decoder.estimator.fp32.onnx"]:
        assert not any(fnmatch.fnmatchcase(name, p) for p in patterns)
    for name in ["llm.pt", "flow.pt", "hift.pt", "cosyvoice3.yaml", "CosyVoice-BlankEN/model.safetensors"]:
        assert any(fnmatch.fnmatchcase(name, p) for p in patterns)


def test_partial_download_is_not_double_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "MODEL_ROOT", tmp_path)
    model = {"repo_id": "a/b", "revision": "abc"}
    file = {"path": "weights", "size": 100, "blob_id": "hash"}
    partial = tmp_path / "hub/models--a--b/blobs/hash.incomplete"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"x" * 40)
    assert fetch.existing_size() == 40
    assert fetch.remaining_bytes(model, file) == 60


def test_unknown_partial_files_still_count(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "MODEL_ROOT", tmp_path)
    (tmp_path / "abandoned.incomplete").write_bytes(b"x" * 100)
    assert fetch.existing_size() == 100
