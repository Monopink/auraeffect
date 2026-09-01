# auraeffect

Local CLI tool for removing a fixed watermark area from a video with a mask image.

## Requirements

- Python 3.11+
- local runtime files already prepared under `.runtime/`
- input video
- mask image with alpha channel and the same resolution as the video

## Install

```powershell
.venv\Scripts\python.exe -m pip install -e .
```

## Usage

```powershell
.venv\Scripts\python.exe -m auraeffect --input input.mov --mask mask.png --output output.mp4
```

## Parameters

- `--input`: source video path
- `--mask`: mask image path
- `--output`: output video path
- `--radius`: inpaint radius, default `5.0`
- `--sharpness`: inpaint sharpness, default `30.0`
- `--preblur`: pre blur, default `0.5`
- `--postblur`: post blur, default `4.0`
- `--keep-workdir`: keep extracted frames and temporary work files next to the output as `*.work`

## Notes

- the tool keeps the original video duration unchanged
- if the source has an audio stream, audio is copied to the output
- the current mainline implementation is the simple `AVSInpaint` pipeline
