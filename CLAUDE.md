# CLAUDE.md

Persistent context for Claude Code. Read this before touching anything.

## What this is

A local-first pipeline that generates photorealistic animal-POV video clips, QCs them,
and assembles them into finished YouTube/TikTok uploads. Everything runs on local
hardware. No per-generation cloud costs.

The product is a channel of short-form and mid-form videos: "what a crocodile sees
underwater", "ant-scale view of a lawn", "mountain goat on a ledge". The camera is
attached to the animal. No narration by default — ambient sound and visual storytelling.

## Hardware

- **RTX 5090 (32GB)** — `CUDA_VISIBLE_DEVICES=0` — hero shots, LTX-2.5 FP8
- **RTX 3090 (24GB)** — `CUDA_VISIBLE_DEVICES=1` — bulk/B-roll, Wan 2.2 TI2V-5B
- Running under WSL2 Ubuntu. NVIDIA driver lives on the Windows host, CUDA toolkit
  inside WSL. `nvidia-smi` must show both cards before any pipeline work.

These are two independent generation queues. Diffusion video models do not shard
cleanly across consumer cards without NVLink — never write code that assumes one
logical device spanning both GPUs.

## Model choices and why

- **Wan 2.2** (Apache 2.0) — default workhorse. Fully unrestricted commercial use,
  which matters because this channel is monetized. TI2V-5B fits the 3090 comfortably.
- **LTX-2.5** — quality tier on the 5090 with FP8 quantization. Native audio+video in
  one pass. Check license terms before it touches a monetized upload.
- **Avoid HunyuanVideo** for anything shipped — license restrictions make it a bad fit
  for commercial output. Fine for experiments only.

Model licenses get re-verified before any new model enters the shipping pipeline.
"Open weights" does not automatically mean "may sell the output".

## Architecture

```
prompt gen  ->  ComfyUI API (2 queues)  ->  QC pass  ->  assembly  ->  final
   LLM            Wan 2.2 / LTX-2.5       auto+manual    ffmpeg      upload-ready
```

ComfyUI is driven over its HTTP API, not the web UI. Workflows live in `workflows/`
as exported API-format JSON. The Python layer queues jobs, polls for completion,
and moves outputs into `output/raw/`.

## Hard rules

1. **Never bypass the QC gate.** Generated clips go to `output/raw/`, and only reach
   `output/approved/` by passing QC. Assembly reads exclusively from `approved/`.
2. **Every video needs a distinct micro-narrative.** Swapping the species name in an
   otherwise identical template is the exact pattern that gets channels demonetized
   under YouTube's inauthentic-content policy. Structure, pacing, and beat must vary
   per episode. The prompt generator enforces this; do not simplify it away.
3. **AI disclosure is mandatory and non-negotiable.** This content is photorealistic
   by design, which is precisely the trigger for YouTube's altered/synthetic content
   label and TikTok's AIGC label. Both platforms have stated the label does not reduce
   distribution. Every upload gets labeled. No exceptions, no "test without the label".
4. **Determinism where possible.** Log the seed, model, workflow hash, and full prompt
   for every clip in `output/raw/<clip_id>.json`. When something looks great, it must
   be reproducible.
5. **Fail loud on VRAM.** OOM should raise with the device, model, and resolution in
   the message — not silently fall back to CPU offload and take 40 minutes.

## Conventions

- Python 3.11+, `uv` for dependency management.
- Config in `config/`, never hardcoded paths or magic numbers in `src/`.
- Every module runnable standalone via `python -m src.<module>` for debugging.
- Logging via `structlog` to stdout, JSON in non-interactive runs.
- No secrets in the repo. `.env` is gitignored.

## Where the code lives

| concern | module | entry point |
|---|---|---|
| config + species | `src/config.py` | `python -m src.config` |
| episode planning | `src/prompts/generator.py` | `python -m src.prompts.generator` |
| ComfyUI HTTP | `src/generation/comfy_client.py` | `python -m src.generation.comfy_client` |
| GPU routing | `src/generation/router.py` | `python -m src.generation.router` |
| clip generation | `src/generation/runner.py` | `python -m src.generation.runner` |
| QC gate | `src/qc/gate.py` | `python -m src.qc.gate` |
| final render | `src/assembly/build.py` | `python -m src.assembly.build <episode_id>` |
| M0 chain | `src/pipeline.py` | `python -m src.pipeline` |

Where the hard rules are enforced in code, so they survive refactors:

1. QC gate — `src/qc/gate.py` is the only writer of `output/approved/`;
   `src/assembly/build.py:collect` reads nothing else.
2. Micro-narrative variety — `src/prompts/generator.py` (structure cooldown + beat
   overlap ceiling, checked against `output/episode_ledger.jsonl`). It raises
   `VarietyError` rather than emitting a near-duplicate.
3. Disclosure — `src/assembly/disclosure.py:verify` runs on every render, and
   `load_config()` refuses a config with disclosure switched off.
4. Determinism — `src/generation/runner.py` writes the sidecar; the workflow sha256
   comes from the untouched `.api.json`, which is why patching goes through a
   separate `.map.json`.
5. VRAM — `src/errors.py:VRAMError`, raised from `comfy_client._raise_for_body`.

## Current phase

See `KICKOFF.md`. Do not build ahead of the current milestone. M0 is a hard gate.
The step-by-step for running it is `docs/M0_RUNBOOK.md`.
