"""Неофициальный клиент публичных HTML-страниц pravo.by.

Официального API нет: поиск Национального реестра — GET-форма на
/natsionalnyy-reestr/poisk-v-reestre/, карточка акта — /document/?guid=&p0=.
Контракт вёрстки может измениться. Это не эталонная сводная редакция
(кроме отдельных HTML-публикаций на портале).
"""

from __future__ import annotations

import html as html_lib
import re
import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any

import httpx2 as httpx

PRAVO_ORIGIN = "https://pravo.by"
SEARCH_PATH = "/natsionalnyy-reestr/poisk-v-reestre/"
CODES_PATH = "/pravovaya-informatsiya/normativnye-dokumenty/kodeksy-respubliki-belarus/"
ALLOWED_HOSTS = frozenset({"pravo.by", "www.pravo.by"})
CODES_TTL_SEC = 3600
MAX_QUERY_LEN = 200
MAX_PAGE = 50
MAX_TEXT_CHARS = 12_000
SNIPPET_RADIUS = 280
MAX_SNIPPETS = 4
TIMEOUT_SEC = 25.0

_HEADERS = {
    "User-Agent": "aichat-pravo/1.0 (+https://pravo.by)",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

_REGISTRY_RE = re.compile(r"^\d[\d.\-]*/\d+$")
_DOC_BLOCK_RE = re.compile(
    r'<div class="doc">\s*<dl>\s*<dt>(.*?)</dt>\s*<dd>(.*?)</dd>\s*</dl>\s*</div>',
    re.I | re.S,
)
_HREF_RE = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_ITALIC_RE = re.compile(r"<i>(.*?)</i>", re.I | re.S)
_TOTAL_RE = re.compile(r"Количество найденных документов:\s*(\d+)", re.I)
_PAGE_RE = re.compile(r'data-type="p[01]"\s+data-page="(\d+)', re.I)
_FIELD_RE = re.compile(r"<b>([^<]+)</b>\s*<div>(.*?)</div>", re.I | re.S)
_PDF_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*class="link-pdf"', re.I)
_CARD_RE = re.compile(r'<div class="reestrmap">(.*)</div>\s*<style', re.I | re.S)
_CODE_LINK_RE = re.compile(
    r'<div class="s-block-link-innertext-wrapper">\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_GENERIC_CODE_TOKENS = frozenset(
    {"кодекс", "кодекса", "кодексу", "закон", "закона", "указ", "указа", "акт", "акта"}
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head"})

_codes_cache: tuple[float, list[dict[str, str]]] | None = None

HINT = (
    "Источник — Национальный правовой портал pravo.by (официальное опубликование). "
    "Это не юридическая консультация. Записи реестра — реквизиты конкретного акта "
    "(часто исходная редакция или изменения), а не гарантированно сводный действующий текст. "
    "В ответе пользователю начни с «По данным pravo.by:» и дай ссылки на найденные документы."
)


def clear_caches() -> None:
    global _codes_cache
    _codes_cache = None


def _strip_tags(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _absolute(href: str) -> str:
    href = html_lib.unescape(href).strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urllib.parse.urljoin(PRAVO_ORIGIN + "/", href)


def _looks_like_registry(value: str) -> bool:
    return bool(_REGISTRY_RE.match(value.strip().replace(" ", "")))


def normalize_pravo_url(url: str) -> str | None:
    """Разрешить только http(s) на pravo.by / www.pravo.by."""
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        return None
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"https://{host}{path}{query}{fragment}"


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in {"p", "div", "br", "tr", "li", "h1", "h2", "h3"} and self._skip == 0:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0 and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", "".join(self._chunks))).strip()


def extract_visible_text(html: str) -> str:
    parser = _VisibleText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return _strip_tags(html)
    return parser.text()


def parse_search_html(html: str) -> tuple[list[dict[str, str]], int | None, int]:
    """Список актов, общее число (если есть) и число страниц пагинации."""
    documents: list[dict[str, str]] = []
    for block in _DOC_BLOCK_RE.finditer(html):
        dt_html, dd_html = block.group(1), block.group(2)
        dt_text = _strip_tags(dt_html)
        registry = ""
        registered_at = ""
        dt_match = re.match(r"(\S+)\s*(?:\((\d{2}\.\d{2}\.\d{4})\))?", dt_text)
        if dt_match:
            registry = dt_match.group(1)
            registered_at = dt_match.group(2) or ""
        href_match = _HREF_RE.search(dd_html)
        title = _strip_tags(href_match.group(2)) if href_match else _strip_tags(dd_html)
        url = _absolute(href_match.group(1)) if href_match else ""
        italic = _ITALIC_RE.search(dd_html)
        kind = _strip_tags(italic.group(1)) if italic else ""
        documents.append(
            {
                "registry_number": registry,
                "registered_at": registered_at,
                "title": title.rstrip("."),
                "kind": kind,
                "url": url,
            }
        )
    total_match = _TOTAL_RE.search(html)
    total = int(total_match.group(1)) if total_match else None
    pages = [int(m.group(1)) for m in _PAGE_RE.finditer(html)]
    page_count = (max(pages) + 1) if pages else (1 if documents else 0)
    return documents, total, page_count


def parse_codes_html(html: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _CODE_LINK_RE.finditer(html):
        title = _strip_tags(match.group(2))
        url = _absolute(match.group(1))
        if not title or url in seen:
            continue
        seen.add(url)
        items.append({"title": title, "url": url, "kind": "Кодекс (каталог pravo.by)"})
    return items


def parse_document_card(html: str) -> dict[str, Any] | None:
    card_match = _CARD_RE.search(html)
    if not card_match:
        return None
    section = card_match.group(1)
    fields: dict[str, str] = {}
    for match in _FIELD_RE.finditer(section):
        label = _strip_tags(match.group(1))
        fields[label] = _strip_tags(match.group(2))
    pdf_match = _PDF_RE.search(section)
    title = fields.get("Название акта") or ""
    return {
        "kind": "card",
        "title": title,
        "act": fields.get("Вид акта, орган принятия, дата и номер принятия (издания)", ""),
        "registry_number": fields.get("Регистрационный номер Национального реестра", ""),
        "registered_at": fields.get("Дата включения в Национальный реестр", ""),
        "effective_at": fields.get("Дата вступления в силу", ""),
        "publication": fields.get("Источник(и) официального опубликования", ""),
        "pdf_url": _absolute(pdf_match.group(1)) if pdf_match else "",
        "fields": fields,
    }


def _snippets(text: str, query: str) -> list[str]:
    hay = text.lower()
    needle = query.lower().strip()
    if not needle:
        return []
    snippets: list[str] = []
    start = 0
    while len(snippets) < MAX_SNIPPETS:
        idx = hay.find(needle, start)
        if idx < 0:
            break
        left = max(0, idx - SNIPPET_RADIUS)
        right = min(len(text), idx + len(needle) + SNIPPET_RADIUS)
        chunk = text[left:right].strip()
        if left > 0:
            chunk = "…" + chunk
        if right < len(text):
            chunk = chunk + "…"
        snippets.append(re.sub(r"\s+", " ", chunk))
        start = idx + len(needle)
    return snippets


async def _fetch(url: str, params: dict[str, Any] | None = None) -> tuple[int, str, str]:
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SEC,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        response = await client.get(url, params=params)
        final = str(response.url)
        return response.status_code, response.text or "", final


def _error(message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"source": "pravo.by", "error": message, "hint": HINT}
    payload.update(extra)
    return payload


def _codes_match(query: str, codes: list[dict[str, str]]) -> list[dict[str, str]]:
    tokens = [part for part in re.split(r"\s+", query.lower()) if len(part) >= 3]
    if not tokens or all(token in _GENERIC_CODE_TOKENS for token in tokens):
        return []
    hits: list[dict[str, str]] = []
    for item in codes:
        hay = item["title"].lower()
        if all(token in hay for token in tokens) or query.lower() in hay:
            hits.append(
                {
                    "registry_number": "",
                    "registered_at": "",
                    "title": item["title"],
                    "kind": item["kind"],
                    "url": item["url"],
                }
            )
    return hits


async def _codes_catalog() -> list[dict[str, str]]:
    global _codes_cache
    now = time.monotonic()
    if _codes_cache and now - _codes_cache[0] < CODES_TTL_SEC:
        return _codes_cache[1]
    try:
        status, body, _final = await _fetch(PRAVO_ORIGIN + CODES_PATH)
    except httpx.HTTPError:
        return _codes_cache[1] if _codes_cache else []
    if status != 200:
        return _codes_cache[1] if _codes_cache else []
    items = parse_codes_html(body)
    _codes_cache = (now, items)
    return items


async def search_register(
    query: str = "",
    registry_number: str = "",
    page: int = 1,
) -> dict[str, Any]:
    query = (query or "").strip()[:MAX_QUERY_LEN]
    registry_number = (registry_number or "").strip().replace(" ", "")
    if not registry_number and query and _looks_like_registry(query):
        registry_number = query
        query = ""
    if not query and not registry_number:
        return _error("Укажи query (название/тема) или registry_number (например 2/1742).")
    if page < 1:
        page = 1
    if page > MAX_PAGE:
        page = MAX_PAGE

    params: dict[str, Any] = {}
    if registry_number:
        params["p1"] = registry_number
    else:
        params["p0"] = query
    if page > 1:
        params["p2"] = page - 1

    search_url = PRAVO_ORIGIN + SEARCH_PATH
    try:
        status, body, _final = await _fetch(search_url, params=params)
    except httpx.HTTPError as exc:
        return _error(f"Не удалось обратиться к pravo.by: {exc}")
    if status == 403:
        return _error("pravo.by отклонил запрос (HTTP 403).")
    if status != 200:
        return _error(f"pravo.by вернул HTTP {status}.")

    documents, total, page_count = parse_search_html(body)
    codes_hits: list[dict[str, str]] = []
    if query and page == 1:
        codes_hits = _codes_match(query, await _codes_catalog())
        existing = {item["url"] for item in documents}
        codes_hits = [item for item in codes_hits if item["url"] not in existing]

    merged = codes_hits + documents
    qs = urllib.parse.urlencode(params, doseq=True)
    return {
        "source": "pravo.by",
        "query": query,
        "registry_number": registry_number,
        "page": page,
        "page_count": page_count,
        "total": total if total is not None else len(documents),
        "count": len(merged),
        "codes_matched": len(codes_hits),
        "search_url": f"{search_url}?{qs}" if qs else search_url,
        "documents": merged,
        "hint": HINT,
    }


def _host_allowed(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in ALLOWED_HOSTS


async def get_document(url: str, query: str = "") -> dict[str, Any]:
    normalized = normalize_pravo_url(url)
    if not normalized:
        return _error("Нужен полный URL документа на pravo.by (из pravo_search).")
    query = (query or "").strip()[:MAX_QUERY_LEN]
    try:
        status, body, final_url = await _fetch(normalized)
    except httpx.HTTPError as exc:
        return _error(f"Не удалось обратиться к pravo.by: {exc}")
    if not _host_allowed(final_url):
        return _error("Редирект ушёл с pravo.by — документ не читаю.")
    if status == 403:
        return _error("pravo.by отклонил запрос (HTTP 403).")
    if status != 200:
        return _error(f"pravo.by вернул HTTP {status}.")

    card = parse_document_card(body)
    if card:
        card.update(
            {
                "source": "pravo.by",
                "url": final_url or normalized,
                "hint": HINT,
            }
        )
        if query:
            card["note"] = (
                "Это карточка реестра без полного текста. "
                "Открой pdf_url или HTML-публикацию, если есть."
            )
        return card

    text = extract_visible_text(body)
    payload: dict[str, Any] = {
        "source": "pravo.by",
        "kind": "html",
        "url": final_url or normalized,
        "title": "",
        "hint": HINT,
    }
    title_match = re.search(r"<title>(.*?)</title>", body, re.I | re.S)
    if title_match:
        payload["title"] = _strip_tags(title_match.group(1))
    if query:
        snippets = _snippets(text, query)
        payload["query"] = query
        payload["snippets"] = snippets
        if not snippets:
            payload["note"] = "В тексте страницы нет точного вхождения запроса."
            payload["text"] = text[:MAX_TEXT_CHARS]
        return payload
    payload["text"] = text[:MAX_TEXT_CHARS]
    if len(text) > MAX_TEXT_CHARS:
        payload["truncated"] = True
        payload["note"] = (
            "Текст обрезан. Уточни статью или фрагмент через query "
            "(например «статья 42»), чтобы получить сниппеты."
        )
    return payload
