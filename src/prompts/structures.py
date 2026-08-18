"""Narrative structures.

Hard rule 2: swapping a species name into an otherwise identical template is the
exact pattern that gets channels demonetized for inauthentic content. So an episode
is not "N clips of an animal walking" -- it is a named structure with its own beat
order and its own pacing curve, and consecutive episodes may not reuse one.

`beats` names beat pools defined per species in config/species/<id>.toml.
`pacing` weights clip durations; it is normalised against the target runtime, so a
structure with a long tail genuinely feels different from one with a fast open.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Structure:
    name: str
    description: str
    beats: tuple[str, ...]
    pacing: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.beats) != len(self.pacing):
            raise ValueError(f"structure {self.name}: beats and pacing lengths differ")

    @property
    def length(self) -> int:
        return len(self.beats)


STRUCTURES: tuple[Structure, ...] = (
    Structure(
        name="journey",
        description="Steady outbound push, one obstacle, arrival pays it off.",
        beats=("open", "travel", "travel", "tension", "travel", "encounter", "reward", "close"),
        pacing=(1.2, 1.0, 1.0, 0.7, 1.0, 1.1, 1.4, 1.3),
    ),
    Structure(
        name="interruption",
        description="Routine established, then broken hard in the middle, never fully recovers.",
        beats=("open", "travel", "encounter", "tension", "tension", "travel", "reward", "close"),
        pacing=(1.4, 1.2, 0.8, 0.5, 0.6, 1.3, 1.2, 1.5),
    ),
    Structure(
        name="encounter",
        description="Everything orbits one meeting; the approach is slow, the exit is quick.",
        beats=("open", "travel", "travel", "encounter", "encounter", "reward", "travel", "close"),
        pacing=(1.1, 1.3, 1.3, 1.0, 0.9, 1.1, 0.8, 1.2),
    ),
    Structure(
        name="descent",
        description="Light and ground both fall away; tension accumulates instead of resolving.",
        beats=("open", "travel", "tension", "travel", "tension", "encounter", "tension", "close"),
        pacing=(1.3, 1.0, 0.8, 0.9, 0.7, 0.9, 0.6, 1.6),
    ),
    Structure(
        name="foraging",
        description="Low-stakes loop of search and small finds; the reward is early, not final.",
        beats=("open", "travel", "reward", "travel", "encounter", "travel", "reward", "close"),
        pacing=(1.0, 1.1, 1.3, 1.0, 0.9, 1.1, 1.3, 1.2),
    ),
    Structure(
        name="vigil",
        description="Mostly held still. Motion is rare and therefore loud.",
        beats=("open", "tension", "encounter", "travel", "tension", "encounter", "reward", "close"),
        pacing=(1.6, 1.0, 0.7, 1.2, 0.8, 0.7, 1.4, 1.6),
    ),
)

BY_NAME = {s.name: s for s in STRUCTURES}


def get(name: str) -> Structure:
    try:
        return BY_NAME[name]
    except KeyError:
        raise ValueError(f"unknown structure {name!r}; known: {', '.join(BY_NAME)}") from None
