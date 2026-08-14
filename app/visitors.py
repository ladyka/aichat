from __future__ import annotations

import re

KIND_HUMAN = "human"
KIND_CRAWLER = "crawler"
KIND_BOT = "bot"

UA_MAX_LEN = 512
LABEL_MAX_LEN = 40

# Link-preview fetchers and search crawlers first; then automation clients.
# Order matters: more specific patterns win.
_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
    (kind, label, re.compile(pattern, re.IGNORECASE))
    for kind, label, pattern in (
        (KIND_CRAWLER, "telegram", r"TelegramBot"),
        (KIND_CRAWLER, "facebook", r"facebookexternalhit|Facebot"),
        (KIND_CRAWLER, "twitter", r"Twitterbot"),
        (KIND_CRAWLER, "slack", r"Slackbot|Slack-ImgProxy"),
        (KIND_CRAWLER, "whatsapp", r"WhatsApp"),
        (KIND_CRAWLER, "discord", r"Discordbot"),
        (KIND_CRAWLER, "linkedin", r"LinkedInBot"),
        (KIND_CRAWLER, "vk", r"VKShare|vkShare|Mail\.RU_Bot"),
        (KIND_CRAWLER, "skype", r"SkypeUriPreview"),
        (KIND_CRAWLER, "pinterest", r"Pinterestbot"),
        (KIND_CRAWLER, "reddit", r"redditbot"),
        (KIND_CRAWLER, "embed", r"Iframely|Embedly|opengraph"),
        (KIND_CRAWLER, "google", r"Googlebot|Google-InspectionTool|Storebot-Google"),
        (KIND_CRAWLER, "bing", r"bingbot|msnbot|BingPreview"),
        (KIND_CRAWLER, "yandex", r"YandexBot|YandexImages|YandexRender"),
        (KIND_CRAWLER, "baidu", r"Baiduspider"),
        (KIND_CRAWLER, "duckduckgo", r"DuckDuckBot"),
        (KIND_CRAWLER, "apple", r"Applebot"),
        (KIND_CRAWLER, "petal", r"PetalBot"),
        (KIND_BOT, "openai", r"GPTBot|ChatGPT-User|OAI-SearchBot"),
        (KIND_BOT, "anthropic", r"ClaudeBot|anthropic-ai|Claude-User"),
        (KIND_BOT, "perplexity", r"PerplexityBot"),
        (KIND_BOT, "bytespider", r"Bytespider"),
        (KIND_BOT, "ccbot", r"CCBot"),
        (KIND_BOT, "curl", r"\bcurl/"),
        (KIND_BOT, "wget", r"\bwget/"),
        (KIND_BOT, "httpie", r"\bHTTPie/"),
        (KIND_BOT, "python", r"python-requests|httpx|aiohttp|Python-urllib"),
        (KIND_BOT, "go", r"Go-http-client"),
        (KIND_BOT, "java", r"Apache-HttpClient|Java/"),
        (KIND_BOT, "headless", r"HeadlessChrome|PhantomJS|SlimerJS"),
    )
)

_GENERIC_CRAWLER = re.compile(r"crawler|spider|preview", re.IGNORECASE)
_GENERIC_BOT = re.compile(r"\bbot\b", re.IGNORECASE)


def classify_visitor(user_agent: str | None) -> tuple[str, str]:
    """Return (visitor_kind, visitor_label) for a User-Agent string."""
    ua = (user_agent or "").strip()
    if not ua:
        return KIND_BOT, "empty"

    for kind, label, pattern in _RULES:
        if pattern.search(ua):
            return kind, label

    if _GENERIC_CRAWLER.search(ua):
        return KIND_CRAWLER, "other"
    if _GENERIC_BOT.search(ua):
        return KIND_BOT, "other"
    return KIND_HUMAN, "browser"


def truncate_user_agent(user_agent: str | None) -> str:
    ua = (user_agent or "").strip()
    if len(ua) <= UA_MAX_LEN:
        return ua
    return ua[: UA_MAX_LEN - 1] + "…"
