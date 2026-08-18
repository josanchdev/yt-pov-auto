from __future__ import annotations

import httpx
import pytest

from src.errors import GenerationError, VRAMError
from src.generation.comfy_client import ComfyClient, _collect_outputs


def make_client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="http://comfy.test")
    return ComfyClient(
        "http://comfy.test",
        device=kwargs.pop("device", "cuda:1 (RTX 3090)"),
        model=kwargs.pop("model", "wan2.2-ti2v-5b"),
        client=http,
    )


def test_queue_and_wait_returns_video(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "abc"})
        if request.url.path == "/history/abc":
            return httpx.Response(
                200,
                json={
                    "abc": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "9": {
                                "gifs": [{"filename": "c.mp4", "subfolder": "", "type": "output"}]
                            }
                        },
                    }
                },
            )
        return httpx.Response(404)

    client = make_client(handler)
    prompt_id = client.queue_prompt({"1": {"class_type": "X", "inputs": {}}})
    files = client.wait(prompt_id, poll_interval_s=0, timeout_s=5)
    assert [f.filename for f in files] == ["c.mp4"]


def test_oom_raises_vram_error_with_device_model_resolution():
    """Hard rule 5: OOM must name the device, model and resolution."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, text="Allocation on device: CUDA out of memory. Tried to allocate 8.00 GiB"
        )

    client = make_client(handler, device="cuda:0 (RTX 5090)", model="ltx-2.5-fp8")
    graph = {
        "6": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": 1280, "height": 720}}
    }
    with pytest.raises(VRAMError) as exc:
        client.queue_prompt(graph)
    message = str(exc.value)
    assert "cuda:0 (RTX 5090)" in message
    assert "ltx-2.5-fp8" in message
    assert "1280x720" in message
    assert "CPU offload" in message, "the error must say it is not silently offloading"


def test_non_oom_failure_is_a_plain_generation_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="node 7: required input missing")

    with pytest.raises(GenerationError) as exc:
        make_client(handler).queue_prompt({})
    assert not isinstance(exc.value, VRAMError)


def test_timeout_names_the_job_and_device():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "slow"})
        return httpx.Response(200, json={})

    client = make_client(handler)
    with pytest.raises(GenerationError, match="slow"):
        client.wait("slow", poll_interval_s=0, timeout_s=0)


def test_completed_with_no_output_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"j": {"status": {"completed": True}, "outputs": {}}})

    with pytest.raises(GenerationError, match="no video output"):
        make_client(handler).wait("j", poll_interval_s=0, timeout_s=5)


def test_video_output_wins_over_preview_image():
    files = _collect_outputs(
        {
            "8": {"images": [{"filename": "preview.png", "subfolder": "", "type": "output"}]},
            "9": {"gifs": [{"filename": "clip.mp4", "subfolder": "", "type": "output"}]},
        }
    )
    assert [f.filename for f in files] == ["clip.mp4"]


def test_ping_reports_false_instead_of_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert make_client(handler).ping() is False
