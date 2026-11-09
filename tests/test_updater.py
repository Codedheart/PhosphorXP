"""Self-updater: everything except the Windows-only file swap, which is a .bat we inspect."""
import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phosphor import updater  # noqa: E402
from phosphor.updater import Release, UpdateError  # noqa: E402


def test_version_falls_back_to_dev():
    from phosphor import version
    assert version.VERSION == "dev" or updater.parse_version(version.VERSION)


def test_parse_and_compare_versions():
    assert updater.parse_version("v1.2") == (1, 2)
    assert updater.parse_version("1.10.3") == (1, 10, 3)
    assert updater.parse_version(" v2 ") == (2,)
    for bad in ("dev", "", None, "v1.2-beta", "main", "v1..2"):
        assert updater.parse_version(bad) is None
    assert updater.is_newer((1, 10), (1, 9))          # numeric, not string, comparison
    assert updater.is_newer((1, 2, 1), (1, 2))
    assert not updater.is_newer((1, 2), (1, 2, 0))
    assert not updater.is_newer((1, 1), (1, 2))


def test_release_from_api():
    data = {"tag_name": "v1.3", "html_url": "https://x/rel", "assets": [
        {"name": "other.zip", "browser_download_url": "https://x/other"},
        {"name": updater.ASSET, "browser_download_url": "https://x/z", "size": 12,
         "digest": "sha256:ABC"}]}
    r = updater.release_from_api(data)
    assert (r.tag, r.version, r.asset_url, r.size, r.sha256) == ("v1.3", (1, 3), "https://x/z", 12, "abc")
    assert updater.release_from_api({**data, "prerelease": True}) is None
    assert updater.release_from_api({**data, "draft": True}) is None
    assert updater.release_from_api({"tag_name": "nightly"}) is None
    no_asset = updater.release_from_api({"tag_name": "v2"})
    assert no_asset.asset_url is None and no_asset.page_url == updater.RELEASES_PAGE


def _fake_zip(path, broken=False):
    with zipfile.ZipFile(path, "w") as z:
        if not broken:
            z.writestr("PhosphorXP/PhosphorXP.exe", b"exe")
            z.writestr("PhosphorXP/_internal/base_library.zip", b"lib")
        z.writestr("PhosphorXP/READ ME FIRST.txt", b"hi")
    return path


def _release(zip_path, **kw):
    data = zip_path.read_bytes()
    return Release("v9.9", (9, 9), "https://x", asset_url=zip_path.as_uri(), size=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), **kw)


def test_download_verify_extract(tmp_path):
    src = _fake_zip(tmp_path / "src.zip")
    seen = []
    z = updater.download(_release(src), tmp_path / "stage" / "dl.zip", progress=seen.append)
    assert seen and seen[-1] == 1.0
    root = updater.extract(z, tmp_path / "stage" / "new")
    assert root.name == "PhosphorXP"
    assert (root / "PhosphorXP.exe").read_bytes() == b"exe"
    assert (root / "_internal").is_dir()


def test_download_rejects_bad_hash_size_and_zip(tmp_path):
    src = _fake_zip(tmp_path / "src.zip")
    rel = _release(src)
    rel.sha256 = "0" * 64
    with pytest.raises(UpdateError, match="CORRUPTED"):
        updater.download(rel, tmp_path / "a.zip")
    rel = _release(src)
    rel.size += 5
    with pytest.raises(UpdateError, match="INCOMPLETE"):
        updater.download(rel, tmp_path / "b.zip")
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip at all")
    with pytest.raises(UpdateError, match="CORRUPTED"):
        updater.download(_release(junk), tmp_path / "c.zip")
    with pytest.raises(UpdateError, match="HAS NO"):
        updater.download(Release("v1", (1,), "x"), tmp_path / "d.zip")


def test_download_without_digest_still_checks_zip(tmp_path):
    rel = _release(_fake_zip(tmp_path / "src.zip"))
    rel.sha256 = None
    assert updater.download(rel, tmp_path / "ok.zip").exists()


