#!/usr/bin/env python3
"""Выгрузить спаны из Arize AX REST v2 (регион данных CA).

Нужны ARIZE_SPACE_ID и ключ: ARIZE_REST_API_KEY или ARIZE_API_KEY.
Секреты только из окружения, в репозиторий не попадают.

Пример:

    python scripts/arize_spans.py --hours 2 --filter "status_code = 'ERROR'"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=2, help="окно времени, часы назад")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--filter",
        default="",
        help="фильтр Arize, например status_code = 'ERROR'",
    )
    parser.add_argument(
        "--project",
        default="",
        help="имя или id проекта; по умолчанию ARIZE_PROJECT_NAME",
    )
    args = parser.parse_args()

    space_id = _env("ARIZE_SPACE_ID")
    api_key = _env("ARIZE_REST_API_KEY", "ARIZE_API_KEY")
    project = args.project or _env("ARIZE_PROJECT_NAME", default="aichat")
    base = _env("ARIZE_REST_BASE_URL", default="https://api.ca-central-1a.arize.com").rstrip("/")
    if not space_id or not api_key:
        print(
            "Нужны ARIZE_SPACE_ID и ARIZE_REST_API_KEY (или ARIZE_API_KEY).",
            file=sys.stderr,
        )
        return 2

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)
    body: dict[str, object] = {
        "space_id": space_id,
        "project_id": project,
        "start_time": start.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "end_time": end.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    if args.filter.strip():
        body["filter"] = args.filter.strip()

    request = Request(
        f"{base}/v2/spans?limit={args.limit}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Arize REST {exc.code}: {detail}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Не удалось связаться с Arize: {exc.reason}", file=sys.stderr)
        return 1

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
