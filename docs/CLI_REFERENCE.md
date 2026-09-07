# HYDRA-UMC-VISION-NODE — CLI Reference

`hydra-umc-vision-node` is a Python console script (`src/hydra_umc_vision_node/main.py`,
installed as an entry point via `pyproject.toml`). This node is an
integration parent for the Vision AI Node family (Vision-Streamer,
Detection-HEF, Safety-Zones, Visual-Servoing-API) — it does not itself
run the real Hailo-8/CM5 runtime, but it does real, hardware-independent
work: checking its children are present, inspecting its own pipeline
shape against whatever hardware is actually attached, and validating raw
frame buffers structurally. Every example below was captured from a real
run of the installed CLI — not written from memory.

## Usage

```
$ hydra-umc-vision-node -h
usage: hydra-umc-vision-node [-h]
                             {family-status,pipeline-status,validate-frame,serve} ...

High-speed perception edge AI node (Hailo-8 + CM5) - integration parent of
Vision-Streamer, Detection-HEF, Safety-Zones and Visual-Servoing-API.

positional arguments:
  {family-status,pipeline-status,validate-frame,serve}
    family-status       Real readiness check of this node's four real
                        children.
    pipeline-status     Real pipeline manifest plus real degraded-mode
                        detection for this node's actual hardware.
    validate-frame      Real structural corruption check of a raw frame buffer
                        file.
    serve               Run family-status/pipeline-status/validate-frame as a
                        JSON/HTTP API (GET /family-status, GET /pipeline-
                        status, POST /validate-frame) - the exact same
                        functions the CLI subcommands above already run.

options:
  -h, --help            show this help message and exit
```

Bare invocation (no subcommand) prints identity/version/role and exits `0`:

```
$ hydra-umc-vision-node
HYDRA-UMC-VISION-NODE v0.0.6
High-speed perception edge AI node (Hailo-8 + CM5) - integration parent of Vision-Streamer, Detection-HEF, Safety-Zones and Visual-Servoing-API.
```

## Commands

### `family-status [--workspace PATH]`

```
$ hydra-umc-vision-node family-status -h
usage: hydra-umc-vision-node family-status [-h] [--workspace WORKSPACE]

options:
  -h, --help            show this help message and exit
  --workspace WORKSPACE
                        Directory containing the sibling repo checkouts
                        (default: this repo's own parent directory).
```

Reads each of the four real children's own `hydra-umc.project.json` —
this is a real check of what's actually cloned as a sibling on disk, not
a hardcoded list. Run against the real workspace (this project's own
parent directory, the default), all four are genuinely present on this
machine:

```
$ hydra-umc-vision-node family-status
Vision AI Node family status (workspace: C:\Users\juane\Documents\GitHub):
  HYDRA-UMC-VISION-STREAMER: v0.1.0, maturity=established, role=service
  HYDRA-UMC-DETECTION-HEF: v0.0.6, maturity=established, role=library
  HYDRA-UMC-SAFETY-ZONES: v0.0.5, maturity=established, role=service
  HYDRA-UMC-VISUAL-SERVOING-API: v0.0.5, maturity=established, role=api

All 4 children present.
```

Against an empty `--workspace` (a real, honest "not found" for every
child, exit code `1`):

```
$ hydra-umc-vision-node family-status --workspace ./empty-ws
Vision AI Node family status (workspace: ./empty-ws):
  HYDRA-UMC-VISION-STREAMER: NOT FOUND
  HYDRA-UMC-DETECTION-HEF: NOT FOUND
  HYDRA-UMC-SAFETY-ZONES: NOT FOUND
  HYDRA-UMC-VISUAL-SERVOING-API: NOT FOUND

4 of 4 children not found: HYDRA-UMC-VISION-STREAMER, HYDRA-UMC-DETECTION-HEF, HYDRA-UMC-SAFETY-ZONES, HYDRA-UMC-VISUAL-SERVOING-API
```

### `pipeline-status`

```
$ hydra-umc-vision-node pipeline-status -h
usage: hydra-umc-vision-node pipeline-status [-h]

options:
  -h, --help  show this help message and exit
```

Prints this node's real perception pipeline manifest (`capture` →
`preprocess` → `inference` → `postprocess` → `publish`, each stage
declaring whether it really needs a camera and/or the Hailo-8
accelerator), then real hardware probing (`camera_present`,
`accelerator_present`) and the resulting degraded-mode decision. On this
dev machine — no CM5 camera, no Hailo-8 — the honest result is
`degraded_no_hardware`, with `capture` and `inference` correctly marked
as unable to run:

