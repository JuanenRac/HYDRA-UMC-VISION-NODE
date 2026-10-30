# Changelog

All notable changes to this project are documented here.

## [Unreleased]

- **One naming for `pipeline-status`:** the command line and `GET /pipeline-status` answer with the same camelCase names (`cameraPresent`, `acceleratorPresent`, `runnableStages`, `skippedStages`, and `requiresCamera` / `requiresAccelerator` in each stage of the manifest). The snake_case names the command line used before stay as aliases with the same values for one release cycle.

## [0.1.7] - Frame specs with pixel format and stride, a frozen-frame watcher and a live family status

- **Frame specs with a pixel format and a stride, and a frozen-frame watcher:** `FrameSpec` may name a `pixel_format` (`GRAY`, `RGB`, `BGR`, `RGBA`, `BGRA`, `YUYV`, `NV12`) and a row `stride`, and the expected size follows them; an impossible combination (unknown format, odd width for `YUYV`, a stride smaller than a row) is reported as `invalid_spec`. A spec without them behaves as before. `FrozenFrameWatcher` flags a sensor that keeps returning byte-for-byte the same frame (`frozen_frame`), which a uniform-buffer check does not catch.
- **`family-status --live` asks the children for their health:** besides reading the manifests, every child that declares a service (port and health path in its manifest) is asked once with a short timeout, on loopback unless `--host` says otherwise; the command prints the latency and the version each service reports and fails when one does not answer. Only the port and path of the manifest are used, redirects are not followed and the answer is read up to a limit. Off by default.

- **README in the universal layout:** the README and its seven translations follow the layout that the four families share (22 sections in a fixed order), with an architecture picture and a repository-structure picture drawn from `docs/architecture.json` and `docs/structure.json`; the CI validator checks both pictures and the file tree.

- **The layout of the README is checked in the CI:** the validator runs `tools/universal_readme_check.py` and lists, as a warning, what differs from the universal layout of the README.
- The README in the eight languages describes the build scripts as they are: `build.sh` prints `Build complete.` and uses `python3`, `build.bat` prints `== Build OK ==` and uses `python`, and the local virtual environment is in the root of the repository, not under `build/`.

## [0.1.6] - Reading the code of a board and optional Bearer token for the HTTP API

- **Reading the code of a board.** `POST /read-code?width=W&height=H&channels=N` takes one raw frame as the body (like `/validate-frame`, with the same size limits, and with the token when there is one) and returns the DataMatrix codes in it as `{"codes": [...], "count": N}`, for the pick-and-place bridge that compares the code on a board with the board of its job. The decoder is `pylibdmtx`, an optional dependency imported on first use: without it the route answers `503` and says what to install, and the rest of the API is unaffected. A code is bounded (printable ASCII, at most 256 characters, at most 16 codes per frame) and a blank frame is read like any other. The reading of the frame body is shared by both routes. Tested with a fake decoder; not tried on a real label.
- The package description says that `os/` and `models/` are reserved and populated at deployment, not versioned here, like the READMEs already do (it said the project carries them).
- **Optional Bearer token for the HTTP API.** `serve --token-file FILE` (or `HYDRA_UMC_VISION_NODE_TOKEN`, at least 16 characters) makes every request carry `Authorization: Bearer <token>` (constant-time comparison); only `GET /stats`, the health path of the manifest, stays open. On loopback no token is needed; any other address is refused at start without one.
- The manifest declares the health route of the service (`/stats`), so the service registry, the ecosystem status of the server and the watchdog know where to probe it.
- Building no longer changes the version; raising it is the explicit `bump` command (`python tools/hydra_project_tool.py bump`). The build scripts only report the version they built, and CI fails if a build script runs a bump.
- The build and run scripts use the Python of the `.venv` directly instead of an activate script, and create the venv with the `py` launcher when there is one; CI fails if a script activates a venv.
- The banner follows the animated style of the ecosystem banners, with the name, the kind and the technologies of the project.
- Added `tools/hydra_project_tool.py`: the same `validate`, `test`, `version`, `status` and `bump` commands in every repository. The checks run with `HYDRA_UMC_CI=1` and fail if the version changes; only `bump` changes it.

## [0.1.5]

- Build version synchronized with `hydra-umc.project.json` and the repository-native version source.
- The licence is written as GPL-3.0-or-later in every header and in the package metadata.

## [0.1.4]

