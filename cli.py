import argparse
import time

import config
from domain.repository import Repository
from services.library import Library
from services.scanner import diff_scan, scan_directory


def cmd_index(args) -> None:
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    repo = Repository(config.DB_PATH)
    start = time.perf_counter()
    files = scan_directory(args.root)
    print(f"found {len(files)} video files in {time.perf_counter() - start:.1f}s")

    need_probe, stale = diff_scan(
        files,
        repo.existing_under(args.root),
        missing_meta=repo.missing_metadata_under(args.root),
    )
    print(f"{len(need_probe)} to probe, {len(stale)} stale under root")

    result = Library(repo).apply_sync(
        need_probe,
        stale,
        progress=lambda done, total, _fp: print(f"  probed {done}/{total}") if done % 200 == 0 else None,
    )
    print(f"upserted {result.probed} rows ({result.changed} changed)")
    if result.removed:
        print(f"removed {result.removed} stale entries")
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
