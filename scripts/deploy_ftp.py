#!/usr/bin/env python3
"""Upload site files to production via FTP. Credentials come from .env."""

from __future__ import annotations

import fnmatch
import sys
from ftplib import FTP, error_perm
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
UPLOADIGNORE_PATH = ROOT / ".uploadignore"


def load_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        sys.exit(f"Missing {path}. Copy .env.example to .env and fill FTP_* values.")
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
    print(f"  ↑ {remote}")


def main() -> None:
    env = load_env(ENV_PATH)
    host = env.get("FTP_HOST")
    user = env.get("FTP_USER")
    password = env.get("FTP_PASS")
    port = int(env.get("FTP_PORT", "21"))
    remote_root = (env.get("FTP_REMOTE_DIR") or ".").rstrip("/") or "."

    if not host or not user or password is None or password == "":
        sys.exit("FTP_HOST, FTP_USER and FTP_PASS must be set in .env")

    rules = load_uploadignore(UPLOADIGNORE_PATH)
    files = iter_upload_files(ROOT, rules)
    if not files:
        sys.exit("Nothing to upload (all files ignored?). Check .uploadignore")

    print(f"Connecting via FTP to {host}:{port} as {user} …")
    ftp = FTP()
    ftp.connect(host, port, timeout=30)
    ftp.login(user, password)
    ftp.set_pasv(True)

    if remote_root not in ("", "."):
        ensure_dir(ftp, remote_root)
        ftp.cwd(remote_root)
        print(f"Remote dir: {remote_root}")
    else:
        print(f"Remote dir: {ftp.pwd()}")

    print(f"Uploading {len(files)} file(s)…")
    for local in files:
        remote = local.relative_to(ROOT).as_posix()
        upload_file(ftp, local, remote)

    ftp.quit()
    print("Done.")


if __name__ == "__main__":
    main()
