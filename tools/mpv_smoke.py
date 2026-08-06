"""libmpv 冒烟测试:无窗口播放指定文件,验证 DLL 加载/解码/进度前进。

用法:python tools/mpv_smoke.py <视频路径> [播放秒数]
退出码 0 = 通过。
"""
import os
import sys
import time

vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
os.environ["PATH"] = vendor + os.pathsep + os.environ["PATH"]

import mpv


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: mpv_smoke.py <video> [seconds]", file=sys.stderr)
        return 2
    path = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

    p = mpv.MPV(vo="null", ao="null", hwdec="auto-safe", ytdl=False)
    try:
        p.play(path)
        try:
            p.wait_for_event("file-loaded", timeout=30)
        except Exception as exc:
            print(f"FAIL: load event {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        duration = p.duration
        print(f"mpv {p.mpv_version}  hwdec={p.hwdec}")
        print(f"video: {p.video_codec} {p.video_format} {p.width}x{p.height}")
        print(f"audio: {p.audio_codec}  duration={duration:.2f}s")

        pos0 = p.time_pos or 0.0
        time.sleep(seconds)
        pos1 = p.time_pos or 0.0
        print(f"time-pos: {pos0:.2f} -> {pos1:.2f} (+{pos1 - pos0:.2f}s)")
        if duration is None or duration <= 0:
            print("FAIL: no duration", file=sys.stderr)
            return 1
        if pos1 - pos0 < 0.5:
            print("FAIL: playback did not advance", file=sys.stderr)
            return 1
        print("SMOKE OK")
        return 0
    finally:
        p.terminate()


if __name__ == "__main__":
    sys.exit(main())