- Build version synchronized with `hydra-umc.project.json` and the repository-native version source.
- Every SPDX identifier, file header, the package metadata, the service units and the README licence line now say GPL-3.0-or-later consistently (the LICENSE file keeps the full GPL-3.0 text).

## [0.1.3] - Maturity lowered to functional, strict sibling-manifest fields

- Maturity is functional: the code, tests and documentation are in place, but nothing here has been verified on real hardware yet, so the manifest no longer claims the established level.
- **`api.py`'s `POST /validate-frame` no longer trusts a caller-controlled
  `Content-Length` with no upper bound** - found on code review. Unlike the sibling JSON endpoints this fix
  landed on elsewhere (HYDRA-UMC-ANOMALY-DETECTOR and others), there is no
  one fixed byte cap that makes sense for a raw frame buffer, since an
  honest frame's size varies with resolution - the request's own declared
  `width`/`height`/`channels` already say exactly how large it should ever
  need to be, so that is the bound instead. An over-limit request is
  drained before the clean 400 is sent, avoiding a real
  `ConnectionAbortedError` race where the client's own send() is still in
  flight when the handler closes the connection. New end-to-end regression
  test against a live server.
- **`.github/workflows/ci.yml`** - the real `tests/` pytest suite is now
  actually installed and run in CI, matching the fix already landed in
  sibling repos (e.g. HYDRA-UMC-SAFETY-ZONES, HYDRA-UMC-SEMANTIC-PLANNER).
  This repo's own baseline workflow only ever compile-checked
  (`py_compile`) `.py` files - it never ran `pytest`, so a regression in
  `tests/` could be merged without CI ever failing. CI-only fix, no
  runtime code changed.
- The family manifest reader now accepts only non-empty text values for
  `name`, `version`, `maturity` and `role`. Invalid JSON types no longer get
  silently coerced into strings and represented as a real child contract.

## [0.1.2] - Hardware probes under systemd, bounded frame validation, confined workspace

- **Hardware probes work under systemd:** the unit used `PrivateDevices=true`, which hides `/dev/video0` and `/dev/hailo0`, so on the real board `pipeline-status` always answered "degraded". The unit now keeps the real `/dev` and limits devices with `DevicePolicy=closed` and explicit `DeviceAllow` rules.
- **Frame validation can no longer exhaust memory:** the body limit came from client-supplied dimensions (two negative sizes gave a huge positive one). Dimensions are checked first (at most 8192x8192x4), the body is capped at 64 MiB and read in chunks.
- **`?workspace=` is confined:** a request may only look inside the workspace the server was started with.
- **Container workspace:** the image reads `HYDRA_UMC_WORKSPACE` (an installed package cannot derive it from its own path), and `serve` warns when the folder does not exist.
- **Compose map corrected:** it now publishes the real ports (8094, 8100, 8116) instead of unused gRPC ports, no longer mounts a `registry.json` that does not exist, mounts the sibling checkouts read-only, uses `latest` image tags and drops the obsolete `version` key.
- **Tests:** the pipeline test no longer depends on the machine having (or lacking) a camera, and new tests cover the bounds, the confined workspace and the unit file (66 tests).
- **Docs:** the gRPC badge is marked as planned, the contributing guide describes the real stack, and the README (all languages) documents the new limits.

## [0.1.1] - Idle connections dropped, accurate compose docs, Portuguese README

- **Idle connections are dropped:** the HTTP handler had no connection timeout, so a client that opened a connection and sent nothing held a handler thread forever. A silent client is now dropped after 15 seconds; the limit covers socket I/O only, never the time a check takes.
- **Tests:** a new test covers the dropped idle connection (61 tests).
- **Docs:** the README no longer says the four children ship no `Dockerfile` (they all do, and the compose file builds from them); it now says plainly that the stack has never been built or run with Docker. The test counts and the sample version line are corrected, and a README in Portuguese (`README_por.md`) joins the other translations.

## [0.1.0] - Compatibility record

- Added a compatibility record (model name, version and SHA-256, Hailo architecture, pipeline version) that round-trips through JSON and reports every reason it does not fit the node using it: architecture, pipeline version and, when given, the registry's recorded digest.

## [0.0.9] - This repo's own Dockerfile, plus real resource limits and a healthcheck for it

