"""ComfyUI API-format workflow loading and patching.

Workflows are exported from ComfyUI in *API format* and live in workflows/ as
`<name>.api.json` (the raw graph, sent verbatim to /prompt) plus `<name>.map.json`
(which node ids hold the prompt, seed, resolution and frame count). Keeping the
patch map in a sidecar means the graph file stays byte-identical to what ComfyUI
exported -- so its hash is a meaningful reproducibility key (hard rule 4).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.errors import WorkflowError

# Patch points a map file may declare. Each maps to {"node": "<id>", "input": "<field>"}.
PATCH_POINTS = ("positive_prompt", "negative_prompt", "seed", "width", "height", "length", "fps")
REQUIRED_POINTS = ("positive_prompt", "seed")


@dataclass(frozen=True)
class WorkflowTemplate:
    name: str
    graph: dict[str, Any]
    patch_map: dict[str, dict[str, str]]
    sha256: str
    path: Path

    @classmethod
    def load(cls, workflows_dir: Path, filename: str) -> WorkflowTemplate:
        path = workflows_dir / filename
        if not path.is_file():
            raise WorkflowError(
                f"workflow not found: {path}. Export it from ComfyUI with "
                f"'Save (API Format)' and drop it in {workflows_dir}/"
            )
        raw = path.read_bytes()
        try:
            graph = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(graph, dict) or not graph:
            raise WorkflowError(f"{path} is not an API-format workflow (expected a node dict)")

        map_path = path.with_name(
            path.name[: -len(".api.json")] + ".map.json"
            if path.name.endswith(".api.json")
            else path.stem + ".map.json"
        )
        if not map_path.is_file():
            raise WorkflowError(
                f"missing patch map {map_path}. It declares which node ids carry the "
                f"prompt/seed/resolution, e.g. "
                f'{{"positive_prompt": {{"node": "6", "input": "text"}}}}'
            )
        patch_map = json.loads(map_path.read_text())

        unknown = set(patch_map) - set(PATCH_POINTS)
        if unknown:
            raise WorkflowError(
                f"{map_path} declares unknown patch points: {', '.join(sorted(unknown))}; "
                f"valid points are {', '.join(PATCH_POINTS)}"
            )
        missing = [p for p in REQUIRED_POINTS if p not in patch_map]
        if missing:
            raise WorkflowError(f"{map_path} must declare {', '.join(missing)}")
        for point, target in patch_map.items():
            node_id = target.get("node")
            if node_id not in graph:
                raise WorkflowError(
                    f"{map_path}: patch point {point!r} targets node {node_id!r}, "
                    f"which does not exist in {path.name}"
                )
            field = target.get("input")
            if field not in graph[node_id].get("inputs", {}):
                raise WorkflowError(
                    f"{map_path}: node {node_id} has no input {field!r} "
                    f"(class {graph[node_id].get('class_type')!r})"
                )

        return cls(
            name=path.name,
            graph=graph,
            patch_map=patch_map,
            sha256=hashlib.sha256(raw).hexdigest(),
            path=path,
        )

    def build(
        self,
        *,
        positive_prompt: str,
        seed: int,
        negative_prompt: str = "",
        width: int | None = None,
        height: int | None = None,
        length: int | None = None,
        fps: int | None = None,
    ) -> dict[str, Any]:
        """Return a patched copy of the graph, ready to POST to /prompt."""
        values: dict[str, Any] = {
            "positive_prompt": positive_prompt,
            "negative_prompt": negative_prompt,
            "seed": seed,
            "width": width,
            "height": height,
            "length": length,
            "fps": fps,
        }
        graph = copy.deepcopy(self.graph)
        for point, value in values.items():
            if value is None:
                continue
            target = self.patch_map.get(point)
            if target is None:
                continue
            graph[target["node"]]["inputs"][target["input"]] = value
        return graph


def frame_count(duration_s: float, fps: int) -> int:
    """Frames for a clip, rounded *up* to the 4n+1 shape video models expect.

    Up, not down: rounding down can push a clip under the QC minimum duration,
    and trimming a few frames later is free while regenerating is not.
    """
    frames = max(1, round(duration_s * fps))
    return 4 * math.ceil((frames - 1) / 4) + 1
