"""Final assembly.

Reads *only* from output/approved/ (hard rule 1), normalises every clip to the
delivery format, stitches them in shot order, lays an ambient bed under whatever
audio the clips carry, and writes a disclosed file to output/final/.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.assembly import disclosure as disclosure_mod
from src.config import Config, load_config, load_species
from src.errors import AssemblyError
from src.logging_setup import get_logger
from src.media import probe, require, run

log = get_logger("assembly")

VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv"}


@dataclass(frozen=True)
class ApprovedClip:
    path: Path
    shot_index: int
    duration_s: float
    model: str
    has_audio: bool


def collect(cfg: Config, episode_id: str) -> list[ApprovedClip]:
    """Gather approved clips for one episode, in shot order."""
    clips: list[ApprovedClip] = []
    for path in sorted(cfg.paths.approved.iterdir()):
        if path.suffix.lower() not in VIDEO_SUFFIXES or not path.stem.startswith(f"{episode_id}_"):
            continue
        sidecar = path.with_suffix(".json")
        meta = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
        if meta.get("qc", {}).get("approved") is False:
            raise AssemblyError(
                f"{path.name} sits in approved/ with a rejecting QC verdict. "
                f"Something moved it by hand. Assembly reads approved/ only and trusts it."
            )
        info = probe(path)
        clips.append(
            ApprovedClip(
                path=path,
                shot_index=int(meta.get("shot_index", len(clips))),
                duration_s=info.duration_s,
                model=meta.get("model", "unknown"),
                has_audio=info.has_audio,
            )
        )
    clips.sort(key=lambda c: c.shot_index)
    return clips


DEFAULT_BED_GAIN_DB = -18.0


def _filter_graph(
    clips: list[ApprovedClip],
    cfg: Config,
    bed_index: int | None,
    bed_gain_db: float = DEFAULT_BED_GAIN_DB,
) -> str:
    a = cfg.assembly
    parts: list[str] = []
    for i, clip in enumerate(clips):
        parts.append(
            f"[{i}:v]scale={a.width}:{a.height}:force_original_aspect_ratio=decrease,"
            f"pad={a.width}:{a.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={a.fps}[v{i}]"
        )
        if clip.has_audio:
            parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={clip.duration_s:.3f}[a{i}]")

    streams = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
    parts.append(f"{streams}concat=n={len(clips)}:v=1:a=1[vout][acat]")

    if bed_index is None:
        parts.append("[acat]anull[aout]")
    else:
        parts.append(
            f"[{bed_index}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"volume={bed_gain_db}dB[bed]"
        )
        parts.append("[acat][bed]amix=inputs=2:duration=first:dropout_transition=0[aout]")
    return ";".join(parts)


def assemble(
    episode_id: str,
    *,
    config: Config | None = None,
    species_id: str | None = None,
    output_name: str | None = None,
) -> Path:
    cfg = config or load_config()
    cfg.paths.ensure()
    require("ffmpeg")

    clips = collect(cfg, episode_id)
    if not clips:
        raise AssemblyError(
            f"no approved clips for episode {episode_id}. Run the QC gate first -- "
            f"assembly never reads output/raw/."
        )
    if len(clips) < cfg.assembly.min_clips:
        raise AssemblyError(
            f"episode {episode_id} has {len(clips)} approved clips; M0 criterion 3 "
            f"requires at least {cfg.assembly.min_clips}. Generate and QC more."
        )

    total = sum(c.duration_s for c in clips)
    if not (cfg.assembly.min_duration_s <= total <= cfg.assembly.max_duration_s):
        log.warning(
            "assembly.duration_out_of_band",
            total_s=round(total, 1),
            min_s=cfg.assembly.min_duration_s,
            max_s=cfg.assembly.max_duration_s,
            message="M0 criterion 1 wants a 60-90s finished file",
        )

    species_id = species_id or episode_id.rsplit("_", 2)[0]
    bed_path: Path | None = None
    bed_gain = DEFAULT_BED_GAIN_DB
    try:
        species = load_species(species_id, cfg)
        bed_name = species.get("audio", {}).get("bed")
        if bed_name:
            candidate = (cfg.paths.output_root.parent / "reference" / bed_name).resolve()
            if candidate.is_file():
                bed_path = candidate
                bed_gain = float(species.get("audio", {}).get("gain_db", DEFAULT_BED_GAIN_DB))
            else:
                log.warning(
                    "assembly.no_ambient_bed",
                    expected=str(candidate),
                    message="rendering with clip audio only; drop the bed file in reference/",
                )
    except Exception as exc:  # noqa: BLE001 - a missing bed must not block the render
        log.warning("assembly.species_audio_unavailable", species=species_id, error=str(exc))

    output = cfg.paths.final / (output_name or f"{episode_id}.mp4")
    args = ["ffmpeg", "-y"]
    for clip in clips:
        args += ["-i", str(clip.path)]
    bed_index = None
    if bed_path is not None:
        bed_index = len(clips)
        args += ["-stream_loop", "-1", "-i", str(bed_path)]

    a = cfg.assembly
    disclosure = disclosure_mod.build(
        cfg.disclosure,
        episode_id=episode_id,
        models=tuple(sorted({c.model for c in clips})),
    )
    args += [
        "-filter_complex",
        _filter_graph(clips, cfg, bed_index, bed_gain),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        a.video_codec,
        "-crf",
        str(a.crf),
        "-preset",
        a.preset,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        a.audio_codec,
        "-b:a",
        a.audio_bitrate,
        "-shortest",
        *disclosure.ffmpeg_metadata(),
        str(output),
    ]

    log.info("assembly.start", episode_id=episode_id, clips=len(clips), total_s=round(total, 1))
    run(args, what="ffmpeg assembly")

    disclosure_mod.write(output, disclosure)
    disclosure_mod.verify(output)

    info = probe(output)
    log.info(
        "assembly.done",
        path=str(output),
        duration_s=round(info.duration_s, 1),
        resolution=f"{info.width}x{info.height}",
        clips=len(clips),
    )
    return output


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Assemble one episode from output/approved/.")
    parser.add_argument("episode_id")
    parser.add_argument("--species", default=None)
    parser.add_argument("--output-name", default=None)
    args = parser.parse_args()

    path = assemble(args.episode_id, species_id=args.species, output_name=args.output_name)
    print(path)


if __name__ == "__main__":
    _main()