`docker-compose.yml`'s own `vision-node` service declared `build: .`, but this repo never actually had
a `Dockerfile` at its root - its own header comment already named this as the one remaining real gap
after all 4 children gained their own. New `Dockerfile` mirrors the real `--addr`/`--port` (8094) the
CM5 systemd unit already runs, non-root. Also added a real `mem_limit` (mirroring the same unit's own
`MemoryMax`) and a `healthcheck` against the real, already-existing `GET /stats` route.

## [0.0.8] - No more personal absolute paths in public docs

- `docs/CLI_REFERENCE.md`'s own real captured CLI/HTTP output examples
  named this machine's real local checkout path outright
  (`C:\Users\juane\Documents\GitHub`) - replaced with `<workspace-root>`
  throughout, keeping every example's actual output format unchanged.

## [0.0.7]

- **`docker-compose.yml` updated: all 4 children now have a real
  Dockerfile of their own** (HYDRA-UMC-VISION-STREAMER,
  HYDRA-UMC-DETECTION-HEF, HYDRA-UMC-SAFETY-ZONES,
  HYDRA-UMC-VISUAL-SERVOING-API) - the "still skeleton-stage... no
  Dockerfile" comment this file carried is now stale, removed. Fixed a
  real path mismatch found in the same pass: `detection-hef`'s own
  volume mounted `../HYDRA-UMC-DETECTION-HEF/build` at
  `/opt/hydra/hef-library`, but that repo's real CLI (and its new
  Dockerfile's own `CMD`) expects `--models-dir /opt/hydra/models` -
  corrected, and a real `registry.json` mount added alongside it.
  `vision-streamer`'s own service now sets a real per-camera `command:`
  (its image ships no default - `--device`/`--port` are real, required,
  per-invocation flags). `docker-compose.yml` parses as valid YAML;
  `docker compose config` itself not run (no Docker runtime on this dev
  machine). `vision-node` itself (this repo) still has no real
  Dockerfile/gRPC server of its own - the one real gap left in this file.

## [0.0.6] - The 0.0.5 workspace approach was unreadable by its own service account

