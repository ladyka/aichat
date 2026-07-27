"""Entrypoint for local and hosting (port or unix socket)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# Ensure INDEX_PATH-era deploys still find the app package
os.chdir(ROOT)


def main() -> None:
    import uvicorn

    socket = os.environ.get("SOCKET")
    if socket:
        if os.path.exists(socket):
            os.unlink(socket)
        # Uvicorn unix socket: path via uds
        config = uvicorn.Config(
            "app.main:app",
            uds=socket,
            log_level="info",
        )
        server = uvicorn.Server(config)
        # chmod after bind
        import threading
        import time

        def _chmod():
            for _ in range(50):
                if os.path.exists(socket):
                    os.chmod(socket, 0o660)
                    return
                time.sleep(0.05)

        threading.Thread(target=_chmod, daemon=True).start()
        print(f"Listening {socket}")
        server.run()
        return

    host = os.environ.get("INSTANCE_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Listening http://{host}:{port}/")
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
elif os.environ.get("SOCKET"):
    # ISPmanager starts the app with SOCKET set and may not use __main__.
    main()
