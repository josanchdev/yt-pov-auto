"""Episode planner.

Turns a species definition plus a narrative structure into an ordered list of
shots, each with its own prompt, seed, target duration and queue assignment.

Variety rules, enforced (hard rule 2):
  * the structure may not match any of the last STRUCTURE_COOLDOWN episodes
    for this species;
  * beat-text overlap with any prior episode of this species must stay below
    MAX_BEAT_OVERLAP;
  * within one episode, no beat text repeats.

If the planner cannot satisfy those, it raises rather than shipping a near-clone.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.config import Config, load_config, load_species
from src.errors import ConfigError, PipelineError
from src.logging_setup import get_logger
from src.prompts import structures
from src.prompts.ledger import Ledger, LedgerEntry, beat_overlap

log = get_logger("prompts")

STRUCTURE_COOLDOWN = 3
MAX_BEAT_OVERLAP = 0.5
MAX_PLAN_ATTEMPTS = 40
# Beats that carry the episode. These get the quality queue; the rest get bulk.
HERO_BEATS = frozenset({"encounter", "reward", "tension"})


class VarietyError(PipelineError):
    """The planner could not produce an episode distinct enough to ship."""


@dataclass(frozen=True)
class Shot:
    index: int
    beat: str
    beat_text: str
    queue: str
    duration_s: float
    seed: int
    prompt: str
    negative_prompt: str

    @property
    def clip_id(self) -> str:
        return f"{self.index:02d}"


@dataclass(frozen=True)
class EpisodePlan:
    episode_id: str
    species_id: str
    structure: str
    light: str
    created_at: str
    shots: tuple[Shot, ...]
    seed: int
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.shots)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["shots"] = [asdict(s) for s in self.shots]
        data["total_duration_s"] = round(self.total_duration_s, 2)
        return data

    def clip_id(self, shot: Shot) -> str:
        return f"{self.episode_id}_{shot.clip_id}"


def _episode_seed(episode_id: str, explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    digest = hashlib.sha256(episode_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _species_beats(species: dict[str, Any], beat: str) -> list[str]:
    pool = species.get("beats", {}).get(beat)
    if not pool:
        raise ConfigError(
            f"species {species.get('id')!r} has no beat pool {beat!r}; "
            f"add it under [beats] in the species config"
        )
    return list(pool)


def _compose_prompt(species: dict[str, Any], beat_text: str, light: str, camera_note: str) -> str:
    prompt_cfg = species.get("prompt", {})
    camera = species.get("camera", {})
    parts = [
        prompt_cfg.get("base", ""),
        beat_text,
        camera.get("lens", ""),
        camera_note,
        light,
        prompt_cfg.get("style", ""),
    ]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def _durations(
    structure: structures.Structure, target_s: float, min_s: float, max_s: float
) -> list[float]:
    """Spread `target_s` over the shots by pacing weight, inside the QC duration band.

    A pacing curve alone will hand a long tail shot 15s, which the QC gate then
    rejects for being out of band -- so weights are water-filled: clamp whatever hits
    a bound, redistribute the remainder over the rest, repeat.
    """
    n = structure.length
    if target_s > n * max_s or target_s < n * min_s:
        raise ConfigError(
            f"target runtime {target_s:.0f}s cannot be split into {n} clips of "
            f"{min_s}-{max_s}s. Adjust [assembly] min/max_duration_s or [qc] "
            f"min/max_duration_s in config/pipeline.toml so the two agree."
        )

    durations = [0.0] * n
    free = list(range(n))
    remaining = target_s
    while free:
        weight = sum(structure.pacing[i] for i in free)
        pinned: list[int] = []
        for i in free:
            share = remaining * structure.pacing[i] / weight
            if share > max_s:
                durations[i] = max_s
                pinned.append(i)
            elif share < min_s:
                durations[i] = min_s
                pinned.append(i)
            else:
                durations[i] = share
        if not pinned:
            break
        remaining -= sum(durations[i] for i in pinned)
        free = [i for i in free if i not in pinned]
    return [round(d, 2) for d in durations]


def _violates_history(
    *,
    structure_name: str,
    beat_texts: tuple[str, ...],
    history: list[LedgerEntry],
) -> str | None:
    recent = history[-STRUCTURE_COOLDOWN:]
    if any(e.structure == structure_name for e in recent):
        return f"structure {structure_name!r} used within the last {STRUCTURE_COOLDOWN} episodes"
    for entry in history:
        overlap = beat_overlap(beat_texts, entry.beat_texts)
        if overlap > MAX_BEAT_OVERLAP:
            return (
                f"beat overlap {overlap:.2f} with episode {entry.episode_id} "
                f"exceeds {MAX_BEAT_OVERLAP}"
            )
    return None


def plan_episode(
    species_id: str,
    *,
    episode_id: str | None = None,
    config: Config | None = None,
    seed: int | None = None,
    target_duration_s: float | None = None,
    ledger: Ledger | None = None,
    record: bool = True,
) -> EpisodePlan:
    """Build one episode plan, or raise if it would be a near-duplicate."""
    cfg = config or load_config()
    species = load_species(species_id, cfg)
    episode_id = episode_id or f"{species_id}_{datetime.now(UTC):%Y%m%d_%H%M%S}"
    ep_seed = _episode_seed(episode_id, seed)
    rng = random.Random(ep_seed)

    led = ledger or Ledger(cfg.paths.output_root / "episode_ledger.jsonl")
    history = led.entries(species_id)

    target = target_duration_s or (cfg.assembly.min_duration_s + cfg.assembly.max_duration_s) / 2
    light_options = species.get("light", {}).get("options") or [""]
    camera_note = species.get("camera", {}).get("motion", "")
    negative = species.get("prompt", {}).get("negative", "")

    last_reason = "no attempt made"
    for attempt in range(MAX_PLAN_ATTEMPTS):
        structure = rng.choice(structures.STRUCTURES)
        light = rng.choice(light_options)

        used: set[str] = set()
        beat_texts: list[str] = []
        ok = True
        for beat in structure.beats:
            pool = [t for t in _species_beats(species, beat) if t not in used]
            if not pool:
                # Pool exhausted within this episode: the species config is too thin
                # for this structure. Try another structure rather than repeating a beat.
                ok = False
                last_reason = f"beat pool {beat!r} exhausted within the episode"
                break
            text = rng.choice(pool)
            used.add(text)
            beat_texts.append(text)
        if not ok:
            continue

        reason = _violates_history(
            structure_name=structure.name, beat_texts=tuple(beat_texts), history=history
        )
        if reason:
            last_reason = reason
            log.debug("plan.rejected", attempt=attempt, reason=reason)
            continue

        durations = _durations(structure, target, cfg.qc.min_duration_s, cfg.qc.max_duration_s)
        shots = tuple(
            Shot(
                index=i,
                beat=beat,
                beat_text=text,
                queue="hero" if beat in HERO_BEATS else "bulk",
                duration_s=durations[i],
                seed=rng.randrange(2**31),
                prompt=_compose_prompt(species, text, light, camera_note),
                negative_prompt=negative,
            )
            for i, (beat, text) in enumerate(zip(structure.beats, beat_texts, strict=True))
        )

        plan = EpisodePlan(
            episode_id=episode_id,
            species_id=species_id,
            structure=structure.name,
            light=light,
            created_at=datetime.now(UTC).isoformat(),
            shots=shots,
            seed=ep_seed,
            notes=structure.description,
            meta={"target_duration_s": target, "attempts": attempt + 1},
        )
        if record:
            led.append(
                LedgerEntry(
                    episode_id=episode_id,
                    species_id=species_id,
                    structure=structure.name,
                    beat_texts=tuple(beat_texts),
                    light=light,
                )
            )
        log.info(
            "plan.built",
            episode_id=episode_id,
            structure=structure.name,
            shots=len(shots),
            duration_s=round(plan.total_duration_s, 1),
            attempts=attempt + 1,
        )
        return plan

    raise VarietyError(
        f"could not plan a distinct episode for {species_id!r} in {MAX_PLAN_ATTEMPTS} attempts "
        f"(last reason: {last_reason}). Widen the beat pools in "
        f"config/species/{species_id}.toml or add a structure -- do not relax the variety "
        f"thresholds, that is the demonetization pattern hard rule 2 exists to prevent."
    )


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Plan one episode and print it as JSON.")
    parser.add_argument("species", nargs="?", default="desert_tortoise")
    parser.add_argument("--episode-id", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do not write the plan to the episode ledger",
    )
    args = parser.parse_args()

    plan = plan_episode(
        args.species, episode_id=args.episode_id, seed=args.seed, record=not args.dry_run
    )
    print(json.dumps(plan.to_json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _main()
