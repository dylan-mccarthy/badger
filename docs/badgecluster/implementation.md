# Initial implementation

The badge app is in `badge/apps/badgecluster/`, on development branch
`feature/badgecluster`. The supplied baseline is preserved as `specification.md`.
Service source lives in the separate GitHub repository
[BadgeCluster](https://github.com/dylan-mccarthy/BadgeCluster), with a local
checkout at `/workspace/badgecluster-service`.
The coordinator is deployed in Australia East; no physical badges have been modified.

Implemented: two navigable screens, original pixel artwork, cooperative
250-sample/minute computation, 25-point/5 ms target slices, wrap-safe deadlines,
no catch-up bursts, foreground cleanup, and RAM-only bounded counters. The
protocol module implements immutable cumulative retries, acknowledgements,
staleness and backoff, ready for a future device transport adapter. The foreground session supports
an injected host-only live Azure transport;
the physical badge adapter remains disabled. Battery telemetry is not enabled.

Checks completed in the cloud workspace:

- Twenty Python worker/protocol/session tests pass, including tick wrap, slice limits,
  battery policy boundaries, exit, counter limits, retries and malformed acks.
- Actual app code runs with a Pillow graphics test adapter: both pages,
  250 samples, navigation preserving state, and stop-on-exit assertions pass.
- Skill app validator and repository app README validation pass.
- Host preview PNGs are in the app's `assets/` directory. The script
  `python tools/preview_badgecluster.py` regenerates them (requires Pillow).

The GitHub Copilot Badge Simulator canvas is unavailable in this session.
A real Azure HTTPS test passed through the actual app under a CPython/Pillow
adapter, including response loss and retry. No MicroPython simulator, hardware
deployment, power measurement, calibrated battery reading, device TLS
transport or physical orientation test has been performed.
Follow `device-spike.md` before integrating real HTTPS and broad rollout.