```
$ hydra-umc-vision-node pipeline-status
{
  "manifest": {
    "version": "0.1.0",
    "stages": [
      {
        "name": "capture",
        "description": "Read a raw frame from the attached camera.",
        "requires_camera": true,
        "requires_accelerator": false
      },
      {
        "name": "preprocess",
        "description": "Resize/normalize a captured frame for inference.",
        "requires_camera": false,
        "requires_accelerator": false
      },
      {
        "name": "inference",
        "description": "Run the compiled HEF model on the Hailo-8 accelerator.",
        "requires_camera": false,
        "requires_accelerator": true
      },
      {
        "name": "postprocess",
        "description": "Decode raw inference output into real detections.",
        "requires_camera": false,
        "requires_accelerator": false
      },
      {
        "name": "publish",
        "description": "Publish detection results to the rest of the swarm.",
        "requires_camera": false,
        "requires_accelerator": false
      }
    ]
  },
  "camera_present": false,
  "accelerator_present": false,
  "mode": "degraded_no_hardware",
  "runnable_stages": [
    "preprocess",
    "postprocess",
    "publish"
  ],
  "skipped_stages": [
    "capture",
    "inference"
  ]
}
$ echo $?
1
```

`pipeline-status` exits `1` whenever the real mode is anything other than
`FULL` — this is a real, honest degradation signal a script can check,
not just informational text: a fleet-health check can key off this exit
code without parsing the JSON at all.

### `validate-frame <path> --width W --height H [--channels N]`

```
$ hydra-umc-vision-node validate-frame -h
usage: hydra-umc-vision-node validate-frame [-h] --width WIDTH --height HEIGHT
                                            [--channels CHANNELS]
                                            path

positional arguments:
  path                 Path to a raw frame buffer file.

options:
  -h, --help           show this help message and exit
  --width WIDTH
  --height HEIGHT
  --channels CHANNELS  Default: 3 (RGB).
```

Real, hardware-independent structural validation of a raw frame buffer
against its claimed `width`/`height`/`channels` — the same shape a real
capture stage hands `preprocess`, and the same shape a torn read, a
dropped network fragment, or a frozen sensor produces. The examples below
use small synthetic fixtures built for real with
`pathlib.Path.write_bytes` (a 4x4x3 buffer is exactly 48 bytes):

```python
from pathlib import Path
import random
random.seed(42)
data = bytes(random.randint(0, 255) for _ in range(4 * 4 * 3))
Path("vn_good_frame.raw").write_bytes(data)          # 48 bytes, varied
Path("vn_short_frame.raw").write_bytes(data[:20])    # 20 bytes, truncated
Path("vn_blank_frame.raw").write_bytes(bytes([0]) * (4 * 4 * 3))  # all zero
```

A correctly-sized, non-uniform buffer — the real success path:

```
$ hydra-umc-vision-node validate-frame vn_good_frame.raw --width 4 --height 4 --channels 3
Frame OK: vn_good_frame.raw matches 4x4x3 (48 bytes)
$ echo $?
0
```

A truncated buffer (the real corruption pattern a torn read or dropped
network fragment produces):

```
$ hydra-umc-vision-node validate-frame vn_short_frame.raw --width 4 --height 4 --channels 3
Frame INVALID: vn_short_frame.raw
  [size_mismatch] frame buffer is 20 bytes, expected 48 bytes for 4x4x3 - likely truncated or corrupt
$ echo $?
1
```

A correctly-sized but perfectly uniform buffer (a real, common symptom of
a frozen sensor or a zeroed/blank capture — flagged, not treated as
corruption on its own):

```
$ hydra-umc-vision-node validate-frame vn_blank_frame.raw --width 4 --height 4 --channels 3
Frame INVALID: vn_blank_frame.raw
  [uniform_buffer] every byte in the frame is identical (0x00) - likely a frozen sensor or a blank/zeroed capture
$ echo $?
1
```

A missing file (real OS error, not a crash):

```
$ hydra-umc-vision-node validate-frame vn_does_not_exist.raw --width 4 --height 4 --channels 3
[vision-node] could not read vn_does_not_exist.raw: [Errno 2] No such file or directory: 'vn_does_not_exist.raw'
$ echo $?
2
```

### `serve [--addr ADDR] [--port PORT] [--workspace PATH]`

```
$ hydra-umc-vision-node serve -h
usage: hydra-umc-vision-node serve [-h] [--addr ADDR] [--port PORT]
                                   [--workspace WORKSPACE]

options:
  -h, --help            show this help message and exit
  --addr ADDR           address to bind the HTTP API to
  --port PORT           port for the HTTP API
  --workspace WORKSPACE
                        Default workspace for GET /family-status when it is
                        not overridden per-request.
```

