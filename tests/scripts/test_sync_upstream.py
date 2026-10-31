from pathlib import Path

import pytest

from scripts import sync_upstream


def _upstream(tmp_path: Path) -> Path:
    tmp = tmp_path / "upstream"
    (tmp / "chuni_penguin").mkdir(parents=True)
    (tmp / "chuni_penguin" / "a.py").write_text("A = 1\n")
    (tmp / "chuni_penguin" / "data.json").write_text("{}")
    (tmp / "LICENSE").write_text("license")
    return tmp


def _vendored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "third_party" / "chuni_penguin"
    target.mkdir(parents=True)
    (target / "old.py").write_text("OLD = 1\n")
    monkeypatch.setattr(sync_upstream, "THIRD_PARTY_DIR", target)
    monkeypatch.setattr(sync_upstream, "EXTRA_DATA_FILES", ("chuni_penguin/data.json",))
    return target


def test_vendor_replaces_the_tree_with_the_new_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _vendored(tmp_path, monkeypatch)

    sync_upstream.vendor(_upstream(tmp_path), [Path("chuni_penguin/a.py")])

    assert sorted(p.name for p in target.iterdir()) == ["UPSTREAM_LICENSE", "a.py", "data.json"]
    assert not (target.parent / ".sync_staging").exists()


def test_vendor_leaves_the_existing_tree_untouched_when_a_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _vendored(tmp_path, monkeypatch)
    monkeypatch.setattr(sync_upstream, "EXTRA_DATA_FILES", ("chuni_penguin/gone.json",))

    with pytest.raises(SystemExit):
        sync_upstream.vendor(_upstream(tmp_path), [Path("chuni_penguin/a.py")])

    assert (target / "old.py").read_text() == "OLD = 1\n"
    assert not (target.parent / ".sync_staging").exists()
