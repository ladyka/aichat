#!/usr/bin/env python3
"""Upload site files to production via FTP. Credentials come from .env."""

from __future__ import annotations

import fnmatch
import socket
import sys
from ftplib import FTP, error_perm, error_proto, error_temp
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
UPLOADIGNORE_PATH = ROOT / ".uploadignore"
PROBE_NAME = ".aichat_upload_probe"


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


def main() -> None:
    env = load_env(ENV_PATH)
    host, user, password, port, remote_root = require_ftp_config(env)

    rules = load_uploadignore(UPLOADIGNORE_PATH)
    files = iter_upload_files(ROOT, rules)
    if not files:
        die("Nothing to upload (all files ignored?). Check .uploadignore")

    ftp = connect_ftp(host, port, user, password)
    try:
        enter_remote_dir(ftp, remote_root)
        verify_write_access(ftp)

        print(f"Uploading {len(files)} file(s)…")
        for local in files:
            remote = local.relative_to(ROOT).as_posix()
            try:
                upload_file(ftp, local, remote)
            except error_perm as exc:
                die(f"Upload denied for {remote}: {exc}")
            except (error_temp, error_proto, OSError) as exc:
                die(f"Failed to upload {remote}: {exc}")

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
