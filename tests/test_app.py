import json

from app import app, youtube_error_message


def test_youtube_bot_error_has_cookie_guidance():
    message = youtube_error_message(
        "[youtube] abc123: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication."
    )
    assert "cookies" in message.lower()
    assert "browser" in message.lower()


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
