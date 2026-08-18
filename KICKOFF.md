# KICKOFF.md

## The thesis

Animal-POV video is a proven format with real precedent — channels in this exact lane
have pulled millions of views on AI-generated animal POV and animal-story content.
The bet is that a locally-run pipeline with genuine effort on realism and per-episode
storytelling can reach €100–500/month within 6–12 months, with upside beyond that if
the format lands.

The bet is NOT that volume wins. Volume without a human fingerprint is the documented
failure mode — channels at real scale have been demonetized channel-wide for exactly
that. Quality per episode is the moat.

## M0 — THE HARD GATE

**One finished video. One species. End to end. Nothing else gets built until this ships.**

Success criteria — all must be true:

1. A 60–90 second finished file exists in `output/final/`.
2. It was produced by the pipeline, not by hand in ComfyUI's web UI.
3. It contains at least 8 distinct generated clips, stitched with audio.
4. Watching it cold, it passes the honest thumb test: *would this stop me scrolling
   if it appeared on my TikTok feed?* Not "is this impressive for local AI" — the
   actual bar, judged against the real feed.
5. Clip reject rate is logged. If >70% of generations are unusable, the pipeline is
   not viable at daily cadence and the plan changes before scaling.

Criterion 4 is the real gate. Local open-weight models are weakest exactly where this
format is most demanding — fur detail, water, consistent lighting across a clip. If
the output does not clear the bar, the honest options are: switch to a stylized
(non-photoreal) direction, or budget cloud generation for hero shots. Both are fine.
Grinding out daily uploads that do not clear the bar is not.

**Chosen M0 species:** **desert tortoise** — committed, defined in
`config/species/desert_tortoise.toml`.

Terrestrial, broad daylight, medium-scale, slow deliberate motion. No water surfaces
and no fur close-ups: keratin shell and scaled forelimbs are hard-edged surfaces that
local models render far better than fur. Slow locomotion also gives temporal coherence
the easiest possible job. This is deliberately the forgiving case — if criterion 4
fails here, it fails on everything harder, which is exactly what M0 is for.

**Runbook:** `docs/M0_RUNBOOK.md`. The pipeline is built and tested end to end against
a stubbed ComfyUI; what remains is the run on real hardware and the honest verdict on
criterion 4.

## M1 — Pipeline hardening

Only after M0 passes.

- Batch orchestration across both GPUs in parallel, with clean job routing
- Automated QC pass (artifact/drift detection) to cut manual review time
- Prompt generator producing genuinely varied micro-narratives per episode
- Target: 1 finished video per ~1 hour of hands-on time, generation running unattended

## M2 — Cadence and catalog

- 3–5 species covered, each with a distinct narrative structure
- Upload cadence established and sustained
- Thumbnail/title workflow
- Disclosure checklist wired into the publish step

## M3 — Monetization gate

- 1,000 subscribers + 4,000 watch hours in trailing 12 months
- For a 6-min video at ~3 min average view duration, this is roughly 80k total views
- This is the real bottleneck, not RPM. Revenue is zero until it clears.

## Out of scope (for now)

- Multi-language dubbing
- Shorts-specific vertical variants (revisit at M2)
- Any cloud generation (revisit only if M0 fails criterion 4)
- Merch, sponsorships, anything downstream of a channel that does not exist yet

## Known risks, stated plainly

- **Demonetization risk is real and channel-wide.** A ~588K-subscriber AI storytelling
  channel reportedly earning ~$30K/month was demonetized entirely in early 2026. The
  mitigation is per-episode variety and genuine editorial input, enforced in code.
- **Photoreal is the hardest tier to hit locally.** M0 criterion 4 exists to find out
  cheaply, before months of effort.
- **Revenue is back-loaded.** Months 1–4 are likely zero. The catalog compounds; the
  first quarter does not.
