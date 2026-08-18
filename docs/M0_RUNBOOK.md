# M0 runbook

One finished video. One species. End to end. Nothing else gets built until this ships.

This is the checklist for actually running the gate on the WSL2 box. The code is done;
what it cannot do for you is criterion 4.

## 0. Before you start

- [ ] `nvidia-smi` shows **both** cards from inside WSL
- [ ] two ComfyUI processes are up, one per card:
      `CUDA_VISIBLE_DEVICES=0 python main.py --port 8188` and
      `CUDA_VISIBLE_DEVICES=1 python main.py --port 8189`
- [ ] `uv run python -m src.generation.comfy_client` reports both reachable
- [ ] `workflows/*.api.json` are **your** exports, not the shipped placeholders, and
      each `.map.json` points at real node ids (`WorkflowTemplate.load` will tell you
      loudly if not)
- [ ] the models named in `config/pipeline.toml` are downloaded and load in ComfyUI by
      hand at least once — debug model paths in ComfyUI, not through this pipeline
- [ ] `ffmpeg` is installed
- [ ] optional: an ambient bed at `reference/desert_ambient_wind.wav`

## 1. Plan

```bash
uv run python -m src.pipeline --stage plan --species desert_tortoise
```

Read the plan before spending GPU hours on it. Eight shots, a named structure, one beat
per shot, no repeated beat text. If the prompts read like eight variations of one shot,
the beat pools in `config/species/desert_tortoise.toml` are too thin — widen them.
Do not lower the variety thresholds (hard rule 2).

Note the `episode_id`; every later stage takes it.

## 2. Generate

```bash
uv run python -m src.pipeline --stage generate --episode-id <id>
```

Expect this to be the slow part. Watch for:

- **`clip.oom`** — the run aborts by design. Lower `max_resolution` for that queue or
  `clip_seconds` in `config/pipeline.toml`. Do not enable CPU offload to make it fit.
- **`router.downgrade`** — a queue was unreachable and hero shots went to the 3090.
  Fix the queue and regenerate those shots; do not ship a downgraded hero shot at M0,
  because it makes criterion 4 unanswerable.
- **`clip.failed`** — one shot died, the run continued. Fine, as long as you still
  clear 8 approved clips.

## 3. QC

```bash
uv run python -m src.pipeline --stage qc --episode-id <id>
```

The automatic pass throws out frozen clips, black clips, wrong durations and temporal
thrash. Then it asks you, per surviving clip, one question — and it is the M0 question:

> would this stop me scrolling if it appeared on my TikTok feed?

Not "is this impressive for local AI". Answer as the feed would. Do not use
`--auto-only` for the M0 run; it exists for M1 batches and it stamps every verdict as
unreviewed.

At the end, read the reject rate. It is appended to `output/qc_reject_rate.jsonl`.
**Above 70% and M0 has already told you something** (criterion 5): the pipeline is not
viable at daily cadence, and the plan changes before anything scales.

## 4. Assemble

```bash
uv run python -m src.pipeline --stage assemble --episode-id <id>
```

Fails if fewer than 8 clips were approved (criterion 3) and warns if the runtime lands
outside 60–90s (criterion 1). Output goes to `output/final/<id>.mp4` with a
`.disclosure.json` beside it and disclosure tags in the container.

## 5. The verdict

Watch the finished file cold, ideally a day later, ideally on a phone.

- **Passes criterion 4** → M0 is cleared. Record the reject rate and the wall-clock
  hands-on time; both are the M1 baseline. Move to M1.
- **Fails criterion 4** → this is a successful M0, not a failed one. It cost one
  episode instead of three months. The honest options per KICKOFF.md are: switch to a
  stylized non-photoreal direction, or budget cloud generation for hero shots. Both
  are fine. Grinding out daily uploads below the bar is not.

## 6. Before anything is uploaded

- [ ] YouTube: "Altered or synthetic content" ticked in the upload flow
- [ ] TikTok: AI-generated content toggle enabled
- [ ] disclosure line is the first line of the description

The checklist is also written into every `output/final/<id>.disclosure.json`.
`src/assembly/disclosure.py:verify` runs on every render and refuses to let an
undisclosed file out of assembly, but the platform-side toggles are still a human
action at M0 — there is no publish step yet (that is M2).
