"""Загрузка дополнительных провайдеров из ``ai-providers.yaml``.

Встроенные OpenRouter и ol сюда не входят. Публичные id моделей:
``<provider.id>/<upstream-model>``.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import ROOT, get_settings

logger = logging.getLogger("aichat.ai_providers")

# Id, занятые встроенными провайдерами и служебными именами.
RESERVED_PROVIDER_IDS = frozenset({"openrouter", "e7", "ol", "aichat", "default"})

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class YamlProvider:
    id: str
    base_url: str
    api_key: str = ""
    models_url: str | None = None
    models: tuple[str, ...] = field(default_factory=tuple)
    timeout: float = 120.0
    enabled: bool = True

    @property
    def prefix(self) -> str:
        return f"{self.id}/"

    @property
    def use_manual_models(self) -> bool:
        return bool(self.models)

    def resolve_models_url(self) -> str:
        if self.models_url:
            return self.models_url.rstrip("/")
        return f"{self.base_url.rstrip('/')}/models"


def _expand_env(value: str) -> str:
    """Подставить ``${VAR}`` из окружения; неизвестная переменная → пустая строка."""

    def repl(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    return _ENV_PATTERN.sub(repl, value)


def _providers_path() -> Path:
    settings = get_settings()
    raw = (getattr(settings, "ai_providers_file", None) or "ai-providers.yaml").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _parse_models(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("providers[].models должен быть списком")
    items: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            model_id = entry.strip()
        elif isinstance(entry, dict):
            model_id = str(entry.get("id") or entry.get("name") or "").strip()
        else:
            continue
        if model_id:
            items.append(model_id)
    return tuple(items)


def _parse_provider(raw: dict[str, Any]) -> YamlProvider | None:
    provider_id = str(raw.get("id") or "").strip().lower()
    if not provider_id:
        logger.warning("ai-providers.yaml: пропущен провайдер без id")
        return None
    if provider_id in RESERVED_PROVIDER_IDS:
        logger.warning(
            "ai-providers.yaml: id %r зарезервирован, провайдер пропущен",
            provider_id,
        )
        return None
    if "/" in provider_id or " " in provider_id:
        logger.warning(
            "ai-providers.yaml: недопустимый id %r (без пробелов и /)",
            provider_id,
        )
        return None

    enabled = raw.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None

    base_url = _expand_env(str(raw.get("base_url") or "")).strip().rstrip("/")
    if not base_url:
        logger.warning("ai-providers.yaml: у %r пустой base_url, пропущен", provider_id)
        return None

    api_key = _expand_env(str(raw.get("api_key") or "")).strip()

    models_url_raw = raw.get("models_url", None)
    models_url: str | None
    if models_url_raw is None or models_url_raw is False:
        models_url = None
    else:
        models_url = _expand_env(str(models_url_raw)).strip() or None

    try:
        timeout = float(raw.get("timeout", 120) or 120)
    except (TypeError, ValueError):
        timeout = 120.0

    models = _parse_models(raw.get("models"))
    return YamlProvider(
        id=provider_id,
        base_url=base_url,
        api_key=api_key,
        models_url=models_url,
        models=models,
        timeout=timeout,
        enabled=True,
    )


def load_yaml_providers(path: Path | None = None) -> tuple[YamlProvider, ...]:
    """Прочитать файл. Отсутствующий или пустой файл → пустой кортеж."""
    file_path = path or _providers_path()
    if not file_path.is_file():
        return ()
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Не удалось прочитать %s: %s", file_path, exc)
        return ()
    if not text.strip():
        return ()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning("Ошибка разбора %s: %s", file_path, exc)
        return ()
    if not data:
        return ()
    if not isinstance(data, dict):
        logger.warning("%s: корень должен быть mapping с ключом providers", file_path)
        return ()
    raw_list = data.get("providers")
    if raw_list is None:
        return ()
    if not isinstance(raw_list, list):
        logger.warning("%s: providers должен быть списком", file_path)
        return ()

    seen: set[str] = set()
    result: list[YamlProvider] = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        try:
            provider = _parse_provider(entry)
        except ValueError as exc:
            logger.warning("ai-providers.yaml: %s", exc)
            continue
        if provider is None:
            continue
        if provider.id in seen:
            logger.warning("ai-providers.yaml: дубликат id %r, пропущен", provider.id)
            continue
        seen.add(provider.id)
        result.append(provider)
    return tuple(result)


@lru_cache
def get_yaml_providers() -> tuple[YamlProvider, ...]:
    return load_yaml_providers()


def invalidate_yaml_providers_cache() -> None:
    get_yaml_providers.cache_clear()


def get_yaml_provider(provider_id: str) -> YamlProvider | None:
    pid = (provider_id or "").strip().lower()
    for provider in get_yaml_providers():
        if provider.id == pid:
            return provider
    return None


def match_yaml_provider(public_id: str) -> tuple[YamlProvider, str] | None:
    """Если public_id вида ``id/model`` принадлежит yaml-провайдеру — (provider, upstream)."""
    model = (public_id or "").strip()
    if "/" not in model:
        return None
    prefix, _, rest = model.partition("/")
    provider = get_yaml_provider(prefix)
    if provider is None or not rest.strip():
        return None
    return provider, rest.strip()


def to_yaml_public_id(provider_id: str, upstream_id: str) -> str:
    upstream = (upstream_id or "").strip().lstrip("/")
    if not upstream:
        return ""
    prefix = f"{provider_id}/"
    if upstream.startswith(prefix):
        return upstream
    return f"{prefix}{upstream}"


def to_yaml_upstream_id(provider_id: str, public_id: str) -> str:
    model = (public_id or "").strip()
    prefix = f"{provider_id}/"
    if model.startswith(prefix):
        return model[len(prefix) :]
    return model
