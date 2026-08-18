"""ComfyUI HTTP API client.

ComfyUI is driven over its HTTP API, never the web UI. One client instance talks to
one ComfyUI process, which is pinned to one GPU -- see CLAUDE.md/Hardware.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from src.errors import GenerationError, VRAMError
from src.logging_setup import get_logger

log = get_logger("comfy")

_OOM_MARKERS = (
    "out of memory",
    "cuda oom",
    "torch.cuda.outofmemoryerror",
    "allocation on device",
)


@dataclass(frozen=True)
class GeneratedFile:
    filename: str
    subfolder: str
    type: str


class ComfyClient:
    """Thin, synchronous ComfyUI client. One instance per GPU queue."""

    def __init__(
        self,
        host: str,
        *,
        device: str,
        model: str,
        timeout_s: float = 30.0,
        client: httpx.Client | None = None,
    ):
        self.host = host.rstrip("/")
        self.device = device
        self.model = model
        self.client_id = str(uuid.uuid4())
        self._http = client or httpx.Client(base_url=self.host, timeout=timeout_s)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ComfyClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- health ---------------------------------------------------------------

    def system_stats(self) -> dict[str, Any]:
        return self._get("/system_stats").json()

    def ping(self) -> bool:
        try:
            self.system_stats()
            return True
        except Exception as exc:  # noqa: BLE001 - health check reports, never raises
            log.warning("comfy.unreachable", host=self.host, device=self.device, error=str(exc))
            return False

    # -- jobs -----------------------------------------------------------------

    def queue_prompt(self, graph: dict[str, Any]) -> str:
        payload = {"prompt": graph, "client_id": self.client_id}
        response = self._http.post("/prompt", json=payload)
        if response.status_code >= 400:
            self._raise_for_body(response.text, resolution=_graph_resolution(graph))
        data = response.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise GenerationError(f"{self.host} accepted the job but returned no prompt_id: {data}")
        log.info("job.queued", host=self.host, device=self.device, prompt_id=prompt_id)
        return prompt_id

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        response = self._get(f"/history/{prompt_id}")
        return response.json().get(prompt_id)

    def wait(
        self,
        prompt_id: str,
        *,
        poll_interval_s: float,
        timeout_s: float,
        resolution: tuple[int, int] = (0, 0),
    ) -> list[GeneratedFile]:
        """Block until the job finishes. Raises on ComfyUI-side failure."""
        deadline = time.monotonic() + timeout_s
        while True:
            entry = self.history(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                if status.get("status_str") == "error" or status.get("completed") is False:
                    self._raise_for_body(str(status), resolution=resolution)
                if status.get("completed") or entry.get("outputs"):
                    files = _collect_outputs(entry.get("outputs", {}))
                    if not files:
                        raise GenerationError(
                            f"job {prompt_id} on {self.device} completed with no video output; "
                            f"check that the workflow ends in a SaveVideo/VHS node"
                        )
                    return files
            if time.monotonic() > deadline:
                raise GenerationError(
                    f"job {prompt_id} on {self.device} ({self.model}) exceeded "
                    f"{timeout_s:.0f}s; still queued or running on {self.host}"
                )
            time.sleep(poll_interval_s)

    def download(self, file: GeneratedFile, dest: Path) -> Path:
        params = {"filename": file.filename, "subfolder": file.subfolder, "type": file.type}
        with self._http.stream("GET", "/view", params=params) as response:
            if response.status_code >= 400:
                raise GenerationError(
                    f"could not fetch {file.filename} from {self.host}: HTTP {response.status_code}"
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
        return dest

    # -- internals ------------------------------------------------------------

    def _get(self, path: str) -> httpx.Response:
        try:
            response = self._http.get(path)
        except httpx.HTTPError as exc:
            raise GenerationError(f"{self.host} unreachable ({self.device}): {exc}") from exc
        if response.status_code >= 400:
            raise GenerationError(f"{self.host}{path} returned HTTP {response.status_code}")
        return response

    def _raise_for_body(self, body: str, *, resolution: tuple[int, int]) -> None:
        """Hard rule 5: OOM surfaces as VRAMError with device, model and resolution."""
        lowered = body.lower()
        if any(marker in lowered for marker in _OOM_MARKERS):
            raise VRAMError(
                device=self.device,
                model=self.model,
                resolution=resolution,
                detail=body.strip()[:400],
            )
        raise GenerationError(f"{self.device} ({self.model}) job failed: {body.strip()[:800]}")


def _graph_resolution(graph: dict[str, Any]) -> tuple[int, int]:
    width = height = 0
    for node in graph.values():
        inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
        if isinstance(inputs.get("width"), int) and isinstance(inputs.get("height"), int):
            width, height = inputs["width"], inputs["height"]
    return width, height


def _collect_outputs(outputs: dict[str, Any]) -> list[GeneratedFile]:
    files: list[GeneratedFile] = []
    for node_output in outputs.values():
        for key in ("videos", "gifs", "images"):
            for item in node_output.get(key, []) or []:
                filename = item.get("filename")
                if not filename:
                    continue
                files.append(
                    GeneratedFile(
                        filename=filename,
                        subfolder=item.get("subfolder", ""),
                        type=item.get("type", "output"),
                    )
                )
    # Video wins over any preview still the workflow also saved.
    video = [f for f in files if Path(f.filename).suffix.lower() in {".mp4", ".webm", ".mkv"}]
    return video or files


if __name__ == "__main__":
    from src.config import load_config

    cfg = load_config()
    for queue in cfg.queues:
        with ComfyClient(queue.host, device=queue.device, model=queue.model) as client:
            alive = client.ping()
            log.info("comfy.health", queue=queue.name, host=queue.host, reachable=alive)
