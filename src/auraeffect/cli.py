from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import struct
import time
import zlib
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

    @property
    def duration_seconds(self) -> float:
        if self.fps <= 0:
            return 0.0
        return self.frame_count / self.fps


ENGINE_AUTO = "auto"
ENGINE_AVSINPAINT = "avsinpaint"
ENGINE_INPAINTDELOGO = "inpaintdelogo"


def _decode_stderr(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def _run(cmd: list[str], *, stdout=None, stderr=None, env=None) -> None:
    proc = subprocess.run(cmd, stdout=stdout, stderr=stderr, text=False, env=env)
    if proc.returncode != 0:
        details = ""
        if proc.stderr:
            details = "\n" + _decode_stderr(proc.stderr)
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


def _clamp_fraction(value: float) -> float:
    return max(0.0, min(1.0, value))


def _format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _parse_ffmpeg_timestamp(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) != 3:
        return 0.0
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return 0.0
    return hours * 3600 + minutes * 60 + seconds


class ProgressBar:
    def __init__(self, total_stages: int, *, stream=None) -> None:
        self.total_stages = total_stages
        self.stream = stream or sys.stderr
        self.enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self._current_stage = 0
        self._current_label = ""
        self._last_width = 0
        self._last_render_at = 0.0
        self._line_open = False

    def start(self, stage: int, label: str) -> None:
        self._current_stage = stage
        self._current_label = label
        self._last_render_at = 0.0
        if self.enabled:
            self.update(0.0)
            return
        print(f"[{stage}/{self.total_stages}] {label}...", file=self.stream)

    def update(self, fraction: float, detail: str = "") -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        fraction = _clamp_fraction(fraction)
        if fraction < 1.0 and now - self._last_render_at < 0.1:
            return
        self._last_render_at = now
        width = 28
        filled = min(width, int(round(width * fraction)))
        bar = "#" * filled + "-" * (width - filled)
        percent = int(round(fraction * 100))
        line = f"[{self._current_stage}/{self.total_stages}] {self._current_label:<18} [{bar}] {percent:>3}%"
        if detail:
            line += f" {detail}"
        padding = " " * max(0, self._last_width - len(line))
        self.stream.write("\r" + line + padding)
        self.stream.flush()
        self._last_width = len(line)
        self._line_open = True

    def finish(self, detail: str = "") -> None:
        if self.enabled:
            self.update(1.0, detail)
            if self._line_open:
                self.stream.write("\n")
                self.stream.flush()
                self._line_open = False
            return
        suffix = f" ({detail})" if detail else ""
        print(f"[{self._current_stage}/{self.total_stages}] {self._current_label} complete{suffix}", file=self.stream)

    def fail(self) -> None:
        if self.enabled and self._line_open:
            self.stream.write("\n")
            self.stream.flush()
            self._line_open = False


def _run_ffmpeg_with_progress(
    cmd: list[str],
    *,
    total_seconds: float,
    progress: ProgressBar,
    stage: int,
    label: str,
) -> None:
    progress.start(stage, label)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stderr_lines: list[str] = []
    current_seconds = 0.0
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)
            entry = line.strip()
            if "=" not in entry:
                continue
            key, value = entry.split("=", 1)
            if key == "out_time":
                current_seconds = _parse_ffmpeg_timestamp(value)
                detail = f"{_format_seconds(current_seconds)}/{_format_seconds(total_seconds)}"
                fraction = current_seconds / total_seconds if total_seconds > 0 else 0.0
                progress.update(min(fraction, 0.99), detail)
    finally:
        return_code = proc.wait()
    if return_code != 0:
        progress.fail()
        details = "".join(stderr_lines).strip()
        if details:
            raise RuntimeError(f"command failed: {' '.join(cmd)}\n{details}")
        raise RuntimeError(f"command failed: {' '.join(cmd)}")
    progress.finish(f"{_format_seconds(total_seconds)}/{_format_seconds(total_seconds)}")


def _estimate_y4m_bytes(probe: ProbeInfo) -> int:
    frame_bytes = (probe.width * probe.height * 3) // 2
    return 128 + probe.frame_count * (frame_bytes + len("FRAME\n"))


