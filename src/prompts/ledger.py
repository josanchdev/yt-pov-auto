"""Episode ledger.

Append-only record of what has already shipped, so the planner can refuse to
repeat itself. Variety is a code-enforced property here, not an aspiration
(CLAUDE.md hard rule 2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LedgerEntry:
    episode_id: str
    species_id: str
    structure: str
    beat_texts: tuple[str, ...]
    light: str

    def to_json(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "species_id": self.species_id,
            "structure": self.structure,
            "beat_texts": list(self.beat_texts),
            "light": self.light,
        }

    @classmethod
    def from_json(cls, data: dict) -> LedgerEntry:
        return cls(
            episode_id=data["episode_id"],
            species_id=data["species_id"],
            structure=data["structure"],
            beat_texts=tuple(data.get("beat_texts", ())),
            light=data.get("light", ""),
        )


class Ledger:
    """JSONL ledger of planned episodes."""

    def __init__(self, path: Path):
        self.path = path

    def entries(self, species_id: str | None = None) -> list[LedgerEntry]:
        if not self.path.is_file():
            return []
        out: list[LedgerEntry] = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            entry = LedgerEntry.from_json(json.loads(line))
            if species_id is None or entry.species_id == species_id:
                out.append(entry)
        return out

    def append(self, entry: LedgerEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_json(), ensure_ascii=False) + "\n")


def beat_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    """Jaccard overlap of two beat-text sets. 1.0 means identical content."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
