# =============================================================================
# HYDRA-UMC-VISION-NODE - Container Build: Dockerfile
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
# Real, minimal image for the family-readiness HTTP API (api.py's own
# server, stdlib http.server - pyproject.toml's own dependencies is
# deliberately empty for v0). Same --addr/--port CLI the real CM5 systemd
# unit (systemd/hydra-umc-vision-node.service) already runs, just bound
# to 0.0.0.0 instead of 127.0.0.1 here - a container's own network
# namespace already isolates it the way the systemd unit's loopback bind
# does on bare metal. Non-root, matching that same unit's own
# User=hydra-umc-vision-node. This is this repo's OWN Dockerfile - its 4
# children (vision-streamer/detection-hef/safety-zones/visual-servoing-api)
# each already ship their own; this one was docker-compose.yml's own
# explicitly-documented remaining gap for the `vision-node` service
# (`build: .`) to actually build.
#
# The real gRPC control API this compose file also exposes (port 50051)
# does not exist yet (see this repo's own README/main.py - v0 is the
# family-readiness HTTP API only) - not wired into CMD here for that
# reason; nothing to start yet beyond `serve`.

FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --system --create-home --home-dir /home/hydra hydra
USER hydra

EXPOSE 8094
ENTRYPOINT ["hydra-umc-vision-node"]
CMD ["serve", "--addr", "0.0.0.0", "--port", "8094"]
