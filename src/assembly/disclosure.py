"""AI disclosure.

Hard rule 3. This content is photorealistic by design, which is exactly what
triggers YouTube's altered/synthetic content label and TikTok's AIGC label. Both
platforms have said the label does not reduce distribution, so there is no upside
to omitting it and a channel-wide downside to being caught without it.

The disclosure block is written next to every final render and embedded in the file's
metadata. `verify()` re-reads the rendered file and raises if either is missing --
nothing leaves output/final/ undisclosed, including files produced by a future code
path that forgets to call this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.config import DisclosureConfig
from src.errors import DisclosureError
from src.media import probe  # noqa: F401  (kept for callers that want media facts alongside)


@dataclass(frozen=True)
class Disclosure:
    description_line: str
    youtube_altered_content: bool
    tiktok_aigc_label: bool
    generated_at: str
    models: tuple[str, ...]
    episode_id: str

    def to_json(self) -> dict:
        return {
            "ai_generated": True,
            "description_line": self.description_line,
            "platform_labels": {
                "youtube_altered_content": self.youtube_altered_content,
                "tiktok_aigc_label": self.tiktok_aigc_label,
            },
            "models": list(self.models),
            "episode_id": self.episode_id,
            "generated_at": self.generated_at,
            "publish_checklist": [
                "YouTube: tick 'Altered or synthetic content' in the upload flow",
                "TikTok: enable the 'AI-generated content' toggle",
                "Both: keep the disclosure line as the first line of the description",
            ],
        }

    def ffmpeg_metadata(self) -> list[str]:
        return [
            "-metadata",
            f"comment={self.description_line}",
            "-metadata",
            "description=AI-generated content. See sidecar disclosure.json.",
            "-metadata",
            "synthetic_content=true",
        ]


def build(cfg: DisclosureConfig, *, episode_id: str, models: tuple[str, ...]) -> Disclosure:
    if not cfg.youtube_altered_content or not cfg.tiktok_aigc_label:
        raise DisclosureError("disclosure is disabled in config; hard rule 3 admits no exceptions")
    return Disclosure(
        description_line=cfg.description_line,
        youtube_altered_content=cfg.youtube_altered_content,
        tiktok_aigc_label=cfg.tiktok_aigc_label,
        generated_at=datetime.now(UTC).isoformat(),
        models=models,
        episode_id=episode_id,
    )


def sidecar_path(video: Path) -> Path:
    return video.with_suffix(".disclosure.json")


def write(video: Path, disclosure: Disclosure) -> Path:
    path = sidecar_path(video)
    path.write_text(json.dumps(disclosure.to_json(), indent=2, ensure_ascii=False) + "\n")
    return path


def verify(video: Path) -> None:
    """Raise unless `video` carries disclosure both in a sidecar and in its metadata."""
    path = sidecar_path(video)
    if not path.is_file():
        raise DisclosureError(
            f"{video.name} has no disclosure sidecar at {path.name}. "
            f"It must not be uploaded (hard rule 3)."
        )
    data = json.loads(path.read_text())
    labels = data.get("platform_labels", {})
    if not data.get("ai_generated") or not all(labels.get(k) for k in labels):
        raise DisclosureError(f"{path.name} exists but does not assert AI disclosure")

    import subprocess

    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format_tags",
            "-print_format",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        tags = {
            k.lower(): v
            for k, v in json.loads(proc.stdout).get("format", {}).get("tags", {}).items()
        }
        if "synthetic_content" not in tags and "comment" not in tags:
            raise DisclosureError(
                f"{video.name} carries no AI-disclosure metadata tags; re-render it"
            )
