import json

import pytest

from app import app, build_yt_dlp_options, youtube_error_message


def test_youtube_bot_error_has_cookie_guidance():
    message = youtube_error_message(
        "[youtube] abc123: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication."
    )
    assert "cookies" in message.lower()
    assert "browser" in message.lower()


def test_build_yt_dlp_options_skips_placeholder_cookie_file(monkeypatch, tmp_path):
    cookie_path = tmp_path / "yt_cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n# This file is intentionally empty for local setup.\n", encoding="utf-8")
    monkeypatch.setenv("YTDLP_COOKIES_PATH", str(cookie_path))
    monkeypatch.delenv("YTDLP_COOKIES_CONTENT", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_FROM_BROWSER", raising=False)

    options = build_yt_dlp_options(skip_download=True)

    assert "cookies" not in options


def test_build_yt_dlp_options_uses_configured_youtube_client(monkeypatch):
    monkeypatch.setenv("YTDLP_PLAYER_CLIENTS", "mweb")
    monkeypatch.delenv("YTDLP_COOKIES_PATH", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_CONTENT", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_FROM_BROWSER", raising=False)

    options = build_yt_dlp_options(skip_download=True)

    assert options["extractor_args"]["youtube"]["player_client"] == ["mweb"]


def test_info_endpoint_accepts_real_youtube_url():
    client = app.test_client()
    response = client.post(
        "/api/info",
        data=json.dumps({"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"}),
        content_type="application/json",
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["title"]
    assert payload["qualities"]
