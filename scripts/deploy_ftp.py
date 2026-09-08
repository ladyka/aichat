#!/usr/bin/env python3
"""Upload site files to production via FTP. Credentials come from .env."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import socket
import subprocess
import sys
from ftplib import FTP, error_perm, error_proto, error_temp
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
UPLOADIGNORE_PATH = ROOT / ".uploadignore"
VERSION_FILE = ".version"
PROBE_NAME = ".aichat_upload_probe"
MANIFEST_NAME = ".aichat_manifest.json"
MANIFEST_TMP = ".aichat_manifest.json.tmp"
_HASH_CHUNK = 1024 * 1024


def get_git_commit_hash() -> str:
    """Get the current git commit hash."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        die("Failed to get git commit hash. Is this a git repository?")


def check_git_status() -> None:
    """Ensure no uncommitted changes exist."""
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        if status:
            die(f"You have uncommitted changes. Please commit or stash them before deploying:\n{status}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        die("Failed to check git status. Is this a git repository?")


def sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file, read in chunks to limit memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        die(f"Missing {path}. Copy .env.example to .env and fill FTP_* values.")
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'").strip('"')
    return env


def load_uploadignore(path: Path) -> list[tuple[bool, str]]:
    """Return list of (negate, pattern). Missing file → empty rules."""
    if not path.is_file():
        return []
    rules: list[tuple[bool, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:].strip() if negate else line
        if pattern:
            rules.append((negate, pattern))
    return rules


def path_matches(rel: str, pattern: str) -> bool:
    """Match relative path against a gitignore-like pattern."""
    pat = pattern.rstrip("/")
    name = Path(rel).name

    if pattern.endswith("/"):
        return (
            rel == pat
            or rel.startswith(pat + "/")
            or fnmatch.fnmatch(rel, pat)
            or fnmatch.fnmatch(name, pat)
            or any(fnmatch.fnmatch(part, pat) for part in rel.split("/"))
        )

    if "/" in pat.strip("/"):
        return fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat.rstrip("/"))

    return fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat)


def is_ignored(rel: str, rules: list[tuple[bool, str]]) -> bool:
    ignored = False
    for negate, pattern in rules:
        if path_matches(rel, pattern):
            ignored = not negate
    return ignored


def iter_upload_files(root: Path, rules: list[tuple[bool, str]]) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        if is_ignored(rel, rules):
            continue
        files.append(path)
    return files


def read_remote_manifest(ftp: FTP, remote: str) -> dict[str, str] | None:
    """Download the last-deploy manifest, if present.

    Returns a {rel_path: sha256} dict, or None when the file is missing or
    unreadable/corrupt (caller then falls back to a full upload).
    """
    chunks: list[bytes] = []

    def collect(data: bytes) -> None:
        chunks.append(data)

    try:
        ftp.retrbinary(f"RETR {remote}", collect)
    except (error_perm, error_temp, error_proto, OSError):
        return None
    try:
        data = json.loads(b"".join(chunks))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    manifest: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str):
            manifest[key] = value
    return manifest


def write_remote_manifest(ftp: FTP, remote: str, manifest: dict[str, str]) -> None:
    """Upload the manifest atomically: STOR to .tmp, then RENAME."""
    tmp = MANIFEST_TMP
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    ftp.storbinary(f"STOR {tmp}", BytesIO(payload))
    try:
        ftp.rename(tmp, remote)
    except error_perm:
        # Some hosts won't RENAME over an existing file; STOR directly instead.
        ftp.storbinary(f"STOR {remote}", BytesIO(payload))
        try:
            ftp.delete(tmp)
        except (error_perm, error_temp, error_proto, OSError):
            pass


def plan_sync(
    local_hashes: dict[str, str],
    manifest: dict[str, str] | None,
    rules: list[tuple[bool, str]],
) -> tuple[list[str], list[str], set[str]]:
    """Decide what to upload, delete and report as new.

    Returns (to_upload, to_delete, new_files). Files currently ignored by
    .uploadignore are never deleted, even if they vanished locally.
    """
    remote = manifest or {}
    to_upload: list[str] = []
    new_files: set[str] = set()
    for rel, digest in local_hashes.items():
        if remote.get(rel) != digest:
            to_upload.append(rel)
            if rel not in remote:
                new_files.add(rel)

    to_delete = [rel for rel in remote if rel not in local_hashes and not is_ignored(rel, rules)]
    return to_upload, to_delete, new_files


