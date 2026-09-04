# Changelog

All notable changes to HYDRA-UMC-VISION-NODE are documented in this file.

Versioning follows the ecosystem-wide `MAJOR.MINOR.PATCH` "odometer" scheme,
applied automatically on every real build by `bump_version.py` (invoked
from build.sh/build.bat right before the compile-check): `PATCH` goes up by
1 per build; once `PATCH` would exceed 9 it resets to 0 and `MINOR` goes up
by 1 instead (e.g. `0.0.9` -> `0.1.0`), the same carry cascading into
`MAJOR` if `MINOR` also exceeds 9. `MAJOR` is otherwise only ever bumped by
hand.

## Unreleased - strict sibling-manifest fields

- The family manifest reader now accepts only non-empty text values for
  `name`, `version`, `maturity` and `role`. Invalid JSON types no longer get
  silently coerced into strings and represented as a real child contract.

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
### Added
- `pipeline.py` - a real, inspectable manifest of this node's perception pipeline shape (`capture` -> `preprocess` -> `inference` -> `postprocess` -> `publish`), with each stage honestly declaring whether it needs a camera, a Hailo-8 accelerator, or neither. Documents the pipeline's shape, not the pipeline itself - no real capture loop or Hailo-8 runtime behind it (see `main.py`'s docstring for why).
- `frame.py` - real, hardware-independent structural validation of a raw frame buffer against a `FrameSpec`: flags a truncated or oversized buffer (the exact corruption pattern a torn read or a dropped fragment produces) and a suspiciously uniform buffer (a real symptom of a frozen sensor or a blank capture). Tested entirely against synthetic byte buffers - no camera needed to validate real frame integrity logic.
- `hardware.py` - real degraded-mode detection: `camera_available()`/`accelerator_available()` probe the real Linux device nodes (`/dev/video0`, `/dev/hailo0`) a real camera/accelerator would expose - honestly reporting `False` (not a crash) on any machine without them, including this development machine. `determine_mode()`/`active_stages()` are pure functions of two booleans, so every hardware combination (full, no camera, no accelerator, no hardware at all) is directly testable without touching a filesystem.
- `main.py` - two new subcommands: `pipeline-status` (prints the real manifest, real probed hardware presence, the resulting mode, and exactly which stages are runnable vs. skipped and why) and `validate-frame <path> --width --height [--channels]` (real corruption check of a frame file on disk). Bare invocation and `family-status` unchanged.
- 26 new real tests (`test_pipeline.py`, `test_frame.py`, `test_hardware.py`, plus CLI coverage) - 38 total. Verified live: `pipeline-status` on this real machine (no camera, no Hailo-8) correctly and honestly reports `degraded_no_hardware`, skipping exactly `capture` and `inference`; `validate-frame` accepts a real correctly-sized frame file and rejects a real truncated one with `size_mismatch`.

## [0.0.3] - Real v0 family-readiness check
### Added
- `manifest.py` - a real, minimal, defensive reader for a sibling repo's own `hydra-umc.project.json` (name/version/maturity/role), returning `None` for every real failure mode (missing checkout, missing file, malformed JSON, missing field) rather than raising.
- `family.py` - `check_family_status()`: a real check of this node's four real children (`HYDRA-UMC-VISION-STREAMER`/`HYDRA-UMC-DETECTION-HEF`/`HYDRA-UMC-SAFETY-ZONES`/`HYDRA-UMC-VISUAL-SERVOING-API`) against a real local workspace, reading each one's own manifest rather than a second hand-maintained list.
- `main.py` - new `family-status [--workspace PATH]` subcommand, defaulting to this repo's own parent directory (the real sibling-checkout layout this whole ecosystem already uses). Bare invocation unchanged.
- 12 new real tests (`tests/`) - manifest reading for every real failure mode, family-status coverage for all-present/some-missing/none-present, and a real end-to-end CLI round-trip.
- Real verification beyond the test suite: ran `family-status` against the actual local ecosystem checkout on this machine - correctly reported `HYDRA-UMC-SAFETY-ZONES` as `functional` and the other three real siblings as still `scaffolding`, matching their real, independently-verified state.

## [0.0.2]

Polish pass: copyright headers normalized across `main.py`, `__init__.py`,
`bump_version.py`, `build.sh`/`build.bat`, `run.sh`/`run.bat` and
`docker-compose.yml`; "why" comments added around the version-reading and
odometer design decisions; this `CHANGELOG.md` added; README (5 languages)
substantially expanded with an Advanced Technical Information section, a
detailed Build & Run walkthrough with troubleshooting, a dateless
"Current Status & Next Steps" section replacing the previous dated
roadmap, and a full Related Projects section. No behavior change - the
bump is this verification build.

## [0.0.1]

Real build verification. `build.sh`/`build.bat` run end-to-end for real:
odometer bump, `.venv` creation, editable install, `python -m compileall`
clean across `src/`. `run.sh`/`run.bat` executed the entry point for real,
printing name + version + role. No business-logic change - the bump is the
recorded event.

## [0.0.0]

Initial skeleton: `pyproject.toml` (package metadata, no runtime
dependencies yet), `src/hydra_umc_vision_node/` (`__init__.py` + `main.py`
entry point reading its version from installed package metadata),
`bump_version.py` (odometer-style version bump), `build.sh`/`build.bat`
(venv + editable install + compile-check) and `run.sh`/`run.bat`. Added
`docker-compose.yml`, documenting (not yet functional) how this
integration parent wires in its 4 children as containers once each ships
its own Dockerfile.
