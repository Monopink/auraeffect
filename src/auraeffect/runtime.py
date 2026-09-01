from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    ffmpeg: Path
    ffprobe: Path
    avs2pipemod: Path
    avisynth_dll: Path
    image_seq_dll: Path
    avsinpaint_dll: Path
    masktools_dll: Path
    neo_fft3d_dll: Path | None = None
    grunt_dll: Path | None = None
    rgtools_dll: Path | None = None
    rt_stats_dll: Path | None = None
    inpaintdelogo_avsi: Path | None = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pick(paths: list[Path], *, prefer: tuple[str, ...] = (), avoid: tuple[str, ...] = ()) -> Path:
    filtered = [p for p in paths if all(token.lower() in str(p).lower() for token in prefer)]
    if not filtered:
        filtered = paths[:]
    filtered = [p for p in filtered if not any(token.lower() in str(p).lower() for token in avoid)]
    if not filtered:
        filtered = paths[:]
    if not filtered:
        raise FileNotFoundError("runtime file not found")
    return sorted(filtered, key=lambda p: len(str(p)))[0]


def _find_exact(name: str, *, prefer: tuple[str, ...] = (), avoid: tuple[str, ...] = ()) -> Path:
    root = repo_root()
    candidates = [p for p in root.rglob(name) if p.is_file()]
    if not candidates:
        raise FileNotFoundError(f"could not find {name} under {root}")
    return _pick(candidates, prefer=prefer, avoid=avoid)


def _find_optional(name: str, *, prefer: tuple[str, ...] = (), avoid: tuple[str, ...] = ()) -> Path | None:
    try:
        return _find_exact(name, prefer=prefer, avoid=avoid)
    except FileNotFoundError:
        return None


def _find_optional_any(patterns: tuple[str, ...], *, prefer: tuple[str, ...] = (), avoid: tuple[str, ...] = ()) -> Path | None:
    root = repo_root()
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(p for p in root.rglob(pattern) if p.is_file())
    if not candidates:
        return None
    return _pick(candidates, prefer=prefer, avoid=avoid)


def discover_runtime() -> RuntimePaths:
    return RuntimePaths(
        ffmpeg=_find_exact("ffmpeg.exe", prefer=("ffmpeg-9.0.1-essentials_build", "bin")),
        ffprobe=_find_exact("ffprobe.exe", prefer=("ffmpeg-9.0.1-essentials_build", "bin")),
        avs2pipemod=_find_exact("avs2pipemod64.exe", prefer=("avs2pipemod",)),
        avisynth_dll=_find_exact("AviSynth.dll", prefer=("avisynthplus", "full", "x64"), avoid=("xp", "arm64")),
        image_seq_dll=_find_exact("ImageSeq.dll", prefer=("x64", "plugins")),
        avsinpaint_dll=_find_exact("AvsInPaint.dll", prefer=("x64", "plugins")),
        neo_fft3d_dll=_find_optional_any(("neo-fft3d.dll", "neo_fft3d.dll"), prefer=("neo_fft3d",), avoid=("x86",)),
        masktools_dll=_find_exact("masktools2.dll", prefer=("x64", "plugins")),
        grunt_dll=_find_optional("grunt.dll", prefer=("x64",)),
        rgtools_dll=_find_optional("RgTools.dll", prefer=("x64",)),
        rt_stats_dll=_find_optional_any(("RT_Stats.dll", "RT_Stats_x64.dll", "RT_Stats_*.dll"), prefer=("x64",)),
        inpaintdelogo_avsi=_find_optional("InpaintDelogo.avsi", prefer=("inpaintdelogo",)),
    )


def avi_string(path: Path) -> str:
    return path.resolve().as_posix()