def remove_empty_dirs(ftp: FTP, remote: str) -> None:
    """Best-effort removal of empty parent directories for a deleted file."""
    parent = str(Path(remote).parent).replace("\\", "/")
    parts = [p for p in parent.split("/") if p and p != "."]
    while parts:
        path = "/".join(parts)
        try:
            ftp.rmd(path)
        except (error_perm, error_temp, error_proto, OSError):
            break
        parts.pop()


def ensure_dir(ftp: FTP, remote_dir: str) -> None:
    parts = [p for p in remote_dir.replace("\\", "/").split("/") if p and p != "."]
    path = ""
    for part in parts:
        path = f"{path}/{part}" if path else part
        try:
            ftp.mkd(path)
        except error_perm:
            pass


def upload_file(ftp: FTP, local: Path, remote: str) -> None:
    parent = str(Path(remote).parent).replace("\\", "/")
    if parent not in ("", "."):
        ensure_dir(ftp, parent)
    with local.open("rb") as fh:
        ftp.storbinary(f"STOR {remote}", fh)


def require_ftp_config(env: dict[str, str]) -> tuple[str, str, str, int, str]:
    missing = [key for key in ("FTP_HOST", "FTP_USER", "FTP_PASS") if not env.get(key)]
    if missing:
        die(
            "FTP upload config incomplete in .env: missing "
            + ", ".join(missing)
            + ". Set FTP_HOST, FTP_USER, FTP_PASS (and optional FTP_PORT, FTP_REMOTE_DIR)."
        )

    host = env["FTP_HOST"]
    user = env["FTP_USER"]
    password = env["FTP_PASS"]
    try:
        port = int(env.get("FTP_PORT", "21") or "21")
    except ValueError:
        die(f"FTP_PORT must be a number, got: {env.get('FTP_PORT')!r}")
    remote_root = (env.get("FTP_REMOTE_DIR") or ".").rstrip("/") or "."
    return host, user, password, port, remote_root


def connect_ftp(host: str, port: int, user: str, password: str) -> FTP:
    print(f"Checking FTP access to {host}:{port} as {user} …")
    ftp = FTP()
    try:
        ftp.connect(host, port, timeout=30)
    except socket.gaierror as exc:
        die(f"Cannot resolve FTP_HOST={host!r}: {exc}")
    except TimeoutError:
        die(f"Connection to {host}:{port} timed out. Check host/port/firewall.")
    except ConnectionRefusedError:
        die(f"Connection to {host}:{port} refused. Is FTP available on this host/port?")
    except OSError as exc:
        die(f"Cannot connect to {host}:{port}: {exc}")

    try:
        ftp.login(user, password)
    except error_perm as exc:
        msg = str(exc)
        if "530" in msg or "login" in msg.lower() or "auth" in msg.lower():
            die(f"FTP login failed for user {user!r}: {exc}. " "Check FTP_USER / FTP_PASS in .env.")
        die(f"FTP login rejected: {exc}")
    except (error_temp, error_proto, OSError) as exc:
        die(f"FTP login error: {exc}")

    try:
        ftp.set_pasv(True)
    except (error_perm, error_temp, error_proto, OSError):
        ftp.set_pasv(False)
    else:
        # This host often blocks passive data connections.
        try:
            ftp.nlst()
        except (error_perm, error_temp, error_proto, OSError):
            ftp.set_pasv(False)

    return ftp


def enter_remote_dir(ftp: FTP, remote_root: str) -> None:
    if remote_root in ("", "."):
        print(f"Remote dir: {ftp.pwd()}")
        return

    try:
        ensure_dir(ftp, remote_root)
        ftp.cwd(remote_root)
    except error_perm as exc:
        die(
            f"Cannot enter FTP_REMOTE_DIR={remote_root!r}: {exc}. "
            "Check the path exists and the user can access it."
        )
    except (error_temp, error_proto, OSError) as exc:
        die(f"Failed to change to FTP_REMOTE_DIR={remote_root!r}: {exc}")
    print(f"Remote dir: {remote_root} ({ftp.pwd()})")


