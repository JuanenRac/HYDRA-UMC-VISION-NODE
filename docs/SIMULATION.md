# Simulation bench

The node can run without a camera and without a Hailo accelerator. The simulated path replaces both: a deterministic synthetic frame source, a registry of
declared models, a fake model whose answer depends only on the frame bytes and the model name, and a virtual clock for the stage times. It proves that the
plumbing (sources, model choice, latency record, loopback detector, frame checks, degraded-mode report) works; it says nothing about real inference speed,
accuracy or any real camera.

## Start it

```
hydra-umc-vision-node serve --vision-sim docs/examples/vision-sim.example.json --latency-window 256
```

The switch is off by default. With it, the API adds `GET /models`, `GET /sources`, `POST /models/select` and `POST /inference/simulate`; without it they answer 404.
The example file declares a host with a CPU only, a model that needs the accelerator (discarded, with the reason) and a CPU model, and one synthetic source of 20 frames.
A second block, `loopback`, turns on `POST /loopback/run` (a trivial CPU detector on simulated frames); see the README for its fields.

## What the checks run

The test files below run on every change in the normal checks and again in the workflow named "Simulation bench", so the simulated path has its own visible job:

| File | What it exercises |
|------|-------------------|
| `tests/test_vision_sim.py` | the configuration, the model choice with the reason for every discard, the simulated run over HTTP |
| `tests/test_simulation_limits.py` | the time limit of a run, the truncated result, a second run refused while one is running |
| `tests/test_sources.py` | the synthetic generator and the folder of frames, with corrupt and oversize input |
| `tests/test_loopback.py` | the loopback pipeline with a fake streamer and decoder |
| `tests/test_pipeline.py`, `tests/test_hardware.py` | the pipeline manifest and the degraded-mode report when no device is present |
| `tests/test_latency.py`, `tests/test_live.py` | the percentiles per stage and the live family check |

The workflow also loads the example file with the same parser the server uses, so the document cannot drift from the code.

## What it cannot show

A real camera or RTSP source, the Hailo runtime, real model files, real latency and the thermal behaviour of the CM5 still need the hardware. The status of
the repository therefore stays at "simulated" for the hardware row until a person has run the node on the bench.