def test_extract_rejects_zip_without_app(tmp_path):
    with pytest.raises(UpdateError, match="LOOKS WRONG"):
        updater.extract(_fake_zip(tmp_path / "x.zip", broken=True), tmp_path / "out")


def test_location_problem(tmp_path, monkeypatch):
    app = tmp_path / "Documents" / "PhosphorXP"
    (app / "_internal").mkdir(parents=True)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "Temp"))
    assert updater.location_problem(app) is None
    # not writable (e.g. Program Files)
    monkeypatch.setattr(updater, "_can_write", lambda d: False)
    msg = updater.location_problem(app)
    assert "Documents" in msg and "administrators" in msg and str(app) in msg
    assert "%APPDATA%\\PhosphorXP" in msg
    # only _internal unwritable still counts
    monkeypatch.setattr(updater, "_can_write", lambda d: d.name != "_internal")
    assert "administrators" in updater.location_problem(app)
    monkeypatch.setattr(updater, "_can_write", lambda d: True)
    # running from inside the zip (Windows extracts it to the temp folder)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "Documents"))
    assert "inside the zip" in updater.location_problem(app)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "Temp"))
    weird = tmp_path / "100%" / "PhosphorXP"
    weird.mkdir(parents=True)
    assert "'%'" in updater.location_problem(weird)


def test_can_write_really_writes(tmp_path):
    assert updater._can_write(tmp_path)
    assert list(tmp_path.iterdir()) == []                  # probe cleaned up
    assert not updater._can_write(tmp_path / "missing")


def test_helper_script(tmp_path):
    app = tmp_path / "My Apps" / "PhosphorXP"
    bat = updater.write_helper(tmp_path / "stage", app, tmp_path / "new", "v9.9", 4242)
    raw = bat.read_bytes()
    text = raw.decode()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")   # CRLF only
    assert 'set "PIDNUM=4242"' in text and f'set "APP={app}"' in text
    assert 'find " %PIDNUM% "' in text and "ping -n 2 127.0.0.1" in text
    assert "timeout " not in text                                        # needs a console
    # back up top-level files + mirror _internal; install mirrors only _internal
    assert 'robocopy "%APP%" "%BAK%" /R:2' in text
    assert 'robocopy "%APP%\\_internal" "%BAK%\\_internal" /MIR' in text
    assert 'robocopy "%NEW%\\_internal" "%APP%\\_internal" /MIR' in text
    assert 'robocopy "%NEW%" "%APP%" /R:5' in text and '"%NEW%" "%APP%" /MIR' not in text
    assert text.count("if errorlevel 8 goto") == 4 and ":restore" in text
    assert "echo ok>" in text and "echo fail>" in text and ">> \"%LOG%\"" in text
    assert 'start "" "%APP%\\PhosphorXP.exe"' in text
    assert not (tmp_path / "stage" / "result.txt").exists()
    for bad in (tmp_path / "100%", tmp_path / 'a"b'):
        with pytest.raises(UpdateError):
            updater.write_helper(tmp_path / "stage", bad, tmp_path / "new", "v1", 1)


def test_take_result(tmp_path):
    stage = tmp_path / "update"
    stage.mkdir()
    (stage / "result.txt").write_text("ok")
    (stage / "backup").mkdir()
    assert updater.take_result(stage) == "ok"
    assert not stage.exists()                               # all cleaned up after success
    stage.mkdir()
    (stage / "result.txt").write_text("fail\r\n")
    (stage / "update.log").write_text("log")
    (stage / "backup").mkdir()
    (stage / "new").mkdir()
    assert updater.take_result(stage) == "fail"
    assert [p.name for p in stage.iterdir()] == ["update.log"]   # keep only the log
    assert updater.take_result(stage) is None                    # reported once only
    assert (stage / "update.log").exists()                       # log stays until next update
    (stage / "PhosphorXP-windows.zip").write_bytes(b"half")      # aborted download leftovers
    assert updater.take_result(stage) is None
    assert [p.name for p in stage.iterdir()] == ["update.log"]
    assert updater.take_result(tmp_path / "never-existed") is None


def test_staging_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert updater.staging_dir() == tmp_path / "PhosphorXP" / "update"
