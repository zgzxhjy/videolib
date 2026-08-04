import argparse
import time

import config
from domain.repository import Repository
from services.metadata import build_video
from services.scanner import scan_directory


def cmd_index(args) -> None:
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    repo = Repository(config.DB_PATH)
    start = time.perf_counter()
    files = scan_directory(args.root)
    print(f"found {len(files)} video files in {time.perf_counter() - start:.1f}s")

    videos = []
    for i, fp in enumerate(files, 1):
        videos.append(build_video(fp))
        if i % 200 == 0:
            print(f"  probed {i}/{len(files)}")
    changed = repo.upsert_videos(videos)
    print(f"upserted {len(videos)} rows ({changed} changed)")

    known = repo.all_filepaths()
    missing = [fp for fp in known if fp not in set(files)]
    if missing:
        n = repo.remove_by_filepaths(missing)
        print(f"removed {n} stale entries")
    print(f"total in db: {repo.count()}")
    repo.close()


def cmd_stats(args) -> None:
    repo = Repository(config.DB_PATH)
    print(f"videos: {repo.count()}")
    print(f"categories: {len(repo.get_categories())}")
    repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="videolib")
    sub = parser.add_subparsers(dest="command", required=True)
    p_index = sub.add_parser("index", help="全量扫描并索引目录")
    p_index.add_argument("root", help="要扫描的目录")
    p_index.set_defaults(func=cmd_index)
    sub.add_parser("stats", help="显示库统计").set_defaults(func=cmd_stats)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
