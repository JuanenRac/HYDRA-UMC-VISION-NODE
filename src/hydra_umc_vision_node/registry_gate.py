# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/registry_gate.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Choosing the model from the registry of DETECTION-HEF and refusing the full mode while it is not ready.

When the gate is on, the node asks the registry (`GET /registry/load?name=...&target_arch=...`) for the model it is configured to use, with the
architecture of the device written in the canonical form of the registry (the Hailo runtime reports `HAILO8`, the registry says `hailo8`). The
node reaches the full mode only when the registry answers `isReady: true`: a model that is missing, whose file does not match its checksum, that was
compiled for another chip, or a registry that does not answer, all keep the node out of it. The answer is remembered, with the identity of the model
(name, version, SHA-256, architecture) that DETECTION-HEF reports, so that the node can say which model it is going to run.
"""
from __future__ import annotations

import http.client
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

from .models import NAME
from .netguard import EndpointError, parse_endpoint

KNOWN_ARCHS = frozenset({"hailo8", "hailo8r", "hailo8l", "hailo15h", "hailo15m", "hailo15l", "hailo10h"})
MAX_ANSWER_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 3.0
MAX_TIMEOUT_SECONDS = 60.0
_CONFIG_KEYS = {"enabled", "url", "allowRemote", "modelName", "targetArch", "task", "requireReady", "timeoutSeconds"}


class GateError(ValueError):
    """The configuration of the gate cannot be used."""


def normalize_arch(text: object) -> str:
    """`HAILO8`, ` Hailo-8 ` and `hailo8` are the same chip: the canonical form is lower case without separators."""
    if not isinstance(text, str):
        raise GateError("targetArch must be a text")
    canonical = text.strip().lower().replace("-", "").replace("_", "")
    if canonical not in KNOWN_ARCHS:
        raise GateError(f"targetArch {text!r} is not a known Hailo architecture ({', '.join(sorted(KNOWN_ARCHS))})")
    return canonical


@dataclass(frozen=True)
class RegistryConfig:
    enabled: bool
    host: str
    port: int
    path: str
    model_name: str
    target_arch: str
    task: str | None
    require_ready: bool
    timeout_seconds: float

    def describe(self) -> dict:
        return {"enabled": self.enabled, "url": f"http://{self.host}:{self.port}{self.path}", "modelName": self.model_name,
                "targetArch": self.target_arch, "task": self.task, "requireReady": self.require_ready, "timeoutSeconds": self.timeout_seconds}


def parse_registry_config(config: object) -> RegistryConfig | None:
    """The optional `registry` block of the configuration. None when it is absent. `enabled` is false unless the block says true."""
    if config is None:
        return None
    if not isinstance(config, dict) or set(config) - _CONFIG_KEYS:
        raise GateError(f"registry is an object with the keys {sorted(_CONFIG_KEYS)}")
    enabled = config.get("enabled", False)
    require_ready = config.get("requireReady", True)
    allow_remote = config.get("allowRemote", False)
    for key, value in (("enabled", enabled), ("requireReady", require_ready), ("allowRemote", allow_remote)):
        if not isinstance(value, bool):
            raise GateError(f"registry.{key} must be true or false")
    try:
        endpoint = parse_endpoint(config.get("url"), allow_remote, "registry.url")
    except EndpointError as error:
        raise GateError(str(error)) from None
    name = config.get("modelName")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise GateError("registry.modelName is 1 to 64 characters of letters, digits and _ . : -")
    task = config.get("task")
    if task is not None and (not isinstance(task, str) or not NAME.fullmatch(task)):
        raise GateError("registry.task is 1 to 64 characters of letters, digits and _ . : -")
    timeout = config.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise GateError(f"registry.timeoutSeconds must be a number above 0 and at most {MAX_TIMEOUT_SECONDS:g}")
    return RegistryConfig(enabled, endpoint.host, endpoint.port, endpoint.path.rstrip("/"), name, normalize_arch(config.get("targetArch")),
                          task, require_ready, float(timeout))


@dataclass(frozen=True)
class GateResult:
    """What the registry said the last time it was asked. `ready` is true only for an explicit `isReady: true`."""

    state: str  # "ready", "not_ready", "unreachable", "invalid_answer"
    ready: bool
    outcome: str | None = None
    detail: str = ""
    model: dict | None = None  # name, version, sha256, hailo_arch of the entry the registry resolved

    def describe(self) -> dict:
        return {"state": self.state, "ready": self.ready, "outcome": self.outcome, "detail": self.detail, "model": self.model}


Fetch = Callable[[RegistryConfig, str], tuple[int, bytes]]


def http_fetch(config: RegistryConfig, target: str) -> tuple[int, bytes]:
    """One GET of `target` on the registry service. Redirects are not followed and the answer is read up to MAX_ANSWER_BYTES."""
    connection = http.client.HTTPConnection(config.host, config.port, timeout=config.timeout_seconds)
    try:
        connection.request("GET", target, headers={"Accept": "application/json"})
        response = connection.getresponse()
        return response.status, response.read(MAX_ANSWER_BYTES + 1)
    finally:
        connection.close()


def _model_of(entry: object) -> dict | None:
    if not isinstance(entry, dict):
        return None
    keys = ("name", "version", "sha256", "hailo_arch")
    if not all(isinstance(entry.get(k), str) for k in keys):
        return None
    return {k: entry[k] for k in keys}


def resolve_model(config: RegistryConfig, fetch: Fetch = http_fetch) -> GateResult:
    """Ask the registry whether the configured model can be loaded on the configured architecture. Never raises: any failure is a not-ready answer."""
    query = {"name": config.model_name, "target_arch": config.target_arch}
    if config.task:
        query["task"] = config.task
    target = f"{config.path}/registry/load?{urlencode(query)}"
    try:
        status, body = fetch(config, target)
    except (OSError, http.client.HTTPException) as error:
        return GateResult("unreachable", False, detail=type(error).__name__)
    if len(body) > MAX_ANSWER_BYTES:
        return GateResult("invalid_answer", False, detail="the answer of the registry is too large")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return GateResult("invalid_answer", False, detail=f"HTTP {status}: the answer is not JSON")
    if not isinstance(data, dict):
        return GateResult("invalid_answer", False, detail=f"HTTP {status}: the answer is not an object")
    if status != 200:
        message = data.get("error")
        return GateResult("not_ready", False, detail=f"HTTP {status}: {str(message)[:200] if message else 'the registry refused the request'}")
    ready = data.get("isReady")
    if not isinstance(ready, bool):
        return GateResult("invalid_answer", False, detail="the answer has no isReady")
    outcome = data.get("outcome") if isinstance(data.get("outcome"), str) else None
    detail = str(data.get("detail", ""))[:300]
    return GateResult("ready" if ready else "not_ready", ready, outcome, detail, _model_of(data.get("entry")))


def mode_allowed(config: RegistryConfig | None, result: GateResult | None) -> bool | None:
    """What to hand `determine_mode` as `model_ready`: None when the gate is off or does not require readiness (no extra condition)."""
    if config is None or not config.enabled or not config.require_ready:
        return None
    return bool(result is not None and result.ready)
