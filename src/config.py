"""Configuration loading.

Everything tunable lives in config/*.toml. src/ reads it; src/ never inlines it.
Environment variables (from .env, exported by the shell) supply hosts and paths only.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "pipeline.toml"


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader. Does not overwrite variables already in the environment."""
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class GPUQueue:
    """One ComfyUI process pinned to one physical card.

    Never treat two of these as a single logical device -- see CLAUDE.md/Hardware.
    """

    name: str
    device_index: int
    gpu: str
    vram_gb: int
    host: str
    model: str
    workflow: str
    max_resolution: tuple[int, int]
    notes: str = ""

    @property
    def device(self) -> str:
        return f"cuda:{self.device_index} ({self.gpu})"


@dataclass(frozen=True)
class GenerationConfig:
    poll_interval_s: float
    job_timeout_s: int
    fps: int


@dataclass(frozen=True)
class QCConfig:
    min_duration_s: float
    max_duration_s: float
    min_width: int
    min_height: int
    max_black_frame_ratio: float
    max_static_frame_ratio: float
    min_motion_score: float
    max_motion_score: float
    frame_sample_count: int
    reject_rate_alarm: float


@dataclass(frozen=True)
class AssemblyConfig:
    width: int
    height: int
    fps: int
    video_codec: str
    crf: int
    preset: str
    audio_codec: str
    audio_bitrate: str
    transition_s: float
    min_clips: int
    min_duration_s: float
    max_duration_s: float


@dataclass(frozen=True)
class DisclosureConfig:
    youtube_altered_content: bool
    tiktok_aigc_label: bool
    description_line: str


@dataclass(frozen=True)
class Paths:
    output_root: Path
    workflows: Path
    species: Path

    @property
    def raw(self) -> Path:
        return self.output_root / "raw"

    @property
    def approved(self) -> Path:
        return self.output_root / "approved"

    @property
    def rejected(self) -> Path:
        return self.output_root / "rejected"

    @property
    def final(self) -> Path:
        return self.output_root / "final"

    def ensure(self) -> None:
        for p in (self.raw, self.approved, self.rejected, self.final):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Config:
    paths: Paths
    queues: tuple[GPUQueue, ...]
    generation: GenerationConfig
    qc: QCConfig
    assembly: AssemblyConfig
    disclosure: DisclosureConfig
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def queue(self, name: str) -> GPUQueue:
        for q in self.queues:
            if q.name == name:
                return q
        known = ", ".join(q.name for q in self.queues)
        raise ConfigError(f"unknown queue {name!r}; configured queues: {known}")


def _resolve(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def load_config(path: Path | None = None) -> Config:
    load_dotenv()
    data = _load_toml(path or DEFAULT_CONFIG)

    try:
        paths_raw = data["paths"]
        paths = Paths(
            output_root=_resolve(os.environ.get("OUTPUT_ROOT") or paths_raw["output_root"]),
            workflows=_resolve(paths_raw["workflows"]),
            species=_resolve(paths_raw["species"]),
        )

        queues: list[GPUQueue] = []
        for entry in data["gpu"]:
            host = os.environ.get(entry["host_env"])
            if not host:
                raise ConfigError(
                    f"queue {entry['name']!r} needs {entry['host_env']} set "
                    f"(copy .env.example to .env and fill it in)"
                )
            w, h = entry["max_resolution"]
            queues.append(
                GPUQueue(
                    name=entry["name"],
                    device_index=entry["device_index"],
                    gpu=entry["gpu"],
                    vram_gb=entry["vram_gb"],
                    host=host.rstrip("/"),
                    model=entry["model"],
                    workflow=entry["workflow"],
                    max_resolution=(int(w), int(h)),
                    notes=entry.get("notes", ""),
                )
            )
        if not queues:
            raise ConfigError("no [[gpu]] queues configured")

        cfg = Config(
            paths=paths,
            queues=tuple(queues),
            generation=GenerationConfig(**data["generation"]),
            qc=QCConfig(**data["qc"]),
            assembly=AssemblyConfig(**data["assembly"]),
            disclosure=DisclosureConfig(**data["disclosure"]),
            raw=data,
        )
    except KeyError as exc:
        raise ConfigError(f"missing config key: {exc}") from exc
    except TypeError as exc:
        raise ConfigError(f"bad config shape: {exc}") from exc

    if not cfg.disclosure.youtube_altered_content or not cfg.disclosure.tiktok_aigc_label:
        raise ConfigError(
            "AI disclosure cannot be disabled in config (CLAUDE.md hard rule 3). "
            "Both youtube_altered_content and tiktok_aigc_label must stay true."
        )
    return cfg


def load_species(species_id: str, config: Config | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    path = cfg.paths.species / f"{species_id}.toml"
    if not path.is_file():
        available = sorted(p.stem for p in cfg.paths.species.glob("*.toml"))
        raise ConfigError(f"unknown species {species_id!r}; available: {', '.join(available)}")
    return _load_toml(path)


if __name__ == "__main__":
    import json

    from src.logging_setup import get_logger

    log = get_logger("config")
    c = load_config()
    log.info(
        "config.loaded",
        output_root=str(c.paths.output_root),
        queues=[
            {"name": q.name, "device": q.device, "model": q.model, "host": q.host} for q in c.queues
        ],
    )
    print(json.dumps({"species": sorted(p.stem for p in c.paths.species.glob("*.toml"))}, indent=2))
