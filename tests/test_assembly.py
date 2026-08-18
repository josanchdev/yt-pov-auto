from __future__ import annotations

import json

import pytest

from src.assembly import disclosure as disclosure_mod
from src.assembly.build import assemble, collect
from src.errors import AssemblyError, DisclosureError
from tests.conftest import make_clip


def _approved(cfg, count, seconds=8.0):
    for i in range(count):
        clip = make_clip(cfg.paths.approved / f"ep_{i:02d}.mp4", seconds=seconds)
        (cfg.paths.approved / f"ep_{i:02d}.json").write_text(
            json.dumps(
                {
                    "clip_id": f"ep_{i:02d}",
                    "shot_index": i,
                    "model": "wan2.2-ti2v-5b",
                    "qc": {"approved": True, "stage": "manual"},
                }
            )
        )
        assert clip.is_file()


def _needs_ffmpeg(has_ffmpeg):
    if not has_ffmpeg:
        pytest.skip("ffmpeg/ffprobe not installed")


def test_collect_orders_by_shot_index(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    _approved(cfg, 3)
    clips = collect(cfg, "ep")
    assert [c.shot_index for c in clips] == [0, 1, 2]


def test_assembly_refuses_below_minimum_clip_count(cfg, has_ffmpeg):
    """M0 criterion 3: at least 8 distinct clips."""
    _needs_ffmpeg(has_ffmpeg)
    _approved(cfg, 3)
    with pytest.raises(AssemblyError, match="criterion 3"):
        assemble("ep", config=cfg, species_id="desert_tortoise")


def test_assembly_refuses_when_nothing_is_approved(cfg, has_ffmpeg):
    """Hard rule 1: assembly never falls back to output/raw/."""
    _needs_ffmpeg(has_ffmpeg)
    make_clip(cfg.paths.raw / "ep_00.mp4", seconds=8)
    with pytest.raises(AssemblyError, match="no approved clips"):
        assemble("ep", config=cfg, species_id="desert_tortoise")


def test_assembly_rejects_a_hand_moved_failing_clip(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    _approved(cfg, 8)
    path = cfg.paths.approved / "ep_03.json"
    meta = json.loads(path.read_text())
    meta["qc"]["approved"] = False
    path.write_text(json.dumps(meta))
    with pytest.raises(AssemblyError, match="rejecting QC verdict"):
        assemble("ep", config=cfg, species_id="desert_tortoise")


@pytest.mark.slow
def test_full_render_is_disclosed(cfg, has_ffmpeg):
    """Hard rule 3 + M0 criteria 1 and 3."""
    _needs_ffmpeg(has_ffmpeg)
    _approved(cfg, 8, seconds=8.0)
    output = assemble("ep", config=cfg, species_id="desert_tortoise")

    assert output.is_file() and output.parent == cfg.paths.final
    from src.media import probe

    info = probe(output)
    assert info.width == cfg.assembly.width and info.height == cfg.assembly.height
    assert info.has_audio, "the finished file must carry an audio track"
    assert 60 <= info.duration_s <= 90, f"M0 criterion 1 wants 60-90s, got {info.duration_s:.1f}s"

    sidecar = json.loads(disclosure_mod.sidecar_path(output).read_text())
    assert sidecar["ai_generated"] is True
    assert sidecar["platform_labels"]["youtube_altered_content"] is True
    assert sidecar["platform_labels"]["tiktok_aigc_label"] is True
    assert "wan2.2-ti2v-5b" in sidecar["models"]
    disclosure_mod.verify(output)


def test_verify_raises_without_a_sidecar(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    clip = make_clip(cfg.paths.final / "undisclosed.mp4", seconds=2)
    with pytest.raises(DisclosureError, match="disclosure sidecar"):
        disclosure_mod.verify(clip)


def test_verify_raises_when_the_sidecar_denies_disclosure(cfg, has_ffmpeg):
    _needs_ffmpeg(has_ffmpeg)
    clip = make_clip(cfg.paths.final / "lying.mp4", seconds=2)
    disclosure_mod.sidecar_path(clip).write_text(
        json.dumps({"ai_generated": False, "platform_labels": {}})
    )
    with pytest.raises(DisclosureError):
        disclosure_mod.verify(clip)
