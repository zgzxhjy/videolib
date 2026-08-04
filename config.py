import os
from pathlib import Path

APP_NAME = "VideoLib"

APP_DIR = Path(os.environ.get("VIDEOLIB_HOME", Path.home() / ".videolib"))
DB_PATH = APP_DIR / "videolib.db"
THUMBS_DIR = APP_DIR / "thumbs"

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp", ".rmvb",
}

SEARCH_LIMIT = 500
