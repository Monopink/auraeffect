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


def discover_runtime() -> RuntimePaths:
    return RuntimePaths(
        ffmpeg=_find_exact("ffmpeg.exe", prefer=("ffmpeg-9.0.1-essentials_build", "bin")),
        ffprobe=_find_exact("ffprobe.exe", prefer=("ffmpeg-9.0.1-essentials_build", "bin")),
        avs2pipemod=_find_exact("avs2pipemod64.exe", prefer=("avs2pipemod",)),
        avisynth_dll=_find_exact("AviSynth.dll", prefer=("avisynthplus", "full", "x64"), avoid=("xp", "arm64")),
        image_seq_dll=_find_exact("ImageSeq.dll", prefer=("x64", "plugins")),
        avsinpaint_dll=_find_exact("AvsInPaint.dll", prefer=("x64", "plugins")),
        masktools_dll=_find_exact("masktools2.dll", prefer=("x64", "plugins")),
    )


def avi_string(path: Path) -> str:
    return path.resolve().as_posix()
