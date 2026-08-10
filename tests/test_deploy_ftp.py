from __future__ import annotations

import json

from scripts.deploy_ftp import (
    plan_sync,
    read_remote_manifest,
    sha256_file,
    write_remote_manifest,
)


class FakeFTP:
    def __init__(self, remote: dict[str, bytes] | None = None) -> None:
        self.files = dict(remote or {})
        self.commands: list[tuple[str, object]] = []

    def retrbinary(self, cmd: str, callback) -> None:
        self.commands.append((cmd, None))
        remote = cmd.split(" ", 1)[1]
        if remote not in self.files:
            from ftplib import error_perm

            raise error_perm("550 File not found")
        callback(self.files[remote])

    def storbinary(self, cmd: str, fh) -> None:
        target = cmd.split(" ", 1)[1]
        self.commands.append((cmd, fh))
        data = fh.read() if hasattr(fh, "read") else fh
        self.files[target] = data if isinstance(data, bytes) else data

    def rename(self, src: str, dst: str) -> None:
        self.commands.append(("rename", (src, dst)))
        if src in self.files:
            self.files[dst] = self.files.pop(src)

    def delete(self, remote: str) -> None:
        self.commands.append(("delete", remote))
        self.files.pop(remote, None)


def test_sha256_file(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("hello\n", encoding="utf-8")
    assert sha256_file(path) == "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"


def test_read_remote_manifest_missing(tmp_path):
    ftp = FakeFTP()
    assert read_remote_manifest(ftp, ".aichat_manifest.json") is None


def test_read_remote_manifest_valid():
    manifest = {"index.html": "abc", "static/app.js": "def"}
    ftp = FakeFTP({".aichat_manifest.json": json.dumps(manifest).encode("utf-8")})
    assert read_remote_manifest(ftp, ".aichat_manifest.json") == manifest


def test_read_remote_manifest_corrupt():
    ftp = FakeFTP({".aichat_manifest.json": b"not json {{{"})
    assert read_remote_manifest(ftp, ".aichat_manifest.json") is None


def test_read_remote_manifest_not_dict():
    ftp = FakeFTP({".aichat_manifest.json": b'["list"]'})
    assert read_remote_manifest(ftp, ".aichat_manifest.json") is None


def test_write_remote_manifest_renames_over_existing():
    ftp = FakeFTP({".aichat_manifest.json": b"old"})
    manifest = {"a": "1"}
    write_remote_manifest(ftp, ".aichat_manifest.json", manifest)
    assert ".aichat_manifest.json" in ftp.files
    assert ".aichat_manifest.json.tmp" not in ftp.files
    assert json.loads(ftp.files[".aichat_manifest.json"]) == manifest


def test_plan_sync_new_changed_unchanged():
    local = {"a": "same", "b": "new-hash", "c": "zzz"}
    manifest = {"a": "same", "b": "old-hash"}
    to_upload, to_delete, new_files = plan_sync(local, manifest, rules=[])
    assert to_upload == ["b", "c"]
    assert new_files == {"c"}
    assert to_delete == []


def test_plan_sync_delete_missing_local():
    local = {"a": "1"}
    manifest = {"a": "1", "b": "2"}
    to_upload, to_delete, new_files = plan_sync(local, manifest, rules=[])
    assert to_delete == ["b"]


def test_plan_sync_delete_skips_ignored():
    local = {"a": "1"}
    manifest = {"a": "1", "b": "2"}
    rules = [(False, "b*")]
    to_upload, to_delete, new_files = plan_sync(local, manifest, rules=rules)
    assert to_delete == []


def test_plan_sync_no_manifest_uploads_all():
    local = {"a": "1", "b": "2"}
    to_upload, to_delete, new_files = plan_sync(local, None, rules=[])
    assert to_upload == ["a", "b"]
    assert new_files == {"a", "b"}
    assert to_delete == []
