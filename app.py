import os
import re
import shutil
import tempfile
from urllib.parse import urlparse

from flask import Flask, after_this_request, jsonify, render_template, request, send_file
import yt_dlp
from yt_dlp.utils import DownloadError


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
    return app


app = create_app()

HAS_FFMPEG = shutil.which("ffmpeg") is not None
ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
HEIGHTS = [2160, 1440, 1080, 720, 480, 360]
YOUTUBE_EXTRACTOR_ARGS = {"youtube": {"player_client": ["android", "web"]}}


def valid_youtube_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and (p.hostname or "").lower() in ALLOWED_HOSTS


def safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", title).strip()[:120] or "video"


@app.get("/")
def index():
    return render_template("index.html", has_ffmpeg=HAS_FFMPEG)


@app.post("/api/info")
def info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not valid_youtube_url(url):
        return jsonify(error="Enter a full YouTube link, like https://www.youtube.com/watch?v=..."), 400
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=False)
    except DownloadError as e:
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
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
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": os.path.join(tmp, "%(title).100s.%(ext)s"),
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
    }
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
