from __future__ import annotations

import json

import pytest

from src.qc.checks import check_clip
from src.qc.gate import run_gate
from tests.conftest import make_clip

pytestmark = pytest.mark.usefixtures("cfg")


def _needs_ffmpeg(has_ffmpeg):
    if not has_ffmpeg:
        pytest.skip("ffmpeg/ffprobe not installed")


def _sidecar(cfg, clip_id, shot_index=0):
    (cfg.paths.raw / f"{clip_id}.json").write_text(
        json.dumps(
            {
                "clip_id": clip_id,
                "shot_index": shot_index,
                "model": "wan2.2-ti2v-5b",
                "seed": 1,
                "prompt": "p",
            }
        )
    )


def test_good_clip_passes_auto_checks(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    clip = make_clip(cfg.paths.raw / "ep_00.mp4", seconds=8)
    report = check_clip(clip, cfg.qc)
    assert report.passed, report.failures
    assert report.metrics["duration_s"] == pytest.approx(8, abs=0.3)


def test_frozen_clip_is_rejected(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    clip = make_clip(cfg.paths.raw / "ep_01.mp4", seconds=8, static=True)
    report = check_clip(clip, cfg.qc)
    assert not report.passed
    assert any("static" in f or "motion" in f for f in report.failures)


def test_too_short_clip_is_rejected(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    clip = make_clip(cfg.paths.raw / "ep_02.mp4", seconds=2)
    report = check_clip(clip, cfg.qc)
    assert not report.passed
    assert any("duration" in f for f in report.failures)


def test_too_small_clip_is_rejected(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    clip = make_clip(cfg.paths.raw / "ep_03.mp4", seconds=8, size="320x180")
    report = check_clip(clip, cfg.qc)
    assert not report.passed
    assert any("resolution" in f for f in report.failures)


def test_gate_moves_clips_and_records_verdicts(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    make_clip(cfg.paths.raw / "ep_00.mp4", seconds=8)
    _sidecar(cfg, "ep_00")
    make_clip(cfg.paths.raw / "ep_01.mp4", seconds=8, static=True)
    _sidecar(cfg, "ep_01", 1)

    summary = run_gate(config=cfg, episode_id="ep", auto_only=True)
    assert summary.total == 2
    assert summary.approved == 1 and summary.rejected == 1
    assert (cfg.paths.approved / "ep_00.mp4").is_file()
    assert (cfg.paths.rejected / "ep_01.mp4").is_file()
    assert not list(cfg.paths.raw.glob("*.mp4")), "gate must not leave clips in raw/"

    meta = json.loads((cfg.paths.approved / "ep_00.json").read_text())
    assert meta["qc"]["approved"] is True
    assert meta["seed"] == 1, "the generation sidecar must survive the move"


def test_auto_only_verdicts_are_marked_unreviewed(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    make_clip(cfg.paths.raw / "ep_00.mp4", seconds=8)
    summary = run_gate(config=cfg, episode_id="ep", auto_only=True)
    approved = [v for v in summary.verdicts if v.approved]
    assert approved and "not human reviewed" in approved[0].reviewer


def test_reject_rate_is_logged(cfg, has_ffmpeg):
    """M0 criterion 5."""
    _needs_ffmpeg(has_ffmpeg)
    make_clip(cfg.paths.raw / "ep_00.mp4", seconds=8, static=True)
    make_clip(cfg.paths.raw / "ep_01.mp4", seconds=8)
    run_gate(config=cfg, episode_id="ep", auto_only=True)

    log_path = cfg.paths.output_root / "qc_reject_rate.jsonl"
    assert log_path.is_file()
    record = json.loads(log_path.read_text().splitlines()[-1])
    assert record["total"] == 2 and record["rejected"] == 1
    assert record["reject_rate"] == pytest.approx(0.5)


def test_gate_on_empty_raw_is_a_noop(cfg):
    summary = run_gate(config=cfg, episode_id="nothing", auto_only=True)
    assert summary.total == 0 and summary.reject_rate == 0.0
