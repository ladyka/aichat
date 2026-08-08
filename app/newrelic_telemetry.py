from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger("aichat.newrelic")

_initialized = False


def setup_newrelic() -> bool:
    """Initialize the New Relic Python agent (APM + log forwarding) when configured.

    The agent reads its NEW_RELIC_* settings from the environment at import time, so the
    import happens lazily here — after app.config has already loaded the .env file.

    Log forwarding is enabled by default in the agent (application_logging.forwarding),
    so Python ``logging`` records are sent to New Relic Logs automatically.
    """
    global _initialized
    if _initialized:
        return True

    settings = get_settings()
    if not settings.new_relic_enabled:
        logger.info("New Relic disabled (set NEW_RELIC_LICENSE_KEY to enable)")
        return False

    import newrelic.agent

    try:
        newrelic.agent.initialize()
        # Non-blocking: registers the app in the background (startup_timeout=0.0),
        # so startup is not delayed when the collector is slow/unreachable.
        newrelic.agent.register_application()
    except Exception:
        logger.exception("Failed to initialize New Relic agent")
        return False

    _initialized = True
    logger.info("New Relic enabled app=%s", settings.new_relic_app_name)
    return True


def wrap_asgi(application: Any) -> Any:
    """Wrap the ASGI app with the New Relic agent when monitoring is enabled.

    Returns an async callable (ASGI v3 single-callable signature) instead of the
    raw New Relic FunctionWrapper: the wrapper's ``__call__`` is not a coroutine,
    which makes Starlette's ``TestClient`` misdetect the app as an ASGI2 app and
    break with ``FastAPI.__call__() missing 2 required positional arguments``.
    """
    if not setup_newrelic():
        return application
    import newrelic.agent

    nr_app = newrelic.agent.ASGIApplicationWrapper(application)

    async def _app(scope, receive, send):
        return await nr_app(scope, receive, send)

    return _app


def shutdown_newrelic() -> None:
    global _initialized
    if not _initialized:
        return
    try:
        import newrelic.agent

        newrelic.agent.shutdown_agent(timeout=2.5)
    except Exception:
        logger.exception("Failed to shut down New Relic agent")
    _initialized = False