- **`systemd/hydra-umc-vision-node.service`** - `--workspace` no longer
  points at a symlink to the real sibling-repo checkout root. Live-
  verified failure on the real CM5 this was first installed on: that
  checkout root lives under the operator's own home directory, itself
  `0700` (Debian's own default) - unreadable by this service's own
  unprivileged account no matter how `ProtectHome` is set (`read-only`
  still respects the real underlying permission bits; it only controls
  whether systemd hides `/home` from the unit, not who is allowed to
  read what's under it). `install_vision_node.sh` now copies out just
  the small `hydra-umc.project.json` for each expected child into a real
  `root:root 0755` tree under `/opt` instead - `ProtectHome` reverts to
  this family's usual `true`.

## [0.0.5] - Real v0: JSON/HTTP server mode, plus CM5 deployment

- **`api.py`** (new) - `GET /family-status`, `GET /pipeline-status`, and
  `POST /validate-frame` reach the exact same `family.py`/`hardware.py`/
  `frame.py` functions the CLI's own subcommands already run.
  `POST /validate-frame` takes the raw frame bytes as the request body
  rather than a server-side file path - the CLI's own `validate-frame`
  reads a *local* file because it runs on the same machine as the frame;
  a real remote caller has no such local path on this server's own
  filesystem to hand it. Real gap this closes: this project's own
  readiness/pipeline/frame-validation logic was only ever reachable as a
  one-shot CLI.
- **`main.py`** - new `serve` subcommand (`--workspace`/`--addr`/`--port`,
  default `127.0.0.1:8094`).
- **`systemd/hydra-umc-vision-node.service`** (new) - unit for
  `HYDRA-UMC-OS/provisioning/install_vision_node.sh` (new, that repo).
  `--workspace` points at a symlink to the real sibling-checkout root
  already on the CM5, rather than a second copy of those repos - real bug
  caught before deploying: that root lives under the operator's home
  directory, so `ProtectHome` is `read-only` here, not the family's usual
  `true` (which would make `/home/` - and this symlink's real target -
  inaccessible outright).
- 9 new tests (`tests/test_api.py`, real end-to-end HTTP, reusing this
  repo's own `tests/test_family.py` fixture shapes) - 48 total.

## [0.0.4] - Real pipeline manifest, frame validation, and degraded-mode detection
- `pipeline.py` - a real, inspectable manifest of this node's perception pipeline shape (`capture` -> `preprocess` -> `inference` -> `postprocess` -> `publish`), with each stage honestly declaring whether it needs a camera, a Hailo-8 accelerator, or neither. Documents the pipeline's shape, not the pipeline itself - no real capture loop or Hailo-8 runtime behind it (see `main.py`'s docstring for why).
- `frame.py` - real, hardware-independent structural validation of a raw frame buffer against a `FrameSpec`: flags a truncated or oversized buffer (the exact corruption pattern a torn read or a dropped fragment produces) and a suspiciously uniform buffer (a real symptom of a frozen sensor or a blank capture). Tested entirely against synthetic byte buffers - no camera needed to validate real frame integrity logic.
- `hardware.py` - real degraded-mode detection: `camera_available()`/`accelerator_available()` probe the real Linux device nodes (`/dev/video0`, `/dev/hailo0`) a real camera/accelerator would expose - honestly reporting `False` (not a crash) on any machine without them, including this development machine. `determine_mode()`/`active_stages()` are pure functions of two booleans, so every hardware combination (full, no camera, no accelerator, no hardware at all) is directly testable without touching a filesystem.
- `main.py` - two new subcommands: `pipeline-status` (prints the real manifest, real probed hardware presence, the resulting mode, and exactly which stages are runnable vs. skipped and why) and `validate-frame <path> --width --height [--channels]` (real corruption check of a frame file on disk). Bare invocation and `family-status` unchanged.
- 26 new real tests (`test_pipeline.py`, `test_frame.py`, `test_hardware.py`, plus CLI coverage) - 38 total. Verified live: `pipeline-status` on this real machine (no camera, no Hailo-8) correctly and honestly reports `degraded_no_hardware`, skipping exactly `capture` and `inference`; `validate-frame` accepts a real correctly-sized frame file and rejects a real truncated one with `size_mismatch`.

## [0.0.3] - Real v0 family-readiness check
- `manifest.py` - a real, minimal, defensive reader for a sibling repo's own `hydra-umc.project.json` (name/version/maturity/role), returning `None` for every real failure mode (missing checkout, missing file, malformed JSON, missing field) rather than raising.
- `family.py` - `check_family_status()`: a real check of this node's four real children (`HYDRA-UMC-VISION-STREAMER`/`HYDRA-UMC-DETECTION-HEF`/`HYDRA-UMC-SAFETY-ZONES`/`HYDRA-UMC-VISUAL-SERVOING-API`) against a real local workspace, reading each one's own manifest rather than a second hand-maintained list.
- `main.py` - new `family-status [--workspace PATH]` subcommand, defaulting to this repo's own parent directory (the real sibling-checkout layout this whole ecosystem already uses). Bare invocation unchanged.
- 12 new real tests (`tests/`) - manifest reading for every real failure mode, family-status coverage for all-present/some-missing/none-present, and a real end-to-end CLI round-trip.
- Real verification beyond the test suite: ran `family-status` against the actual local ecosystem checkout on this machine - correctly reported `HYDRA-UMC-SAFETY-ZONES` as `functional` and the other three real siblings as still `scaffolding`, matching their real, independently-verified state.

## [0.0.2]

Polish pass: copyright headers normalized across `main.py`, `__init__.py`,
`bump_version.py`, `build.sh`/`build.bat`, `run.sh`/`run.bat` and
`docker-compose.yml`; "why" comments added around the version-reading and
version-bump design decisions; this `CHANGELOG.md` added; README (5 languages)
substantially expanded with an Advanced Technical Information section, a
detailed Build & Run walkthrough with troubleshooting, a dateless
"Current Status & Next Steps" section replacing the previous dated
roadmap, and a full Related Projects section. No behavior change - the
bump is this verification build.

## [0.0.1]

Real build verification. `build.sh`/`build.bat` run end-to-end for real:
version bump, `.venv` creation, editable install, `python -m compileall`
clean across `src/`. `run.sh`/`run.bat` executed the entry point for real,
printing name + version + role. No business-logic change - the bump is the
recorded event.

## [0.0.0]

Initial skeleton: `pyproject.toml` (package metadata, no runtime
dependencies yet), `src/hydra_umc_vision_node/` (`__init__.py` + `main.py`
entry point reading its version from installed package metadata),
`bump_version.py` (version bump), `build.sh`/`build.bat`
(venv + editable install + compile-check) and `run.sh`/`run.bat`. Added
`docker-compose.yml`, documenting (not yet functional) how this
integration parent wires in its 4 children as containers once each ships
its own Dockerfile.
