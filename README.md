# animal-pov

Local-first pipeline for generating photorealistic animal-POV video content.

Runs entirely on local GPUs (RTX 5090 + RTX 3090) under WSL2. Generates clips via
ComfyUI, QCs them, and assembles finished uploads with ffmpeg.

## Status

M0 — see `KICKOFF.md`. The pipeline is built end to end and tested against a stubbed
ComfyUI; **no episode has cleared the M0 gate yet.** Nothing ships until one does.

Committed M0 species: **desert tortoise** (`config/species/desert_tortoise.toml`) —
terrestrial, daylight, hard-surfaced, no water and no fur close-ups. The reasoning is
in the config file's header comment.

## Setup

```bash
# verify GPUs are visible inside WSL first — both cards must appear
nvidia-smi

uv sync --extra dev
cp .env.example .env   # then fill in COMFYUI_HOST etc.
sudo apt install ffmpeg

# one ComfyUI process per card, each on its own port
CUDA_VISIBLE_DEVICES=0 python main.py --port 8188   # 5090, hero shots
CUDA_VISIBLE_DEVICES=1 python main.py --port 8189   # 3090, bulk / B-roll
```

Then replace the placeholder workflows with real API-format exports from your own
ComfyUI — see `workflows/README.md`. Nothing generates correctly until you do.

## Running it

```bash
# full M0 chain: plan -> generate -> QC (interactive) -> assemble
uv run python -m src.pipeline --species desert_tortoise

# or one stage at a time
uv run python -m src.pipeline --stage plan
uv run python -m src.pipeline --stage generate --episode-id desert_tortoise_20260818_101500
uv run python -m src.pipeline --stage qc       --episode-id desert_tortoise_20260818_101500
uv run python -m src.pipeline --stage assemble --episode-id desert_tortoise_20260818_101500
```

Every module also runs standalone for debugging:

```bash
uv run python -m src.config                     # what config is actually loaded
uv run python -m src.generation.comfy_client    # are both ComfyUI instances up?
uv run python -m src.generation.router          # how would this episode be routed?
uv run python -m src.prompts.generator          # plan an episode, print the JSON
uv run python -m src.qc.gate --episode-id <id>  # review output/raw/
uv run python -m src.assembly.build <id>        # render from output/approved/
```

QC asks for a human verdict on every clip that survives the automatic checks.
`--auto-only` skips that for unattended batches and stamps every resulting verdict as
unreviewed, so an unreviewed clip is never mistaken for a reviewed one.

## Layout

```
src/generation/   ComfyUI API client, job queueing, GPU routing
src/prompts/      per-episode prompt + micro-narrative generation
src/qc/           artifact/drift detection, approve/reject
src/assembly/     ffmpeg stitching, audio, final render
workflows/        ComfyUI workflows exported in API format (+ patch maps)
config/           model configs, species definitions, pipeline settings
reference/        ambient audio beds and other local assets (gitignored)
output/           raw -> approved|rejected -> final
```

Runtime state that lives in `output/` and is worth knowing about:

| file | what it is |
|---|---|
| `raw/<clip_id>.json` | seed, model, workflow hash, full prompt — hard rule 4 |
| `episode_ledger.jsonl` | every episode planned, so the planner can refuse to repeat itself |
| `qc_reject_rate.jsonl` | reject rate per QC run — M0 criterion 5 |
| `final/<id>.disclosure.json` | the AI-disclosure block for that render |

## Tests

```bash
uv run pytest              # full suite; needs ffmpeg
uv run pytest -m "not slow" # skips the full-length renders
uv run ruff check src tests
```

The suite fakes only ComfyUI's two HTTP endpoints. Clips, QC and the final render are
real ffmpeg work, so `tests/test_pipeline_e2e.py` exercises the actual M0 chain.

## Disclosure

All output from this pipeline is AI-generated and is labeled as such on every platform
it is published to. See `CLAUDE.md` hard rule 3. Disclosure cannot be turned off in
config — `load_config()` refuses to load a config that tries.
