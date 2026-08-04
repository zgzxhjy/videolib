import argparse
import time

import config
from domain.repository import Repository
from services.metadata import build_video
from services.scanner import diff_scan, scan_directory
from services.thumbnailer import Thumbnailer


def cmd_index(args) -> None:
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    repo = Repository(config.DB_PATH)
    start = time.perf_counter()
    files = scan_directory(args.root)
    print(f"found {len(files)} video files in {time.perf_counter() - start:.1f}s")

    need_probe, stale = diff_scan(files, repo.existing_under(args.root))
    print(f"{len(need_probe)} to probe, {len(stale)} stale under root")

    videos = []
    for i, fp in enumerate(need_probe, 1):
        videos.append(build_video(fp))
        if i % 200 == 0:
            print(f"  probed {i}/{len(need_probe)}")
    changed = repo.upsert_videos(videos)
    print(f"upserted {len(videos)} rows ({changed} changed)")

    if stale:
        deleted_ids = repo.remove_by_filepaths(stale)
        Thumbnailer().delete_for(deleted_ids)
        print(f"removed {len(deleted_ids)} stale entries")
    repo.register_scan(args.root)
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
