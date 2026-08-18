"""The QC gate.

Hard rule 1: clips land in output/raw/ and reach output/approved/ only by passing
here. Assembly reads exclusively from approved/. There is no bypass flag and no
"just this once" path -- if you find yourself wanting one, the fix is to change the
thresholds in config/pipeline.toml, in a commit, with a reason.

Two stages:
  1. automatic  -- src/qc/checks.py, cheap failures rejected without a human
  2. manual     -- a human looks at the survivors and answers M0 criterion 4

Manual review is required by default. `--auto-only` promotes clips on the automatic
pass alone; it exists for unattended M1 batch runs and it is recorded in the verdict
so an unreviewed clip is never mistaken for a reviewed one.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from src.config import Config, load_config
from src.errors import PipelineError
from src.logging_setup import get_logger
from src.media import available
from src.qc.checks import check_clip

log = get_logger("qc")

VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv"}


@dataclass
class Verdict:
    clip_id: str
    approved: bool
    stage: str  # "auto" | "manual"
    reviewer: str
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    decided_at: str = ""

    def __post_init__(self) -> None:
        self.decided_at = self.decided_at or datetime.now(UTC).isoformat()


@dataclass
class GateSummary:
    total: int
    approved: int
    rejected: int
    verdicts: list[Verdict]

    @property
    def reject_rate(self) -> float:
        return self.rejected / self.total if self.total else 0.0


def raw_clips(cfg: Config, episode_id: str | None = None) -> list[Path]:
    clips = [
        p
        for p in sorted(cfg.paths.raw.iterdir())
        if p.suffix.lower() in VIDEO_SUFFIXES
        and (episode_id is None or p.stem.startswith(f"{episode_id}_"))
    ]
    return clips


def _move(clip: Path, dest_dir: Path, verdict: Verdict) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / clip.name
    shutil.move(str(clip), target)

    sidecar = clip.with_suffix(".json")
    payload: dict = {}
    if sidecar.is_file():
        payload = json.loads(sidecar.read_text())
        sidecar.unlink()
    payload["qc"] = asdict(verdict)
    (dest_dir / f"{clip.stem}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )
    return target


def _ask(clip: Path, metrics: dict[str, float]) -> tuple[bool, str]:
    print(f"\n  {clip}")
    print(f"  metrics: {json.dumps(metrics)}")
    print("  Watch it, then answer. Criterion 4: would this stop you scrolling?")
    while True:
        answer = input("  approve? [y]es / [n]o / [q]uit: ").strip().lower()
        if answer in {"y", "yes"}:
            return True, "human approved"
        if answer in {"n", "no"}:
            reason = input("  reason: ").strip() or "human rejected"
            return False, reason
        if answer in {"q", "quit"}:
            raise KeyboardInterrupt


def run_gate(
    *,
    config: Config | None = None,
    episode_id: str | None = None,
    auto_only: bool = False,
    reviewer: str = "manual",
) -> GateSummary:
    cfg = config or load_config()
    cfg.paths.ensure()
    if not available("ffprobe"):
        raise PipelineError(
            "QC needs ffprobe and it is not on PATH. QC does not degrade to "
            "'approve everything' when its tools are missing (hard rule 1)."
        )

    clips = raw_clips(cfg, episode_id)
    if not clips:
        log.warning("qc.empty", raw=str(cfg.paths.raw), episode_id=episode_id)
        return GateSummary(total=0, approved=0, rejected=0, verdicts=[])

    verdicts: list[Verdict] = []
    for clip in clips:
        report = check_clip(clip, cfg.qc)
        if not report.passed:
            verdict = Verdict(
                clip_id=report.clip_id,
                approved=False,
                stage="auto",
                reviewer="auto",
                reasons=report.failures,
                metrics=report.metrics,
            )
            _move(clip, cfg.paths.rejected, verdict)
            log.info("qc.rejected", clip_id=report.clip_id, reasons=report.failures)
            verdicts.append(verdict)
            continue

        if auto_only:
            verdict = Verdict(
                clip_id=report.clip_id,
                approved=True,
                stage="auto",
                reviewer="auto-only (not human reviewed)",
                reasons=["passed automatic checks; no human review"],
                metrics=report.metrics,
            )
        else:
            approved, reason = _ask(clip, report.metrics)
            verdict = Verdict(
                clip_id=report.clip_id,
                approved=approved,
                stage="manual",
                reviewer=reviewer,
                reasons=[reason],
                metrics=report.metrics,
            )
        _move(clip, cfg.paths.approved if verdict.approved else cfg.paths.rejected, verdict)
        log.info(
            "qc.verdict",
            clip_id=report.clip_id,
            approved=verdict.approved,
            stage=verdict.stage,
            reasons=verdict.reasons,
        )
        verdicts.append(verdict)

    approved = sum(1 for v in verdicts if v.approved)
    summary = GateSummary(
        total=len(verdicts),
        approved=approved,
        rejected=len(verdicts) - approved,
        verdicts=verdicts,
    )
    _log_reject_rate(cfg, summary, episode_id)
    return summary


def _log_reject_rate(cfg: Config, summary: GateSummary, episode_id: str | None) -> None:
    """M0 criterion 5: the reject rate is logged, and a bad one is stated plainly."""
    record = {
        "episode_id": episode_id,
        "logged_at": datetime.now(UTC).isoformat(),
        "total": summary.total,
        "approved": summary.approved,
        "rejected": summary.rejected,
        "reject_rate": round(summary.reject_rate, 4),
    }
    path = cfg.paths.output_root / "qc_reject_rate.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    log.info("qc.summary", **record)
    if summary.reject_rate > cfg.qc.reject_rate_alarm:
        log.error(
            "qc.reject_rate_alarm",
            reject_rate=round(summary.reject_rate, 3),
            threshold=cfg.qc.reject_rate_alarm,
            message=(
                "Reject rate above the M0 viability threshold. Per KICKOFF.md the plan "
                "changes before scaling: switch to a stylized direction, or budget cloud "
                "generation for hero shots. Do not just generate more."
            ),
        )


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the QC gate over output/raw/.")
    parser.add_argument("--episode-id", default=None)
    parser.add_argument(
        "--auto-only",
        action="store_true",
        help="skip human review (recorded in the verdict); for unattended batches",
    )
    parser.add_argument("--reviewer", default="manual")
    args = parser.parse_args()

    summary = run_gate(episode_id=args.episode_id, auto_only=args.auto_only, reviewer=args.reviewer)
    print(
        json.dumps(
            {
                "total": summary.total,
                "approved": summary.approved,
                "rejected": summary.rejected,
                "reject_rate": round(summary.reject_rate, 3),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    _main()
