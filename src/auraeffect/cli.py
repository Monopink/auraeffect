from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .runtime import avi_string, discover_runtime


@dataclass(frozen=True)
class ProbeInfo:
    width: int
    height: int
    fps: float
    frame_count: int


def _run(cmd: list[str], *, stdout=None, stderr=None) -> None:
    proc = subprocess.run(cmd, stdout=stdout, stderr=stderr, text=False)
    if proc.returncode != 0:
        details = ""
        if proc.stderr:
            details = "\n" + proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"command failed: {' '.join(cmd)}{details}")


def _capture(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"command failed: {' '.join(cmd)}")
    return proc.stdout


def probe_video(ffprobe: Path, input_path: Path) -> ProbeInfo:
    payload = _capture([
        str(ffprobe),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames:format=duration",
        "-of",
        "json",
        str(input_path),
    ])
    data = json.loads(payload)
    stream = data["streams"][0]
    width = int(stream["width"])
    height = int(stream["height"])
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/0"
    fps = float(Fraction(rate)) if rate != "0/0" else 0.0
    nb_frames = stream.get("nb_frames")
    if nb_frames and nb_frames != "N/A":
        frame_count = int(nb_frames)
    else:
        duration = float(data["format"]["duration"])
        frame_count = max(1, round(duration * fps))
    return ProbeInfo(width=width, height=height, fps=fps, frame_count=frame_count)


def extract_frames(ffmpeg: Path, input_path: Path, frames_dir: Path) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    _run([
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-fps_mode",
        "passthrough",
        "-start_number",
        "0",
        str(frames_dir / "%06d.png"),
    ])


def _make_avs_script(
    *,
    script_path: Path,
    input_frames: Path,
    mask_path: Path,
    runtime_image_seq: Path,
    runtime_inpaint: Path,
    runtime_masktools: Path,
    probe: ProbeInfo,
    radius: float,
    sharpness: float,
    preblur: float,
    postblur: float,
) -> None:
    frame_pattern = avi_string(input_frames / "%06d.png")
    mask_file = avi_string(mask_path)
    image_seq_dll = avi_string(runtime_image_seq)
    inpaint_dll = avi_string(runtime_inpaint)
    masktools_dll = avi_string(runtime_masktools)
    script = f'''LoadPlugin("{image_seq_dll}")
LoadPlugin("{masktools_dll}")
LoadCPlugin("{inpaint_dll}")

src = ImageSource("{frame_pattern}", start=0, end={probe.frame_count - 1}, fps={probe.fps:.10f}, use_DevIL=true, info=false, pixel_type="rgb24")
mask = ImageSource("{mask_file}", start=0, end={probe.frame_count - 1}, fps={probe.fps:.10f}, use_DevIL=true, info=false, pixel_type="rgb32")
out = InpaintLogo(src, mask, Radius={radius:.3f}, Sharpness={sharpness:.3f}, PreBlur={preblur:.3f}, PostBlur={postblur:.3f}, ChromaWeight=0.0, ChromaTensor=false, PixelAspect=1.0, Steps=-1)
out = out.ConvertToYV12(matrix="Rec709")
out
'''
    script_path.write_text(script, encoding="utf-8")


def encode_video(ffmpeg: Path, avs2pipemod: Path, avs_dll: Path, script_path: Path, y4m_path: Path, video_path: Path) -> None:
    with y4m_path.open("wb") as out:
        _run([
            str(avs2pipemod),
            f"-dll={avi_string(avs_dll)}",
            "-y4mp",
            str(script_path),
        ], stdout=out, stderr=subprocess.PIPE)
    _run([
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(y4m_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "18",
        "-preset",
        "medium",
        "-movflags",
        "+faststart",
        "-an",
        str(video_path),
    ])


def remux_audio(ffmpeg: Path, video_path: Path, input_path: Path, output_path: Path) -> None:
    _run([
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output_path),
    ])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auraeffect")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--mask", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--radius", type=float, default=5.0)
    parser.add_argument("--sharpness", type=float, default=30.0)
    parser.add_argument("--preblur", type=float, default=0.5)
    parser.add_argument("--postblur", type=float, default=4.0)
    parser.add_argument("--keep-workdir", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = discover_runtime()

    input_path = args.input.resolve()
    mask_path = args.mask.resolve()
    output_path = args.output.resolve()

    if not input_path.exists():
        raise SystemExit(f"missing input: {input_path}")
    if not mask_path.exists():
        raise SystemExit(f"missing mask: {mask_path}")

    probe = probe_video(runtime.ffprobe, input_path)
    if probe.fps <= 0:
        raise SystemExit("could not determine source frame rate")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="auraeffect-") as tmpdir:
        tmp = Path(tmpdir)
        frames_dir = tmp / "frames"
        work = tmp / "work"
        work.mkdir(parents=True, exist_ok=True)
        script_path = work / "render.avs"
        y4m_path = work / "video.y4m"
        encoded_path = work / "video.mp4"

        extract_frames(runtime.ffmpeg, input_path, frames_dir)
        _make_avs_script(
            script_path=script_path,
            input_frames=frames_dir,
            mask_path=mask_path,
            runtime_image_seq=runtime.image_seq_dll,
            runtime_inpaint=runtime.avsinpaint_dll,
            runtime_masktools=runtime.masktools_dll,
            probe=probe,
            radius=args.radius,
            sharpness=args.sharpness,
            preblur=args.preblur,
            postblur=args.postblur,
        )
        encode_video(runtime.ffmpeg, runtime.avs2pipemod, runtime.avisynth_dll, script_path, y4m_path, encoded_path)
        remux_audio(runtime.ffmpeg, encoded_path, input_path, output_path)
        if args.keep_workdir:
            preserved = output_path.with_suffix(".work")
            preserved.parent.mkdir(parents=True, exist_ok=True)
            if preserved.exists():
                shutil.rmtree(preserved)
            shutil.copytree(tmp, preserved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
