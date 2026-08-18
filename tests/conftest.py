from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from src import config as config_mod

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A Config rooted at a temp output dir, with both ComfyUI hosts stubbed."""
    monkeypatch.setenv("COMFYUI_HOST", "http://127.0.0.1:18188")
    monkeypatch.setenv("COMFYUI_HOST_SECONDARY", "http://127.0.0.1:18189")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "output"))
    monkeypatch.setattr(config_mod, "load_dotenv", lambda path=None: None)
    c = config_mod.load_config(REPO_ROOT / "config" / "pipeline.toml")
    c.paths.ensure()
    return c


@pytest.fixture
def has_ffmpeg():
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def make_clip(path: Path, *, seconds: float = 8.0, size: str = "1280x704", static: bool = False):
    """Render a synthetic test clip with ffmpeg."""
    import subprocess

    source = f"color=c=gray:s={size}:d={seconds}" if static else f"testsrc=size={size}:d={seconds}"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"{source}:r=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


os.environ.setdefault("LOG_LEVEL", "WARNING")
