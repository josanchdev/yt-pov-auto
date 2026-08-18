from __future__ import annotations

import json

import pytest

from src.errors import WorkflowError
from src.generation.workflow import WorkflowTemplate, frame_count


def test_load_and_patch(cfg):
    tpl = WorkflowTemplate.load(cfg.paths.workflows, "wan22_bulk.api.json")
    graph = tpl.build(
        positive_prompt="a tortoise",
        negative_prompt="blurry",
        seed=1234,
        width=960,
        height=544,
        length=97,
        fps=24,
    )
    assert graph["4"]["inputs"]["text"] == "a tortoise"
    assert graph["5"]["inputs"]["text"] == "blurry"
    assert graph["7"]["inputs"]["seed"] == 1234
    assert graph["6"]["inputs"]["width"] == 960
    assert graph["9"]["inputs"]["frame_rate"] == 24


def test_build_does_not_mutate_the_template(cfg):
    tpl = WorkflowTemplate.load(cfg.paths.workflows, "wan22_bulk.api.json")
    tpl.build(positive_prompt="x", seed=1)
    assert tpl.graph["4"]["inputs"]["text"] == "PLACEHOLDER POSITIVE PROMPT"


def test_hash_is_stable_and_distinguishes_workflows(cfg):
    a = WorkflowTemplate.load(cfg.paths.workflows, "wan22_bulk.api.json")
    b = WorkflowTemplate.load(cfg.paths.workflows, "wan22_bulk.api.json")
    c = WorkflowTemplate.load(cfg.paths.workflows, "ltx25_hero.api.json")
    assert a.sha256 == b.sha256 and len(a.sha256) == 64
    assert a.sha256 != c.sha256


def test_stale_map_fails_loudly(cfg, tmp_path):
    src_graph = (cfg.paths.workflows / "wan22_bulk.api.json").read_text()
    (tmp_path / "x.api.json").write_text(src_graph)
    (tmp_path / "x.map.json").write_text(
        json.dumps(
            {
                "positive_prompt": {"node": "999", "input": "text"},
                "seed": {"node": "7", "input": "seed"},
            }
        )
    )
    with pytest.raises(WorkflowError, match="does not exist"):
        WorkflowTemplate.load(tmp_path, "x.api.json")


def test_missing_map_fails_loudly(cfg, tmp_path):
    (tmp_path / "y.api.json").write_text((cfg.paths.workflows / "wan22_bulk.api.json").read_text())
    with pytest.raises(WorkflowError, match="patch map"):
        WorkflowTemplate.load(tmp_path, "y.api.json")


@pytest.mark.parametrize(
    "seconds,fps,expected", [(8, 24, 193), (4, 24, 97), (1, 24, 25), (7.5, 24, 181)]
)
def test_frame_count_is_4n_plus_1(seconds, fps, expected):
    frames = frame_count(seconds, fps)
    assert frames == expected
    assert (frames - 1) % 4 == 0