def verify_write_access(ftp: FTP) -> None:
    pwd = ftp.pwd()
    try:
        ftp.storbinary(f"STOR {PROBE_NAME}", BytesIO(b"aichat upload probe\n"))
    except error_perm as exc:
        die(
            f"No upload (write) permission in {pwd!r}: {exc}. "
            "FTP login works, but STOR is denied — check account permissions / remote dir."
        )
    except (error_temp, error_proto, OSError) as exc:
        die(f"Upload probe failed in {pwd!r}: {exc}")

    try:
        ftp.delete(PROBE_NAME)
    except error_perm as exc:
        print(
            f"warning: upload works, but cannot delete probe {PROBE_NAME!r} in {pwd!r}: {exc}",
            file=sys.stderr,
        )

    print("FTP access OK (connect, login, write).")


def delete_file(ftp: FTP, remote: str) -> None:
    ftp.delete(remote)
    print(f"  ↓ {remote}")
    remove_empty_dirs(ftp, remote)


def main() -> None:
    force = "--force" in sys.argv

    check_git_status()
    env = load_env(ENV_PATH)
    host, user, password, port, remote_root = require_ftp_config(env)

    rules = load_uploadignore(UPLOADIGNORE_PATH)
    files = iter_upload_files(ROOT, rules)
    if not files:
        die("Nothing to upload (all files ignored?). Check .uploadignore")

    local_hashes = {local.relative_to(ROOT).as_posix(): sha256_file(local) for local in files}

    ftp = connect_ftp(host, port, user, password)
    try:
        enter_remote_dir(ftp, remote_root)
        verify_write_access(ftp)

        manifest = None if force else read_remote_manifest(ftp, MANIFEST_NAME)
        to_upload, to_delete, new_files = plan_sync(local_hashes, manifest, rules)
        if force:
            new_files = set()

        if to_delete:
            print(f"Removing {len(to_delete)} stale file(s)…")
            for remote in sorted(to_delete):
                try:
                    delete_file(ftp, remote)
                except (error_perm, error_temp, error_proto, OSError) as exc:
                    die(f"Failed to delete {remote}: {exc}")

        total = len(to_upload)
        skipped = len(files) - total
        print(
            f"Uploading {total} of {len(files)} file(s)"
            + (f" ({len(new_files)} new, {skipped} up to date)…" if not force else "…")
        )
        for rel in to_upload:
            local = ROOT / rel
            remote = local.relative_to(ROOT).as_posix()
            marker = "NEW " if rel in new_files else "   "
            try:
                upload_file(ftp, local, remote)
                print(f"  {marker}↑ {remote}")
            except error_perm as exc:
                die(f"Upload denied for {remote}: {exc}")
            except (error_temp, error_proto, OSError) as exc:
                die(f"Failed to upload {remote}: {exc}")

        try:
            write_remote_manifest(ftp, MANIFEST_NAME, local_hashes)
        except (error_perm, error_temp, error_proto, OSError) as exc:
            print(
                f"warning: files uploaded, but could not write {MANIFEST_NAME}: {exc}",
                file=sys.stderr,
            )

        try:
            commit_hash = get_git_commit_hash()
            ftp.storbinary(f"STOR {VERSION_FILE}", BytesIO(commit_hash.encode("utf-8")))
            print(f"Version {commit_hash[:7]} uploaded to {VERSION_FILE}")
        except (error_perm, error_temp, error_proto, OSError) as exc:
            print(
                f"warning: could not upload {VERSION_FILE}: {exc}",
                file=sys.stderr,
            )

        try:
            ftp.quit()
        except (error_perm, error_temp, error_proto, OSError):
            ftp.close()
    except SystemExit:
        try:
            ftp.close()
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            ftp.close()
        except Exception:
            pass
        die(f"Unexpected FTP error: {exc}")

    print("Done.")


if __name__ == "__main__":
    main()
