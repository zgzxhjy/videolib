from pathlib import Path

import config
from domain.repository import Repository


def main() -> None:
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    repo = Repository(config.DB_PATH)
    print(f"VideoLib ready. Database: {config.DB_PATH}  Videos indexed: {repo.count()}")
    repo.close()


if __name__ == "__main__":
    main()
