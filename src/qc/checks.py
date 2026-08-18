"""Automated clip checks.

These catch the failures that are cheap to catch: wrong duration, wrong resolution,
a clip that went black, a clip that froze, a clip that is thrashing between unrelated
frames (the usual signature of temporal collapse in local video models).

What they explicitly do NOT catch is whether the clip looks real. That is M0
criterion 4 and it stays a human call -- see src/qc/gate.py.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.config import QCConfig
from src.errors import PipelineError
from src.media import MediaInfo, probe, require

SAMPLE_WIDTH = 64
SAMPLE_HEIGHT = 36
BLACK_LUMA = 16.0
STATIC_DELTA = 0.5


@dataclass
class CheckReport:
    clip_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def fail(self, reason: str) -> None:
        self.passed = False
        self.failures.append(reason)


def sample_luma(path: Path, count: int) -> np.ndarray:
    """Return `count`-ish evenly spaced grayscale frames as a (n, h, w) float array."""
    require("ffmpeg")
    info = probe(path)
    if info.duration_s <= 0:
        raise PipelineError(f"{path.name} reports zero duration; cannot sample frames")
    rate = max(count / info.duration_s, 0.1)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={rate:.4f},scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PipelineError(f"frame sampling failed on {path.name}: {proc.stderr.decode()[-400:]}")
    frame_size = SAMPLE_WIDTH * SAMPLE_HEIGHT
    usable = len(proc.stdout) - (len(proc.stdout) % frame_size)
    if usable < frame_size:
        raise PipelineError(f"{path.name} yielded no decodable frames")
    buffer = np.frombuffer(proc.stdout[:usable], dtype=np.uint8)
    return buffer.reshape(-1, SAMPLE_HEIGHT, SAMPLE_WIDTH).astype(np.float32)


def analyse(frames: np.ndarray) -> dict[str, float]:
    means = frames.reshape(frames.shape[0], -1).mean(axis=1)
    black_ratio = float((means < BLACK_LUMA).mean())
    if frames.shape[0] < 2:
        return {
            "black_frame_ratio": black_ratio,
            "motion_score": 0.0,
            "static_frame_ratio": 1.0,
            "frames_sampled": float(frames.shape[0]),
        }
    deltas = np.abs(np.diff(frames, axis=0)).reshape(frames.shape[0] - 1, -1).mean(axis=1)
    return {
        "black_frame_ratio": black_ratio,
        "motion_score": float(deltas.mean()),
        "static_frame_ratio": float((deltas < STATIC_DELTA).mean()),
        "frames_sampled": float(frames.shape[0]),
    }


def check_clip(path: Path, cfg: QCConfig, *, clip_id: str | None = None) -> CheckReport:
    report = CheckReport(clip_id=clip_id or path.stem, passed=True)
    info: MediaInfo = probe(path)
    report.metrics.update(
        {
            "duration_s": round(info.duration_s, 3),
            "width": float(info.width),
            "height": float(info.height),
            "fps": round(info.fps, 3),
        }
    )

    if not (cfg.min_duration_s <= info.duration_s <= cfg.max_duration_s):
        report.fail(
            f"duration {info.duration_s:.2f}s outside [{cfg.min_duration_s}, {cfg.max_duration_s}]s"
        )
    if info.width < cfg.min_width or info.height < cfg.min_height:
        report.fail(f"resolution {info.width}x{info.height} below {cfg.min_width}x{cfg.min_height}")

    metrics = analyse(sample_luma(path, cfg.frame_sample_count))
    report.metrics.update({k: round(v, 4) for k, v in metrics.items()})

    if metrics["black_frame_ratio"] > cfg.max_black_frame_ratio:
        report.fail(
            f"black frames {metrics['black_frame_ratio']:.0%} exceed "
            f"{cfg.max_black_frame_ratio:.0%}"
        )
    if metrics["static_frame_ratio"] > cfg.max_static_frame_ratio:
        report.fail(
            f"static frames {metrics['static_frame_ratio']:.0%} exceed "
            f"{cfg.max_static_frame_ratio:.0%} (clip likely frozen)"
        )
    if metrics["motion_score"] < cfg.min_motion_score:
        report.fail(f"motion score {metrics['motion_score']:.2f} below {cfg.min_motion_score}")
    if metrics["motion_score"] > cfg.max_motion_score:
        report.fail(
            f"motion score {metrics['motion_score']:.2f} above {cfg.max_motion_score} "
            f"(temporal incoherence, not motion)"
        )
    return report
