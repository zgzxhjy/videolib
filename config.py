import json
import os
from pathlib import Path

APP_NAME = "VideoLib"

APP_DIR = Path(os.environ.get("VIDEOLIB_HOME", Path.home() / ".videolib"))
DB_PATH = APP_DIR / "videolib.db"
THUMBS_DIR = APP_DIR / "thumbs"
SETTINGS_PATH = APP_DIR / "settings.json"
BACKUPS_DIR = APP_DIR / "backups"

BACKUP_KEEP = 5

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp", ".rmvb", ".bin",
}

SEARCH_LIMIT = 500


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_setting(key: str, value) -> None:
    settings = load_settings()
    settings[key] = value
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
