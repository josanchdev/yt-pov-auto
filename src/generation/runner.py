"""Clip generation.

Runs an episode plan through the ComfyUI queues and lands every result in
output/raw/ with a sidecar JSON recording seed, model, workflow hash and the full
prompt -- hard rule 4. Nothing here writes to output/approved/; only QC does.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.config import Config, load_config
from src.errors import GenerationError, VRAMError
from src.generation.router import Router
from src.generation.workflow import WorkflowTemplate, frame_count
from src.logging_setup import get_logger
from src.prompts.generator import EpisodePlan, Shot

log = get_logger("generation")


@dataclass(frozen=True)
class ClipResult:
    clip_id: str
    path: Path | None
    ok: bool
    error: str | None = None


def _sidecar(
    *,
    clip_id: str,
    plan: EpisodePlan,
    shot: Shot,
    queue_name: str,
    device: str,
    model: str,
    workflow: WorkflowTemplate,
    resolution: tuple[int, int],
    frames: int,
    fps: int,
    video_path: Path,
    elapsed_s: float,
) -> dict:
    return {
        "clip_id": clip_id,
        "episode_id": plan.episode_id,
        "species_id": plan.species_id,
        "structure": plan.structure,
        "shot_index": shot.index,
        "beat": shot.beat,
        "beat_text": shot.beat_text,
        "prompt": shot.prompt,
        "negative_prompt": shot.negative_prompt,
        "seed": shot.seed,
        "queue": queue_name,
        "device": device,
        "model": model,
        "workflow": workflow.name,
        "workflow_sha256": workflow.sha256,
        "resolution": list(resolution),
        "frames": frames,
        "fps": fps,
        "target_duration_s": shot.duration_s,
        "video": video_path.name,
        "generated_at": datetime.now(UTC).isoformat(),
        "elapsed_s": round(elapsed_s, 2),
        "ai_generated": True,
    }


def generate_episode(
    plan: EpisodePlan,
    *,
    config: Config | None = None,
    router: Router | None = None,
    stop_on_error: bool = False,
) -> list[ClipResult]:
    """Generate every shot in the plan into output/raw/.

    A failed shot is recorded and the run continues -- a 60-90s episode survives
    losing one clip, and stopping the whole run for one bad seed wastes GPU hours.
    VRAM errors are the exception: they abort, because every later shot on that card
    would hit the same wall (hard rule 5).
    """
    cfg = config or load_config()
    cfg.paths.ensure()
    own_router = router is None
    router = router or Router(cfg)
    templates: dict[str, WorkflowTemplate] = {}
    results: list[ClipResult] = []

    try:
        for assignment in router.plan_assignments(plan.shots):
            shot = assignment.shot
            queue = assignment.queue
            clip_id = plan.clip_id(shot)

            if queue.workflow not in templates:
                templates[queue.workflow] = WorkflowTemplate.load(
                    cfg.paths.workflows, queue.workflow
                )
            template = templates[queue.workflow]

            width, height = assignment.resolution
            fps = cfg.generation.fps
            frames = frame_count(shot.duration_s, fps)
            graph = template.build(
                positive_prompt=shot.prompt,
                negative_prompt=shot.negative_prompt,
                seed=shot.seed,
                width=width,
                height=height,
                length=frames,
                fps=fps,
            )

            log.info(
                "clip.start",
                clip_id=clip_id,
                beat=shot.beat,
                queue=queue.name,
                device=queue.device,
                model=queue.model,
                seed=shot.seed,
                frames=frames,
                resolution=[width, height],
            )
            started = time.monotonic()
            client = router.client(queue)
            try:
                prompt_id = client.queue_prompt(graph)
                files = client.wait(
                    prompt_id,
                    poll_interval_s=cfg.generation.poll_interval_s,
                    timeout_s=cfg.generation.job_timeout_s,
                    resolution=(width, height),
                )
            except VRAMError:
                log.error("clip.oom", clip_id=clip_id, device=queue.device, model=queue.model)
                raise
            except GenerationError as exc:
                log.error("clip.failed", clip_id=clip_id, error=str(exc))
                results.append(ClipResult(clip_id=clip_id, path=None, ok=False, error=str(exc)))
                if stop_on_error:
                    raise
                continue

            elapsed = time.monotonic() - started
            source = files[0]
            suffix = Path(source.filename).suffix or ".mp4"
            video_path = cfg.paths.raw / f"{clip_id}{suffix}"
            client.download(source, video_path)

            sidecar_path = cfg.paths.raw / f"{clip_id}.json"
            sidecar_path.write_text(
                json.dumps(
                    _sidecar(
                        clip_id=clip_id,
                        plan=plan,
                        shot=shot,
                        queue_name=queue.name,
                        device=queue.device,
                        model=queue.model,
                        workflow=template,
                        resolution=(width, height),
                        frames=frames,
                        fps=fps,
                        video_path=video_path,
                        elapsed_s=elapsed,
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )
            log.info(
                "clip.done", clip_id=clip_id, path=str(video_path), elapsed_s=round(elapsed, 1)
            )
            results.append(ClipResult(clip_id=clip_id, path=video_path, ok=True))
    finally:
        if own_router:
            router.close()

    ok = sum(1 for r in results if r.ok)
    log.info(
        "generation.summary", episode_id=plan.episode_id, generated=ok, failed=len(results) - ok
    )
    return results


def write_plan(plan: EpisodePlan, config: Config | None = None) -> Path:
    cfg = config or load_config()
    cfg.paths.ensure()
    path = cfg.paths.raw / f"{plan.episode_id}_plan.json"
    path.write_text(json.dumps(plan.to_json(), indent=2, ensure_ascii=False) + "\n")
    return path


def _main() -> None:
    import argparse

    from src.prompts.generator import plan_episode

    parser = argparse.ArgumentParser(description="Generate one episode's clips into output/raw/.")
    parser.add_argument("species", nargs="?", default="desert_tortoise")
    parser.add_argument("--episode-id", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    plan = plan_episode(args.species, episode_id=args.episode_id, seed=args.seed, config=cfg)
    write_plan(plan, cfg)
    results = generate_episode(plan, config=cfg, stop_on_error=args.stop_on_error)
    print(
        json.dumps(
            [asdict(r) | {"path": str(r.path) if r.path else None} for r in results], indent=2
        )
    )


if __name__ == "__main__":
    _main()
