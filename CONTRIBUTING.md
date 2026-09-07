# Contributing to HYDRA-UMC-VISION-NODE 🦾

We welcome contributions to the high-speed perception engine of the HYDRA-UMC platform.

## Technology Stack
- **Language**: Python 3.12, C++20.
- **Hardware**: Raspberry Pi CM5 (BCM2712), Hailo-8 M.2 AI Accelerator.
- **Frameworks**: HailoRT, GStreamer, gRPC, Protobuf.
- **Environment**: Linux (Ubuntu 22.04 / Raspberry Pi OS).

## Guidelines
1. **Model Optimization**: Ensure all HEF models are quantized and optimized for Hailo-8.
2. **Zero-Copy Performance**: Maintain zero-copy memory management in GStreamer pipelines.
3. **Safety Integrity**: Any changes to safety zones must be validated with the E-STOP interlock.
4. **Testing**: Validate inference latency benchmarks before submitting PRs.
