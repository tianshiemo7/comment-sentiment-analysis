"""The public-corpus recipe preserves conflicting labels for downstream audit."""
import csv

import pytest

from scripts import prepare_training_data as recipe
from src.preprocessing.dataset import load_records


def test_recipe_keeps_conflicts_and_pins_downloads(tmp_path, monkeypatch):
    downloads = []

    def fake_download(name, target_dir, **kwargs):
        downloads.append((name, kwargs))
        path = target_dir / f"{name}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerows([("review", "label"), ("same text", "1"), ("same text", "0")])
        return path

    def inspect_preprocessing(path, *, output_root, **kwargs):
        records = load_records(path)
        assert len(records) == 4
        assert {record.label for record in records} == {"positive", "negative"}
        assert len({record.meta["comment_id"] for record in records}) == 4
        assert {record.meta["source"] for record in records} == {"waimai_10k", "chnsenticorp"}
        assert all(record.text == "same text" for record in records)
        assert not kwargs.get("allow_small_dataset")
        (output_root / "outputs/stats").mkdir(parents=True)
        return {"manifest": {"dataset_version": "test"}, "stats": {}}

    monkeypatch.setattr(recipe, "download_dataset", fake_download)
    monkeypatch.setattr(recipe, "prepare_dataset", inspect_preprocessing)
    report = recipe.prepare_training_data(tmp_path)
    assert not report["bilibili_annotations"]
    assert len(downloads) == 2
    for (_, options), (_, relative, sha256) in zip(downloads, recipe.SOURCES):
        assert options["sha256"] == sha256
        assert f"/{recipe.CORPUS_COMMIT}/datasets/{relative}" in options["url"]
    with pytest.raises(FileExistsError):
        recipe.prepare_training_data(tmp_path)
    assert len(downloads) == 2
