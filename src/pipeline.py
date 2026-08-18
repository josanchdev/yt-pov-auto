"""M0 end-to-end runner.

    plan -> generate -> QC gate -> assemble

M0 is a hard gate (KICKOFF.md). This entry point exists to satisfy criterion 2:
the finished file must come out of the pipeline, not out of ComfyUI's web UI.
"""

from __future__ import annotations

import argparse
import json
import sys

from src.config import load_config
from src.errors import PipelineError
from src.generation.runner import generate_episode, write_plan
from src.logging_setup import get_logger
from src.prompts.generator import plan_episode
from src.qc.gate import run_gate

log = get_logger("pipeline")

DEFAULT_SPECIES = "desert_tortoise"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="animal-pov", description=__doc__)
    parser.add_argument("--species", default=DEFAULT_SPECIES)
    parser.add_argument("--episode-id", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--stage",
        choices=["plan", "generate", "qc", "assemble", "all"],
        default="all",
        help="run one stage only; 'all' runs the full M0 chain",
    )
    parser.add_argument(
        "--auto-only",
        action="store_true",
        help="QC without human review (recorded as unreviewed in every verdict)",
    )
    parser.add_argument("--reviewer", default="manual")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    cfg.paths.ensure()

    try:
        episode_id = args.episode_id
        if args.stage in {"plan", "generate", "all"}:
            plan = plan_episode(args.species, episode_id=episode_id, seed=args.seed, config=cfg)
            episode_id = plan.episode_id
            plan_path = write_plan(plan, cfg)
            log.info("pipeline.planned", episode_id=episode_id, plan=str(plan_path))
            if args.stage == "plan":
                print(json.dumps(plan.to_json(), indent=2, ensure_ascii=False))
                return 0

            results = generate_episode(plan, config=cfg)
            generated = sum(1 for r in results if r.ok)
            if generated == 0:
                log.error("pipeline.no_clips", episode_id=episode_id)
                return 1
            if args.stage == "generate":
                return 0

        if episode_id is None:
            log.error("pipeline.missing_episode_id", stage=args.stage)
            print(
                "--episode-id is required when starting at the qc or assemble stage",
                file=sys.stderr,
            )
            return 2

        if args.stage in {"qc", "all"}:
            summary = run_gate(
                config=cfg,
                episode_id=episode_id,
                auto_only=args.auto_only,
                reviewer=args.reviewer,
            )
            if summary.approved == 0:
                log.error("pipeline.nothing_approved", episode_id=episode_id)
                return 1
            if args.stage == "qc":
                return 0

        if args.stage in {"assemble", "all"}:
            from src.assembly.build import assemble

            output = assemble(episode_id, config=cfg, species_id=args.species)
            log.info("pipeline.done", episode_id=episode_id, output=str(output))
            print(output)
    except KeyboardInterrupt:
        log.warning("pipeline.interrupted")
        return 130
    except PipelineError as exc:
        log.error("pipeline.failed", error=str(exc), type=type(exc).__name__)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
