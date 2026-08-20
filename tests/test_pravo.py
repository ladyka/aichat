import asyncio
import json

from app.pravo import (
    clear_caches,
    extract_visible_text,
    get_document,
    normalize_pravo_url,
    parse_codes_html,
    parse_document_card,
    parse_search_html,
    search_register,
)
from app.tools import call_tool, enabled_tools

SEARCH_HTML = """
<h3 id="paging">Количество найденных документов: 2</h3>
<div class="doc">
  <dl>
    <dt>
      2/2129
      <br/>
      (22.01.2014)
    </dt>
    <dd>
      <a href="/document/?guid=3961&amp;p0=H11400131" target="_blank">О внесении изменений в Трудовой кодекс</a>.
      <br/>
      <i>Закон Республики Беларусь от 8 января 2014 г. № 131-З</i>
    </dd>
  </dl>
</div>
<div class="doc">
  <dl>
    <dt>
      2/1742
      <br/>
      (22.11.2010)
    </dt>
    <dd>
      <a href="/document/?guid=3961&amp;p0=H11000190" target="_blank">О наименованиях географических объектов</a>.
      <br/>
      <i>Закон Республики Беларусь от 16 ноября 2010 г. № 190-З</i>
    </dd>
  </dl>
</div>
<div class="pages">
  <span>1</span>
  <a href="#" data-type="p0" data-page="1#paging">2</a>
</div>
"""

CODES_HTML = """
<div class="s-block-link">
  <div class="s-block-link-innertext-wrapper">
    <a target="_blank" href="/document/?guid=3871&p0=HK9900296">Трудовой кодекс Республики Беларусь</a>
  </div>
</div>
<div class="s-block-link">
  <div class="s-block-link-innertext-wrapper">
    <a href="https://etalonline.by/document/?regnum=hk2400359" target="_blank">Кодекс гражданского судопроизводства Республики Беларусь</a>
  </div>
</div>
"""

CARD_HTML = """
<title>Информация о документе</title>
<div class="reestrmap">
  <div class="reestrmap-aside">
    <a href="/document/?guid=12551&amp;p0=H11400131" class="link-pdf">PDF</a>
  </div>
  <b>Название акта</b>
  <div>
    <a href="/document/?guid=12551&amp;p0=H11400131" class="link-pdf">О внесении изменений</a>
  </div>
  <b>Вид акта, орган принятия, дата и номер принятия (издания)</b>
  <div>Закон Республики Беларусь от 8 января 2014 г. № 131-З</div>
  <b>Регистрационный номер Национального реестра</b>
  <div>2/2129</div>
  <b>Дата включения в Национальный реестр</b>
  <div>22.01.2014</div>
  <b>Дата вступления в силу</b>
  <div>25.07.2014</div>
  <b>Источник(и) официального опубликования</b>
  <div>Национальный правовой Интернет-портал, 24.01.2014, 2/2129</div>
</div>
<style>.tl{}</style>
"""

TEXT_HTML = """
<html><head><title>Трудовой кодекс Республики Беларусь</title>
<script>var x = 1;</script>
</head><body>
<p>Статья 42. Расторжение трудового договора.</p>
<p>Наниматель может расторгнуть договор в случаях...</p>
</body></html>
"""


class FakeResp:
    def __init__(self, text="", status_code=200, url=""):
        self.text = text
        self.status_code = status_code
        self.url = url


class FakePravoClient:
    def __init__(self):
        self.gets: list[str] = []
        self.search_html = SEARCH_HTML
        self.codes_html = CODES_HTML
        self.document_html = CARD_HTML
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        params = kw.get("params") or {}
        joined = str(url)
        if params:
            joined += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        self.gets.append(joined)
        if self.status != 200:
            return FakeResp(text="err", status_code=self.status, url=joined)
        if "kodeksy" in joined:
            return FakeResp(text=self.codes_html, url=joined)
        if "/document/" in joined:
            return FakeResp(text=self.document_html, url=joined)
        return FakeResp(text=self.search_html, url=joined)


def _patch_pravo(monkeypatch, client=None):
    clear_caches()
    client = client or FakePravoClient()
    monkeypatch.setattr("app.pravo.httpx.AsyncClient", lambda *a, **kw: client)
    return client


def test_parse_search_html():
    docs, total, pages = parse_search_html(SEARCH_HTML)
    assert total == 2
    assert pages == 2
    assert docs[0]["registry_number"] == "2/2129"
    assert docs[0]["registered_at"] == "22.01.2014"
    assert "Трудовой кодекс" in docs[0]["title"]
    assert docs[0]["url"].startswith("https://pravo.by/document/")
    assert "131-З" in docs[0]["kind"]


