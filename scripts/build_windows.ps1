$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$DistRoot = Join-Path $ProjectRoot "dist\Auraeffect"
$LegacyExe = Join-Path $ProjectRoot "dist\Auraeffect.exe"

# PyInstaller uses this intermediate executable before collecting the folder build.
if (Test-Path -LiteralPath $LegacyExe) {
    Remove-Item -LiteralPath $LegacyExe -Force
}

python -m pip install pyinstaller
python -m PyInstaller (Join-Path $ProjectRoot "packaging\auraeffect.spec") --clean --noconfirm

$RuntimeOutput = Join-Path $DistRoot "runtime"
New-Item -ItemType Directory -Force -Path $RuntimeOutput | Out-Null

function Copy-RuntimeFile($Source, $RelativeTarget) {
    $Target = Join-Path $RuntimeOutput $RelativeTarget
    New-Item -ItemType Directory -Force -Path (Split-Path $Target) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Target -Force
}

$AviRoot = Join-Path $RuntimeRoot "avisynthplus\full\AviSynthPlus_3.7.5_20250420-filesonly\x64"
Copy-RuntimeFile (Join-Path $RuntimeRoot "ffmpeg\ffmpeg-9.0.1-essentials_build\bin\ffmpeg.exe") "ffmpeg\bin\ffmpeg.exe"
Copy-RuntimeFile (Join-Path $RuntimeRoot "ffmpeg\ffmpeg-9.0.1-essentials_build\bin\ffprobe.exe") "ffmpeg\bin\ffprobe.exe"
Copy-RuntimeFile (Join-Path $RuntimeRoot "avs2pipemod\avs2pipemod64.exe") "avs2pipemod\avs2pipemod64.exe"
Copy-RuntimeFile (Join-Path $AviRoot "AviSynth.dll") "avisynthplus\x64\AviSynth.dll"
Copy-RuntimeFile (Join-Path $AviRoot "plugins\ImageSeq.dll") "avisynthplus\x64\plugins\ImageSeq.dll"
Copy-RuntimeFile (Join-Path $AviRoot "plugins\x64\AvsInPaint.dll") "avisynthplus\x64\plugins\x64\AvsInPaint.dll"
Copy-RuntimeFile (Join-Path $AviRoot "plugins\x64\masktools2.dll") "avisynthplus\x64\plugins\x64\masktools2.dll"

Write-Host "Built: $DistRoot\Auraeffect.exe"
