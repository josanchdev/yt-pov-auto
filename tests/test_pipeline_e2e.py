"""End-to-end: plan -> generate -> QC -> assemble, against a stubbed ComfyUI.

This exercises the real code path M0 criterion 2 cares about (the file comes out of
the pipeline, not out of ComfyUI's web UI). Only the two ComfyUI HTTP endpoints are
faked; the clips, QC and the final render are real ffmpeg work.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.assembly.build import assemble
from src.generation.comfy_client import ComfyClient
from src.generation.router import Router
from src.generation.runner import generate_episode
from src.prompts.generator import plan_episode
from src.prompts.ledger import Ledger
from src.qc.gate import run_gate
from tests.conftest import make_clip


@pytest.fixture
def stub_comfy(cfg, tmp_path):
    """A Router whose clients serve a real, freshly rendered mp4 for every job."""
    source = make_clip(tmp_path / "stub.mp4", seconds=8, size="1280x704")
    payload = source.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"name": "stub"}]})
        if path == "/prompt":
            body = json.loads(request.content)
            assert "PLACEHOLDER" not in json.dumps(body["prompt"]), "prompt was not patched in"
            return httpx.Response(200, json={"prompt_id": "job"})
        if path.startswith("/history/"):
            return httpx.Response(
                200,
                json={
                    "job": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "9": {
                                "gifs": [{"filename": "out.mp4", "subfolder": "", "type": "output"}]
                            }
                        },
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    clients = {
        q.name: ComfyClient(
            q.host,
            device=q.device,
            model=q.model,
            client=httpx.Client(transport=httpx.MockTransport(handler), base_url=q.host),
        )
        for q in cfg.queues
    }
    return Router(cfg, clients=clients)


@pytest.mark.slow
def test_m0_chain(cfg, stub_comfy, tmp_path, has_ffmpeg):
    if not has_ffmpeg:
        pytest.skip("ffmpeg/ffprobe not installed")

    plan = plan_episode(
        "desert_tortoise", config=cfg, episode_id="e2e", ledger=Ledger(tmp_path / "l.jsonl")
    )
    results = generate_episode(plan, config=cfg, router=stub_comfy)
    assert all(r.ok for r in results)
    assert len(results) >= cfg.assembly.min_clips

    # Hard rule 4: every clip is reproducible from its sidecar.
    sidecar = json.loads((cfg.paths.raw / "e2e_00.json").read_text())
    for key in ("seed", "model", "workflow_sha256", "prompt", "device", "resolution"):
        assert sidecar[key], f"sidecar is missing {key}"

    summary = run_gate(config=cfg, episode_id="e2e", auto_only=True)
    assert summary.approved == len(results), summary.verdicts

    output = assemble("e2e", config=cfg, species_id="desert_tortoise")
    assert output.is_file()

    from src.assembly.disclosure import verify

    verify(output)


def test_generation_survives_one_failed_clip(cfg, tmp_path, has_ffmpeg):
    if not has_ffmpeg:
        pytest.skip("ffmpeg/ffprobe not installed")

    calls = {"n": 0}
    source = make_clip(tmp_path / "stub.mp4", seconds=8, size="1280x704")
    payload = source.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/system_stats":
            return httpx.Response(200, json={})
        if path == "/prompt":
            calls["n"] += 1
            if calls["n"] == 2:
                return httpx.Response(400, text="node 7: sampler exploded")
            return httpx.Response(200, json={"prompt_id": "job"})
        if path.startswith("/history/"):
            return httpx.Response(
                200,
                json={
                    "job": {
                        "status": {"completed": True},
                        "outputs": {
                            "9": {
                                "gifs": [{"filename": "o.mp4", "subfolder": "", "type": "output"}]
                            }
                        },
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    clients = {
        q.name: ComfyClient(
            q.host,
            device=q.device,
            model=q.model,
            client=httpx.Client(transport=httpx.MockTransport(handler), base_url=q.host),
        )
        for q in cfg.queues
    }
    plan = plan_episode(
        "desert_tortoise", config=cfg, episode_id="partial", ledger=Ledger(tmp_path / "l.jsonl")
    )
    results = generate_episode(plan, config=cfg, router=Router(cfg, clients=clients))
    assert sum(1 for r in results if not r.ok) == 1
    assert sum(1 for r in results if r.ok) == len(plan.shots) - 1


def test_router_falls_back_when_a_queue_is_down(cfg):
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def alive(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    clients = {}
    for q in cfg.queues:
        handler = dead if q.name == "hero" else alive
        clients[q.name] = ComfyClient(
            q.host,
            device=q.device,
            model=q.model,
            client=httpx.Client(transport=httpx.MockTransport(handler), base_url=q.host),
        )
    plan = plan_episode("desert_tortoise", config=cfg, episode_id="fb", record=False)
    assignments = Router(cfg, clients=clients).plan_assignments(plan.shots)
    hero_shots = [a for a in assignments if a.shot.queue == "hero"]
    assert hero_shots, "expected some hero-routed shots"
    assert all(a.queue.name == "bulk" for a in hero_shots)
    assert all(a.downgraded_from == "hero" for a in hero_shots)