def _estimate_y4m_frames(size_bytes: int, probe: ProbeInfo) -> int:
    frame_packet_bytes = ((probe.width * probe.height * 3) // 2) + len("FRAME\n")
    payload = max(0, size_bytes - 128)
    if frame_packet_bytes <= 0:
        return 0
    return min(probe.frame_count, payload // frame_packet_bytes)


def _run_avs2pipemod_with_progress(
    cmd: list[str],
    *,
    y4m_path: Path,
    probe: ProbeInfo,
    progress: ProgressBar,
    stage: int,
    label: str,
    env: dict[str, str] | None = None,
) -> None:
    progress.start(stage, label)
    expected_bytes = max(1, _estimate_y4m_bytes(probe))
    with y4m_path.open("wb") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.PIPE, env=env)
        stderr_data = b""
        try:
            while True:
                return_code = proc.poll()
                size_bytes = y4m_path.stat().st_size if y4m_path.exists() else 0
                frame_count = _estimate_y4m_frames(size_bytes, probe)
                fraction = size_bytes / expected_bytes
                if return_code is None:
                    progress.update(min(fraction, 0.99), f"{frame_count}/{probe.frame_count} frames")
                    time.sleep(0.2)
                    continue
                stderr_data = proc.stderr.read() if proc.stderr else b""
                break
        finally:
            return_code = proc.wait()
    if return_code != 0:
        progress.fail()
        details = _decode_stderr(stderr_data).strip()
        if details:
            raise RuntimeError(f"command failed: {' '.join(cmd)}\n{details}")
        raise RuntimeError(f"command failed: {' '.join(cmd)}")
    progress.finish(f"{probe.frame_count}/{probe.frame_count} frames")


def extract_frames(
    ffmpeg: Path,
    input_path: Path,
    frames_dir: Path,
    *,
    probe: ProbeInfo,
    progress: ProgressBar,
    stage: int,
) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg_with_progress(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostats",
            "-progress",
            "pipe:2",
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
        ],
        total_seconds=probe.duration_seconds,
        progress=progress,
        stage=stage,
        label="Extracting frames",
    )


def _even_floor(value: int) -> int:
    return value if value % 2 == 0 else value - 1


def _even_ceil(value: int) -> int:
    return value if value % 2 == 0 else value + 1


def mask_alpha_bbox(mask_path: Path) -> tuple[int, int, int, int]:
    data = mask_path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"mask must be a PNG with alpha: {mask_path}")

    offset = 8
    width = height = color_type = None
    idat = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        offset += length + 12
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, *_ = struct.unpack(">IIBBBBB", chunk)
            if bit_depth != 8 or color_type not in (4, 6):
                raise SystemExit("mask PNG must be 8-bit RGBA or LA")
        elif chunk_type == b"IDAT":
            idat.extend(chunk)
        elif chunk_type == b"IEND":
            break
    if width is None or height is None or color_type is None:
        raise SystemExit(f"invalid PNG: {mask_path}")

    raw = zlib.decompress(bytes(idat))
    channels = 4 if color_type == 6 else 2
    stride = width * channels
    prev = bytearray(stride)
    min_x, min_y = width, height
    max_x = max_y = -1
    idx = 0

    def paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa = abs(p - a)
        pb = abs(p - b)
        pc = abs(p - c)
        if pa <= pb and pa <= pc:
            return a
        if pb <= pc:
            return b
        return c

    for y in range(height):
        filter_type = raw[idx]
        idx += 1
        scan = bytearray(raw[idx : idx + stride])
        idx += stride
        if filter_type == 1:
            for j in range(stride):
                left = scan[j - channels] if j >= channels else 0
                scan[j] = (scan[j] + left) & 0xFF
        elif filter_type == 2:
            for j in range(stride):
                scan[j] = (scan[j] + prev[j]) & 0xFF
        elif filter_type == 3:
            for j in range(stride):
                left = scan[j - channels] if j >= channels else 0
                up = prev[j]
                scan[j] = (scan[j] + ((left + up) >> 1)) & 0xFF
        elif filter_type == 4:
            for j in range(stride):
                left = scan[j - channels] if j >= channels else 0
                up = prev[j]
                up_left = prev[j - channels] if j >= channels else 0
                scan[j] = (scan[j] + paeth(left, up, up_left)) & 0xFF
        prev = scan

        alpha_offset = 3 if color_type == 6 else 1
        for x in range(width):
            alpha = scan[x * channels + alpha_offset]
            if alpha:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y

    if max_x < 0 or max_y < 0:
        raise SystemExit("mask alpha is empty")
    return min_x, min_y, max_x, max_y


