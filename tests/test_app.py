import json

from app import app


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