def test_parse_codes_and_card():
    codes = parse_codes_html(CODES_HTML)
    assert len(codes) == 2
    assert codes[0]["title"].startswith("Трудовой кодекс")
    card = parse_document_card(CARD_HTML)
    assert card is not None
    assert card["registry_number"] == "2/2129"
    assert card["pdf_url"].endswith("H11400131")
    assert card["effective_at"] == "25.07.2014"


def test_extract_visible_text_skips_script():
    text = extract_visible_text(TEXT_HTML)
    assert "Статья 42" in text
    assert "var x" not in text


def test_normalize_pravo_url():
    assert normalize_pravo_url("https://pravo.by/document/?guid=1") is not None
    assert normalize_pravo_url("https://etalonline.by/document/?regnum=1") is None
    assert normalize_pravo_url("https://evil.example/pravo.by") is None


def test_search_register_query(monkeypatch):
    client = _patch_pravo(monkeypatch)
    result = asyncio.run(search_register(query="Трудовой кодекс"))
    assert result["source"] == "pravo.by"
    assert result["codes_matched"] == 1
    assert result["documents"][0]["title"].startswith("Трудовой кодекс")
    assert any("H11400131" in item["url"] for item in result["documents"])
    assert "p0=Трудовой кодекс" in client.gets[0] or "p0=" in client.gets[0]


def test_search_detects_registry_number(monkeypatch):
    client = _patch_pravo(monkeypatch)
    result = asyncio.run(search_register(query="2/1742"))
    assert result["registry_number"] == "2/1742"
    assert result["query"] == ""
    assert any("p1=2/1742" in item for item in client.gets)


def test_generic_query_skips_all_codes():
    from app.pravo import _codes_match

    codes = parse_codes_html(CODES_HTML)
    assert _codes_match("кодекс", codes) == []
    assert len(_codes_match("Трудовой кодекс", codes)) == 1


def test_search_requires_input(monkeypatch):
    _patch_pravo(monkeypatch)
    result = asyncio.run(search_register())
    assert "error" in result
    _patch_pravo(monkeypatch)
    result = asyncio.run(search_register())
    assert "error" in result


def test_get_document_card(monkeypatch):
    _patch_pravo(monkeypatch)
    result = asyncio.run(get_document("https://pravo.by/document/?guid=3961&p0=H11400131"))
    assert result["kind"] == "card"
    assert result["registry_number"] == "2/2129"
    assert "pdf_url" in result


def test_get_document_html_snippets(monkeypatch):
    client = _patch_pravo(monkeypatch)
    client.document_html = TEXT_HTML
    result = asyncio.run(
        get_document(
            "https://pravo.by/document/?guid=3871&p0=HK9900296",
            query="статья 42",
        )
    )
    assert result["kind"] == "html"
    assert result["snippets"]
    assert "Статья 42" in result["snippets"][0]


def test_get_document_rejects_foreign_host(monkeypatch):
    _patch_pravo(monkeypatch)
    result = asyncio.run(get_document("https://example.com/x"))
    assert "error" in result


def test_search_http_error(monkeypatch):
    client = FakePravoClient()
    client.status = 403
    _patch_pravo(monkeypatch, client)
    result = asyncio.run(search_register(query="тест"))
    assert "403" in result["error"]


def test_tools_enabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openweather_api_key", "")
    monkeypatch.setattr(settings, "pzz_enabled", False)
    monkeypatch.setattr(settings, "pravo_enabled", True)
    names = [t["function"]["name"] for t in enabled_tools()]
    assert names == [
        "get_current_datetime",
        "download_file",
        "pravo_search",
        "pravo_get_document",
    ]


def test_call_tool_search(monkeypatch):
    _patch_pravo(monkeypatch)
    monkeypatch.setattr(get_settings(), "pravo_enabled", True)
    result = json.loads(asyncio.run(call_tool("pravo_search", '{"query": "Трудовой кодекс"}')))
    assert result["source"] == "pravo.by"
    assert result["count"] >= 1


def test_pravo_tools_disabled(monkeypatch):
    _patch_pravo(monkeypatch)
    monkeypatch.setattr(get_settings(), "pravo_enabled", False)
    result = json.loads(asyncio.run(call_tool("pravo_search", '{"query": "тест"}')))
    assert "выключен" in result["error"]