def build_loc_from_mask(mask_path: Path, *, pad: int = 16) -> str:
    min_x, min_y, max_x, max_y = mask_alpha_bbox(mask_path)
    left = _even_floor(max(0, min_x - pad))
    top = _even_floor(max(0, min_y - pad))
    right = _even_ceil(max_x + 1 + pad)
    bottom = _even_ceil(max_y + 1 + pad)
    width = max(2, right - left)
    height = max(2, bottom - top)
    return f"{left},{top},{width},{height}"


def extract_alpha_mask(ffmpeg: Path, mask_path: Path, bmp_path: Path) -> None:
    _run([
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(mask_path),
        "-vf",
        "alphaextract,format=gray",
        "-frames:v",
        "1",
        str(bmp_path),
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


def _make_inpaintdelogo_script(
    *,
    script_path: Path,
    input_frames: Path,
    mask_bmp: Path,
    runtime_image_seq: Path,
    runtime_avsinpaint: Path,
    runtime_neo_fft3d: Path,
    runtime_rt_stats: Path,
    runtime_grunt: Path,
    runtime_rgtools: Path,
    runtime_masktools: Path,
    runtime_inpaintdelogo: Path,
    probe: ProbeInfo,
    loc: str,
    radius: float,
    sharpness: float,
    preblur: float,
    postblur: float,
) -> None:
    frame_pattern = avi_string(input_frames / "%06d.png")
    mask_file = avi_string(mask_bmp)
    image_seq_dll = avi_string(runtime_image_seq)
    avsinpaint_dll = avi_string(runtime_avsinpaint)
    neo_fft3d_dll = avi_string(runtime_neo_fft3d)
    rt_stats_dll = avi_string(runtime_rt_stats)
    grunt_dll = avi_string(runtime_grunt)
    rgtools_dll = avi_string(runtime_rgtools)
    masktools_dll = avi_string(runtime_masktools)
    inpaintdelogo_avsi = avi_string(runtime_inpaintdelogo)
    script = f'''LoadPlugin("{image_seq_dll}")
LoadCPlugin("{avsinpaint_dll}")
LoadPlugin("{neo_fft3d_dll}")
LoadPlugin("{rt_stats_dll}")
LoadPlugin("{grunt_dll}")
LoadPlugin("{rgtools_dll}")
LoadPlugin("{masktools_dll}")
Import("{inpaintdelogo_avsi}")

src = ImageSource("{frame_pattern}", start=0, end={probe.frame_count - 1}, fps={probe.fps:.10f}, use_DevIL=true, info=false, pixel_type="rgb24")
out = InpaintDelogo(src, mask="{mask_file}", Loc="{loc}", Mode="Inpaint", Analyze=2, Automask=0, Radius={radius:.3f}, Sharpness={sharpness:.3f}, PreBlur={preblur:.3f}, PostBlur={postblur:.3f}, Optimize1=0, Greyscale=0)
out = out.ConvertToYV12(matrix="Rec709")
out
'''
    script_path.write_text(script, encoding="utf-8")


def encode_video(
    ffmpeg: Path,
    avs2pipemod: Path,
    avs_dll: Path,
    script_path: Path,
    y4m_path: Path,
    video_path: Path,
    *,
    probe: ProbeInfo,
    progress: ProgressBar,
    render_stage: int,
    encode_stage: int,
    extra_path_dirs: list[Path] | None = None,
) -> None:
    env = None
    if extra_path_dirs:
        env = os.environ.copy()
        path_parts = [str(p) for p in extra_path_dirs if p]
        env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
    _run_avs2pipemod_with_progress(
        [
            str(avs2pipemod),
            f"-dll={avi_string(avs_dll)}",
            "-y4mp",
            str(script_path),
        ],
        y4m_path=y4m_path,
        probe=probe,
        progress=progress,
        stage=render_stage,
        label="Rendering script",
        env=env,
    )
    _run_ffmpeg_with_progress(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostats",
            "-progress",
            "pipe:2",
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
        ],
        total_seconds=probe.duration_seconds,
        progress=progress,
        stage=encode_stage,
        label="Encoding video",
    )


def remux_audio(
    ffmpeg: Path,
    video_path: Path,
    input_path: Path,
    output_path: Path,
    *,
    probe: ProbeInfo,
    progress: ProgressBar,
    stage: int,
) -> None:
    _run_ffmpeg_with_progress(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostats",
            "-progress",
            "pipe:2",
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
        ],
        total_seconds=probe.duration_seconds,
        progress=progress,
        stage=stage,
        label="Remuxing audio",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auraeffect")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--mask", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engine", choices=(ENGINE_AUTO, ENGINE_AVSINPAINT, ENGINE_INPAINTDELOGO), default=ENGINE_AUTO)
    parser.add_argument("--radius", type=float, default=5.0)
    parser.add_argument("--sharpness", type=float, default=30.0)
    parser.add_argument("--preblur", type=float, default=0.5)
    parser.add_argument("--postblur", type=float, default=4.0)
    parser.add_argument("--keep-workdir", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = discover_runtime()
    progress = ProgressBar(total_stages=4)

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
        engine = args.engine
        if engine == ENGINE_AUTO:
            if runtime.inpaintdelogo_avsi and runtime.grunt_dll and runtime.rgtools_dll and runtime.rt_stats_dll:
                engine = ENGINE_INPAINTDELOGO
            else:
                engine = ENGINE_AVSINPAINT

        extract_frames(runtime.ffmpeg, input_path, frames_dir, probe=probe, progress=progress, stage=1)
        if engine == ENGINE_AVSINPAINT:
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
        else:
            missing = [
                name
                for name, value in (
                    ("grunt.dll", runtime.grunt_dll),
                    ("RgTools.dll", runtime.rgtools_dll),
                    ("neo-fft3d.dll", runtime.neo_fft3d_dll),
                    ("RT_Stats.dll", runtime.rt_stats_dll),
                    ("InpaintDelogo.avsi", runtime.inpaintdelogo_avsi),
                )
                if value is None
            ]
            if missing:
                raise SystemExit(
                    "InpaintDelogo is not ready yet; missing: "
                    + ", ".join(missing)
                    + ". Install the plugins, or use --engine avsinpaint for the current fallback."
                )
            loc = build_loc_from_mask(mask_path)
            mask_bmp = work / "mask.bmp"
            extract_alpha_mask(runtime.ffmpeg, mask_path, mask_bmp)
            _make_inpaintdelogo_script(
                script_path=script_path,
                input_frames=frames_dir,
                mask_bmp=mask_bmp,
                runtime_image_seq=runtime.image_seq_dll,
                runtime_avsinpaint=runtime.avsinpaint_dll,
                runtime_neo_fft3d=runtime.neo_fft3d_dll,
                runtime_rt_stats=runtime.rt_stats_dll,
                runtime_grunt=runtime.grunt_dll,
                runtime_rgtools=runtime.rgtools_dll,
                runtime_masktools=runtime.masktools_dll,
                runtime_inpaintdelogo=runtime.inpaintdelogo_avsi,
                probe=probe,
                loc=loc,
                radius=args.radius,
                sharpness=args.sharpness,
                preblur=args.preblur,
                postblur=args.postblur,
            )
        extra_path_dirs = [runtime.neo_fft3d_dll.parent] if engine == ENGINE_INPAINTDELOGO and runtime.neo_fft3d_dll else None
        encode_video(
            runtime.ffmpeg,
            runtime.avs2pipemod,
            runtime.avisynth_dll,
            script_path,
            y4m_path,
            encoded_path,
            probe=probe,
            progress=progress,
            render_stage=2,
            encode_stage=3,
            extra_path_dirs=extra_path_dirs,
        )
        remux_audio(runtime.ffmpeg, encoded_path, input_path, output_path, probe=probe, progress=progress, stage=4)
        if args.keep_workdir:
            preserved = output_path.with_suffix(".work")
            preserved.parent.mkdir(parents=True, exist_ok=True)
            if preserved.exists():
                shutil.rmtree(preserved)
            shutil.copytree(tmp, preserved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
