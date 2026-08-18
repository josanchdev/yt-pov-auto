from __future__ import annotations

import pytest

from src.config import load_config, load_species
from src.errors import ConfigError


def test_queues_are_distinct_devices(cfg):
    assert len(cfg.queues) == 2
    indices = {q.device_index for q in cfg.queues}
    assert indices == {0, 1}, "the two queues must be pinned to different physical cards"
    assert cfg.queue("hero").vram_gb > cfg.queue("bulk").vram_gb


def test_unknown_queue_raises(cfg):
    with pytest.raises(ConfigError):
        cfg.queue("nope")


def test_missing_host_env_is_a_config_error(monkeypatch, tmp_path):
    monkeypatch.delenv("COMFYUI_HOST", raising=False)
    monkeypatch.setenv("COMFYUI_HOST_SECONDARY", "http://127.0.0.1:18189")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path))
    import src.config as config_mod

    monkeypatch.setattr(config_mod, "load_dotenv", lambda path=None: None)
    with pytest.raises(ConfigError, match="COMFYUI_HOST"):
        load_config()


def test_disclosure_cannot_be_disabled(tmp_path, monkeypatch):
    """Hard rule 3: config that turns disclosure off must not load."""
    monkeypatch.setenv("COMFYUI_HOST", "http://x")
    monkeypatch.setenv("COMFYUI_HOST_SECONDARY", "http://y")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path))
    import src.config as config_mod

    monkeypatch.setattr(config_mod, "load_dotenv", lambda path=None: None)
    source = (config_mod.REPO_ROOT / "config" / "pipeline.toml").read_text()
    bad = tmp_path / "bad.toml"
    bad.write_text(
        source.replace("youtube_altered_content = true", "youtube_altered_content = false")
    )
    with pytest.raises(ConfigError, match="disclosure"):
        load_config(bad)


def test_m0_species_loads(cfg):
    species = load_species("desert_tortoise", cfg)
    assert species["id"] == "desert_tortoise"
    for beat in ("open", "travel", "encounter", "tension", "reward", "close"):
        assert len(species["beats"][beat]) >= 4, f"beat pool {beat} too thin for variety rules"
