"""PyInstaller hook: make PyAV's lazily-imported Cython submodules visible.

av/stream.py imports `from av.subtitles.stream import SubtitleStream` at
runtime only when a subtitle stream is encountered; PyInstaller's static
analysis cannot see such imports, so the modules never enter the frozen
module graph and the exe raises `ModuleNotFoundError: av.subtitles.stream`
for every video carrying an embedded subtitle track (metadata probe and
thumbnail generation then silently fail). The list mirrors the "missing
module named 'av.*'" entries of warn-VideoLib.txt; keep in sync whenever
PyAV is upgraded.
"""

hiddenimports = [
    "av.audio.codeccontext",
    "av.audio.frame",
    "av.audio.stream",
    "av.codec.codec",
    "av.codec.context",
    "av.container.core",
    "av.container.input",
    "av.container.output",
    "av.container.streams",
    "av.dictionary",
    "av.index",
    "av.logging",
    "av.opaque",
    "av.packet",
    "av.sidedata.encparams",
    "av.sidedata.motionvectors",
    "av.sidedata.sidedata",
    "av.subtitles.codeccontext",
    "av.subtitles.stream",
    "av.subtitles.subtitle",
    "av.utils",
    "av.video.codeccontext",
    "av.video.frame",
    "av.video.stream",
]
