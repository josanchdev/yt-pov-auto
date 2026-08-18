"""ffmpeg/ffprobe helpers.

The pipeline shells out to ffmpeg rather than binding a library: it is the tool that
is actually installed on the WSL2 box, and its failures are legible in the log.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.errors import PipelineError


class FFmpegMissingError(PipelineError):
    def __init__(self, tool: str):
        super().__init__(
            f"{tool} not found on PATH. Install it inside WSL: sudo apt install ffmpeg"
        )


def require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise FFmpegMissingError(tool)
    return path


def available(tool: str = "ffmpeg") -> bool:
    return shutil.which(tool) is not None


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration_s: float
    width: int
    height: int
    fps: float
    has_audio: bool


def probe(path: Path) -> MediaInfo:
    require("ffprobe")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PipelineError(f"ffprobe failed on {path.name}: {proc.stderr.strip()[:400]}")
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise PipelineError(f"{path.name} has no video stream")

    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den) if float(den) else 0.0
    except ValueError:
        fps = 0.0
    return MediaInfo(
        path=path,
        duration_s=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def run(args: list[str], *, what: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise PipelineError(f"{what} failed: {proc.stderr.strip()[-800:]}")
    return proc
