# workflows/

ComfyUI workflows in **API format** (`Workflow -> Export (API)`), one pair per file:

| file | what it is |
|---|---|
| `<name>.api.json` | the graph, sent verbatim to `POST /prompt`. Never hand-edit. |
| `<name>.map.json` | which node ids hold the prompt, seed, resolution and frame count |

The `.api.json` file is hashed (sha256) into every clip's sidecar, so it must stay
byte-identical to what ComfyUI exported — that is what makes a good clip reproducible
(hard rule 4). All per-clip patching happens through the map file instead.

## These two files are placeholders

`wan22_bulk.api.json` and `ltx25_hero.api.json` are structurally valid graphs with the
right shape, but the exact node classes, model filenames and sampler settings depend on
your ComfyUI version and which custom nodes you have installed. **Replace both with real
exports from your own ComfyUI before the first real run**, then update the matching
`.map.json` node ids. `WorkflowTemplate.load()` validates that every node id and input
name in the map actually exists in the graph, so a stale map fails immediately and
loudly rather than silently generating with an unpatched prompt.

The placeholders assume [VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite)
for `VHS_VideoCombine`. If you save video another way, point `fps` at whatever node
carries the frame rate and make sure the graph ends in a node that writes a video file —
`ComfyClient.wait()` raises if a completed job produced no video output.

## Patch points

`positive_prompt` and `seed` are required. `negative_prompt`, `width`, `height`,
`length` and `fps` are optional — omit one and the graph's own value is used.