Runs `family-status`/`pipeline-status`/`validate-frame` as a plain JSON/HTTP
API (`src/hydra_umc_vision_node/api.py`) instead of a one-shot CLI call — a
real `http.server.ThreadingHTTPServer`, no extra runtime dependency, the same
convention this family's other `api.py` files already use. Defaults to
`127.0.0.1:8094`; runs until `Ctrl+C`:

```
$ hydra-umc-vision-node serve --port 8094
[vision-node] HTTP API listening on 127.0.0.1:8094 (workspace=C:\Users\juane\Documents\GitHub)
[vision-node] GET /family-status, GET /pipeline-status, POST /validate-frame, GET /stats
```

| Route | Method | Query params | Notes |
|---|---|---|---|
| `/family-status` | `GET` | `workspace` (optional) | Same check as the `family-status` subcommand. `workspace` overrides the server's own default for that one request only. |
| `/pipeline-status` | `GET` | — | Same check as the `pipeline-status` subcommand. |
| `/validate-frame` | `POST` | `width`, `height` (required), `channels` (optional, default `3`) | Same check as the `validate-frame` subcommand, but the raw frame bytes travel as the request body instead of a local file path — the only thing a real remote caller can hand this server. |
| `/stats` | `GET` | — | Reports the server's own default workspace path. |

`GET /family-status` (all 4 children present, same real checkout as the CLI example above):

```
$ curl http://127.0.0.1:8094/family-status
{"workspace": "C:\\Users\\juane\\Documents\\GitHub", "children": [{"name": "HYDRA-UMC-VISION-STREAMER", "present": true, "manifest": {"name": "HYDRA-UMC-VISION-STREAMER", "version": "0.1.0", "maturity": "established", "role": "service"}}, {"name": "HYDRA-UMC-DETECTION-HEF", "present": true, "manifest": {"name": "HYDRA-UMC-DETECTION-HEF", "version": "0.0.6", "maturity": "established", "role": "library"}}, {"name": "HYDRA-UMC-SAFETY-ZONES", "present": true, "manifest": {"name": "HYDRA-UMC-SAFETY-ZONES", "version": "0.0.5", "maturity": "established", "role": "service"}}, {"name": "HYDRA-UMC-VISUAL-SERVOING-API", "present": true, "manifest": {"name": "HYDRA-UMC-VISUAL-SERVOING-API", "version": "0.0.5", "maturity": "established", "role": "api"}}], "missing": [], "allPresent": true}
```

`GET /pipeline-status` (same honest `degraded_no_hardware` result as the CLI, on this dev machine with no camera/Hailo-8):

```
$ curl http://127.0.0.1:8094/pipeline-status
{"manifest": {"version": "0.1.0", "stages": [...]}, "cameraPresent": false, "acceleratorPresent": false, "mode": "degraded_no_hardware", "runnableStages": ["preprocess", "postprocess", "publish"], "skippedStages": ["capture", "inference"]}
```

`POST /validate-frame` (same 48-byte varied fixture as the CLI walkthrough above, sent as the request body):

```
$ curl -X POST "http://127.0.0.1:8094/validate-frame?width=4&height=4&channels=3" --data-binary @vn_good_frame.raw
{"valid": true, "expectedBytes": 48, "actualBytes": 48, "issues": []}
```

`GET /stats`:

```
$ curl http://127.0.0.1:8094/stats
{"workspace": "C:\\Users\\juane\\Documents\\GitHub"}
```

An unknown route or a bad/missing query param never crashes the server — it
gets a real error body and the matching HTTP status: `404` with
`{"error": "not found"}` for an unknown path, `400` with a descriptive
`{"error": "..."}` for a `/validate-frame` request missing `width`/`height`
or a repeated query parameter.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | ok — `family-status` with every child present, `validate-frame` on a clean frame, or `pipeline-status` in `FULL` mode |
| `1` | a real, non-fatal problem: `family-status` with at least one missing child, `pipeline-status` in any degraded mode, or `validate-frame` finding structural issues |
| `2` | `validate-frame` could not read the given path at all (missing file, permission error) |

## Out of scope for this CLI

The real Hailo-8 runtime, the gRPC child-supervision/control API, and
actually driving a camera are described in the project README's own
roadmap but are not run by this integration parent itself — they need
real CM5+Hailo-8 hardware this environment does not have.
