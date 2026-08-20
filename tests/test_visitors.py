from app.og import DEFAULT_OG_DESCRIPTION, OG_IMAGE_PATH, plain_snippet
from app.visitors import (
    KIND_BOT,
    KIND_CRAWLER,
    KIND_HUMAN,
    UA_MAX_LEN,
    classify_visitor,
    truncate_user_agent,
)


def test_classify_preview_crawlers():
    cases = {
        "TelegramBot (like TwitterBot)": (KIND_CRAWLER, "telegram"),
        "facebookexternalhit/1.1": (KIND_CRAWLER, "facebook"),
        "Twitterbot/1.0": (KIND_CRAWLER, "twitter"),
        "Slackbot-LinkExpanding 1.0": (KIND_CRAWLER, "slack"),
        "WhatsApp/2.23.0": (KIND_CRAWLER, "whatsapp"),
        "Mozilla/5.0 (compatible; Discordbot/2.0)": (KIND_CRAWLER, "discord"),
        "LinkedInBot/1.0": (KIND_CRAWLER, "linkedin"),
        "Mozilla/5.0 (compatible; Googlebot/2.1)": (KIND_CRAWLER, "google"),
        "Mozilla/5.0 (compatible; YandexBot/3.0)": (KIND_CRAWLER, "yandex"),
        "Mozilla/5.0 (compatible; bingbot/2.0)": (KIND_CRAWLER, "bing"),
        "Iframely/1.0": (KIND_CRAWLER, "embed"),
    }
    for ua, expected in cases.items():
        assert classify_visitor(ua) == expected, ua


def test_classify_bots_and_humans():
    assert classify_visitor("") == (KIND_BOT, "empty")
    assert classify_visitor("   ") == (KIND_BOT, "empty")
    assert classify_visitor("curl/8.5.0") == (KIND_BOT, "curl")
    assert classify_visitor("python-requests/2.32.0") == (KIND_BOT, "python")
    assert classify_visitor("Mozilla/5.0 AppleWebKit GPTBot") == (KIND_BOT, "openai")
    assert classify_visitor("SomeUnknownCrawler/1.0") == (KIND_CRAWLER, "other")
    assert classify_visitor("Friendly-bot") == (KIND_BOT, "other")
    assert classify_visitor(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ) == (KIND_HUMAN, "browser")


def test_truncate_user_agent():
    assert truncate_user_agent(None) == ""
    assert truncate_user_agent("curl/8") == "curl/8"
    long = "x" * (UA_MAX_LEN + 20)
    clipped = truncate_user_agent(long)
    assert len(clipped) == UA_MAX_LEN
    assert clipped.endswith("…")


def test_plain_snippet_strips_markdown_and_truncates():
    assert plain_snippet("   ") == ""
    assert plain_snippet("  **Привет**, [мир](https://x)  ") == "Привет, мир"
    assert DEFAULT_OG_DESCRIPTION
    assert OG_IMAGE_PATH.endswith(".png")
    long = "слово " * 80
    snippet = plain_snippet(long, limit=40)
    assert snippet.endswith("…")
    assert len(snippet) <= 40
