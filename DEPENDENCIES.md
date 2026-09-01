# Dependencies

## Tracked in Git

These stay in the repository because they describe the project rather than bundling local binaries.

- `pyproject.toml`
- `src/auraeffect/`
- `scripts/`
- `.gitignore`
- `DEPENDENCIES.md`

## Mainline Runtime Dependencies

These are required for the current recommended pipeline based on the first implementation.

- Python 3.11+
- FFmpeg / FFprobe
- AviSynth+
- avs2pipemod
- AvsInpaint
- MaskTools2

## Experimental Runtime Dependencies

These were added only for the `InpaintDelogo` experiment and should not be treated as the default path.

- InpaintDelogo
- GRunT
- RgTools
- RT_Stats
- Neo_FFT3D
- FFTW3 runtime libraries
- 7-Zip or another extractor for some plugin archives

## Not Tracked in Git

These are machine-local artifacts and should remain outside version control.

- `.venv/`
- `.runtime/` extracted binaries and bundled tools
- downloaded archives such as `*.zip` and `*.7z`
- plugin binaries such as `*.dll` and helper tools such as `*.exe`
- local extracted plugin folders such as `RT_Stats_25&26_x86_x64_dll_v2.00Beta13_20201229/`
- input and output media such as `*.mov`, `*.mp4`, `*.png`
- temporary work directories such as `*.work/` and `*.y4m`
