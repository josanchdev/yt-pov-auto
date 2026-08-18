from __future__ import annotations

import pytest

from src.prompts import structures
from src.prompts.generator import (
    MAX_BEAT_OVERLAP,
    STRUCTURE_COOLDOWN,
    VarietyError,
    plan_episode,
)
from src.prompts.ledger import Ledger, LedgerEntry, beat_overlap


def test_structures_are_well_formed():
    assert len(structures.STRUCTURES) >= 4
    for s in structures.STRUCTURES:
        assert s.length >= 8, "M0 criterion 3 needs at least 8 clips"
        assert len(s.pacing) == s.length
        assert s.beats[0] == "open" and s.beats[-1] == "close"


def test_plan_has_no_repeated_beat_text(cfg, tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    plan = plan_episode("desert_tortoise", config=cfg, ledger=ledger, episode_id="ep1")
    texts = [s.beat_text for s in plan.shots]
    assert len(texts) == len(set(texts))
    assert len(plan.shots) >= cfg.assembly.min_clips


def test_plan_is_deterministic_for_an_episode_id(cfg, tmp_path):
    a = plan_episode(
        "desert_tortoise", config=cfg, ledger=Ledger(tmp_path / "a.jsonl"), episode_id="ep_same"
    )
    b = plan_episode(
        "desert_tortoise", config=cfg, ledger=Ledger(tmp_path / "b.jsonl"), episode_id="ep_same"
    )
    assert a.structure == b.structure
    assert [s.prompt for s in a.shots] == [s.prompt for s in b.shots]
    assert [s.seed for s in a.shots] == [s.seed for s in b.shots]


def test_prompt_carries_species_base_and_beat(cfg, tmp_path):
    plan = plan_episode(
        "desert_tortoise", config=cfg, ledger=Ledger(tmp_path / "l.jsonl"), episode_id="ep2"
    )
    for shot in plan.shots:
        assert "first-person POV from a desert tortoise" in shot.prompt
        assert shot.beat_text in shot.prompt
        assert shot.negative_prompt


def test_structure_cooldown_is_enforced(cfg, tmp_path):
    """Hard rule 2: consecutive episodes may not reuse the same structure."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    plans = [
        plan_episode("desert_tortoise", config=cfg, ledger=ledger, episode_id=f"ep{i}")
        for i in range(4)
    ]
    for i in range(1, len(plans)):
        window = {p.structure for p in plans[max(0, i - STRUCTURE_COOLDOWN) : i]}
        assert plans[i].structure not in window


def test_beat_overlap_limit_is_enforced(cfg, tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    plans = [
        plan_episode("desert_tortoise", config=cfg, ledger=ledger, episode_id=f"ep{i}")
        for i in range(4)
    ]
    for i, a in enumerate(plans):
        for b in plans[i + 1 :]:
            overlap = beat_overlap(
                tuple(s.beat_text for s in a.shots), tuple(s.beat_text for s in b.shots)
            )
            assert overlap <= MAX_BEAT_OVERLAP


def test_planner_refuses_rather_than_shipping_a_clone(cfg, tmp_path, monkeypatch):
    """When variety is impossible, it must raise -- never relax and emit a near-duplicate."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    monkeypatch.setattr("src.prompts.generator.MAX_BEAT_OVERLAP", 0.0)
    monkeypatch.setattr("src.prompts.generator.STRUCTURE_COOLDOWN", 99)
    plan_episode("desert_tortoise", config=cfg, ledger=ledger, episode_id="ep_a")
    with pytest.raises(VarietyError):
        for i in range(20):
            plan_episode("desert_tortoise", config=cfg, ledger=ledger, episode_id=f"ep_b{i}")


def test_hero_beats_route_to_the_quality_queue(cfg, tmp_path):
    plan = plan_episode(
        "desert_tortoise", config=cfg, ledger=Ledger(tmp_path / "l.jsonl"), episode_id="ep3"
    )
    queues = {s.queue for s in plan.shots}
    assert queues <= {"hero", "bulk"}
    assert "hero" in queues, "an episode with no hero shots is not using the 5090"


def test_ledger_roundtrip(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    entry = LedgerEntry("ep", "sp", "journey", ("a", "b"), "noon")
    ledger.append(entry)
    assert ledger.entries("sp") == [entry]
    assert ledger.entries("other") == []


def test_shot_durations_stay_inside_the_qc_band(cfg, tmp_path):
    """A shot QC would reject on duration alone is a planner bug, not a QC failure."""
    ledger = Ledger(tmp_path / "l.jsonl")
    for i in range(3):
        plan = plan_episode(
            "desert_tortoise", config=cfg, ledger=ledger, episode_id=f"dur{i}"
        )
        for shot in plan.shots:
            assert cfg.qc.min_duration_s <= shot.duration_s <= cfg.qc.max_duration_s, (
                f"shot {shot.index} at {shot.duration_s}s is outside the QC band"
            )


def test_episode_runtime_lands_in_the_assembly_band(cfg, tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    plan = plan_episode("desert_tortoise", config=cfg, ledger=ledger, episode_id="len1")
    assert cfg.assembly.min_duration_s <= plan.total_duration_s <= cfg.assembly.max_duration_s


def test_unreachable_target_runtime_is_a_config_error(cfg):
    from src.errors import ConfigError
    from src.prompts.generator import _durations

    structure = structures.STRUCTURES[0]
    with pytest.raises(ConfigError, match="cannot be split"):
        _durations(structure, 500.0, 4.0, 12.0)
