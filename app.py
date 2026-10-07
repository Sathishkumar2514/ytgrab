import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, after_this_request, jsonify, render_template, request, send_file
import yt_dlp
from yt_dlp.utils import DownloadError

load_dotenv(Path(__file__).resolve().parent / ".env")


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
    return app


app = create_app()

HAS_FFMPEG = shutil.which("ffmpeg") is not None
ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
HEIGHTS = [2160, 1440, 1080, 720, 480, 360]
DEFAULT_YOUTUBE_PLAYER_CLIENTS = ["android", "web"]


def build_youtube_extractor_args() -> dict:
    configured = os.getenv("YTDLP_PLAYER_CLIENTS", "").strip()
    clients = [part.strip() for part in configured.split(",") if part.strip()] if configured else DEFAULT_YOUTUBE_PLAYER_CLIENTS
    args = {"youtube": {"player_client": clients}}

    visitor_data = os.getenv("YTDLP_VISITOR_DATA", "").strip()
    if visitor_data:
        args["youtube"]["visitor_data"] = visitor_data

    return args


YOUTUBE_EXTRACTOR_ARGS = build_youtube_extractor_args()


def valid_youtube_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and (p.hostname or "").lower() in ALLOWED_HOSTS


def safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", title).strip()[:120] or "video"


def cookie_file_has_data(path: str) -> bool:
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return False

    stripped = content.strip()
    if not stripped:
        return False

    lower = stripped.lower()
    if "intentionally empty" in lower or "replace this file with a real exported" in lower:
        return False
    return True


def youtube_error_message(exc: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "")
    lower = msg.lower()
    if "sign in to confirm you’re not a bot" in lower or "sign in to confirm you're not a bot" in lower:
        return (
            "This YouTube video is blocking anonymous access. "
            "Use a browser cookie export or set YTDLP_COOKIES_PATH in production. "
            "Example: YTDLP_COOKIES_PATH=/path/to/yt_cookies.txt"
        )
    return msg


def build_yt_dlp_options(*, skip_download: bool = False, output_dir: Optional[str] = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": build_youtube_extractor_args(),
    }
    if skip_download:
        opts["skip_download"] = True
    if output_dir:
        opts["outtmpl"] = os.path.join(output_dir, "%(title).100s.%(ext)s")

    user_agent = os.getenv("YTDLP_USER_AGENT")
    if user_agent:
        opts["http_headers"] = {"User-Agent": user_agent}

    cookies_path = os.getenv("YTDLP_COOKIES_PATH")
    cookies_content = os.getenv("YTDLP_COOKIES_CONTENT")
    if cookies_content:
        cookies_path = "/tmp/yt_cookies.txt"
        with open(cookies_path, "w", encoding="utf-8") as f:
            f.write(cookies_content)

    cookies_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER")
    if cookies_browser:
        opts["cookiesfrombrowser"] = [cookies_browser]
    elif cookies_path and cookie_file_has_data(cookies_path):
        opts["cookies"] = cookies_path

    return opts


@app.get("/")
def index():
    return render_template("index.html", has_ffmpeg=HAS_FFMPEG)


@app.post("/api/info")
def info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not valid_youtube_url(url):
        return jsonify(error="Enter a full YouTube link, like https://www.youtube.com/watch?v=..."), 400
    opts = build_yt_dlp_options(skip_download=True)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=False)
    except DownloadError as e:
        msg = youtube_error_message(e)
        return jsonify(error=f"Couldn't read that video. {msg}"), 422
    except Exception as e:  # network errors etc.
        return jsonify(error=f"Something went wrong: {e}"), 500

    available = {f.get("height") for f in data.get("formats", []) if f.get("height") and f.get("vcodec") != "none"}
    qualities = [h for h in HEIGHTS if any(a and a >= h for a in available)] or [360]
    return jsonify(
        title=data.get("title"),
        channel=data.get("uploader"),
        duration=data.get("duration"),
        thumbnail=data.get("thumbnail"),
        qualities=qualities,
        has_ffmpeg=HAS_FFMPEG,
    )


@app.get("/api/download")
def download():
    url = request.args.get("url", "").strip()
    kind = request.args.get("kind", "video")      # video | audio
    height = request.args.get("height", "720")
    if not valid_youtube_url(url):
        return jsonify(error="Invalid YouTube link."), 400
    if kind not in ("video", "audio") or not height.isdigit() or int(height) not in HEIGHTS:
        return jsonify(error="Invalid options."), 400
    h = int(height)

    tmp = tempfile.mkdtemp(prefix="ytgrab_")
    opts = build_yt_dlp_options(output_dir=tmp)
    if kind == "audio":
        if HAS_FFMPEG:
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        else:
            opts["format"] = "bestaudio[ext=m4a]/bestaudio"
    else:
        if HAS_FFMPEG:
            opts["format"] = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
            opts["merge_output_format"] = "mp4"
        else:  # single-file formats only (no merging possible)
            opts["format"] = f"best[height<={h}][ext=mp4]/best[height<={h}]/best"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify(error=f"Download failed: {e}"), 500

    files = [f for f in os.listdir(tmp) if not f.endswith((".part", ".ytdl"))]
    if not files:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify(error="Download produced no file."), 500
    path = os.path.join(tmp, files[0])
    ext = os.path.splitext(files[0])[1]

    @after_this_request
    def cleanup(resp):
        resp.call_on_close(lambda: shutil.rmtree(tmp, ignore_errors=True))
        return resp

    return send_file(path, as_attachment=True, download_name=safe_name(os.path.splitext(files[0])[0]) + ext)


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    app.run(host=host, port=port, debug=False)
