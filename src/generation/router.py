"""GPU routing.

Two independent queues, never one logical device. The 5090 takes shots that carry
the episode; the 3090 takes the connective tissue. If a queue is unreachable the
router will fall back to the other one, but it logs the downgrade loudly and clamps
resolution to what the receiving card can actually hold.

Running both queues concurrently is M1 (see KICKOFF.md). M0 dispatches sequentially
so that a failure is trivially attributable to one shot.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config import Config, GPUQueue
from src.errors import GenerationError
from src.generation.comfy_client import ComfyClient
from src.logging_setup import get_logger
from src.prompts.generator import Shot

log = get_logger("router")


@dataclass(frozen=True)
class Assignment:
    shot: Shot
    queue: GPUQueue
    resolution: tuple[int, int]
    downgraded_from: str | None = None


class Router:
    def __init__(self, config: Config, *, clients: dict[str, ComfyClient] | None = None):
        self.config = config
        self._clients = clients or {}
        self._health: dict[str, bool] = {}

    def client(self, queue: GPUQueue) -> ComfyClient:
        if queue.name not in self._clients:
            self._clients[queue.name] = ComfyClient(
                queue.host, device=queue.device, model=queue.model
            )
        return self._clients[queue.name]

    def healthy(self, queue: GPUQueue, *, recheck: bool = False) -> bool:
        if recheck or queue.name not in self._health:
            self._health[queue.name] = self.client(queue).ping()
        return self._health[queue.name]

    def assign(self, shot: Shot) -> Assignment:
        preferred = self.config.queue(shot.queue)
        if self.healthy(preferred):
            return Assignment(shot=shot, queue=preferred, resolution=preferred.max_resolution)

        alternatives = [
            q for q in self.config.queues if q.name != preferred.name and self.healthy(q)
        ]
        if not alternatives:
            raise GenerationError(
                f"no reachable ComfyUI queue for shot {shot.index} "
                f"(wanted {preferred.name} at {preferred.host}). "
                f"Start ComfyUI on both GPUs and check nvidia-smi sees both cards."
            )
        fallback = max(alternatives, key=lambda q: q.vram_gb)
        log.warning(
            "router.downgrade",
            shot=shot.index,
            wanted=preferred.name,
            using=fallback.name,
            reason="preferred queue unreachable",
        )
        return Assignment(
            shot=shot,
            queue=fallback,
            resolution=fallback.max_resolution,
            downgraded_from=preferred.name,
        )

    def plan_assignments(self, shots: tuple[Shot, ...]) -> list[Assignment]:
        return [self.assign(shot) for shot in shots]

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


if __name__ == "__main__":
    from src.config import load_config
    from src.prompts.generator import plan_episode

    cfg = load_config()
    plan = plan_episode("desert_tortoise", record=False)
    router = Router(cfg)
    try:
        for assignment in router.plan_assignments(plan.shots):
            log.info(
                "router.assignment",
                shot=assignment.shot.index,
                beat=assignment.shot.beat,
                queue=assignment.queue.name,
                device=assignment.queue.device,
                model=assignment.queue.model,
                resolution=list(assignment.resolution),
                downgraded_from=assignment.downgraded_from,
            )
    finally:
        router.close()
