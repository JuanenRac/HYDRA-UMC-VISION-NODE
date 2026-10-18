# Contributing to HYDRA-UMC-VISION-NODE 🦾

We welcome contributions to the high-speed perception engine of the HYDRA-UMC platform.

## Technology Stack
- **Language**: Python 3.10 or newer, standard library only (no runtime dependencies).
- **Tests**: `pytest tests/`, plus `python tools/ci_validate.py` before every push.
- **Hardware** (planned, not driven by this repository yet): Raspberry Pi CM5 and a Hailo-8 M.2 accelerator; HailoRT, GStreamer and gRPC belong to the child projects and to future work.
- **Environment**: Linux (Raspberry Pi OS) or Windows for development.

## Guidelines
1. **Model Optimization**: Ensure all HEF models are quantized and optimized for Hailo-8.
2. **Zero-Copy Performance**: Maintain zero-copy memory management in GStreamer pipelines.
3. **Safety Integrity**: Any changes to safety zones must be validated with the E-STOP interlock.
4. **Testing**: Validate inference latency benchmarks before submitting PRs.
