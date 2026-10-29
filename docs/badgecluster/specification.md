# BadgeCluster — Technical Specification v0.1

**Date:** 2026-10-28  
**Status:** MVP implementation baseline  
**Tagline:** *Hundreds of processors. One irrational goal.*

## 1. Purpose

BadgeCluster is an opt-in distributed-computing application for GitHub Universe 2026 badges. Each badge independently estimates π using a small Monte Carlo workload and periodically submits its contributions to an Azure-hosted coordinator. Participants see a Mona-themed "lend your power" screen and a shared-statistics screen. The badge remains usable and battery longevity takes priority over processing throughput.

## 2. Goals and non-goals

### Goals
- Run computation only while BadgeCluster is the foreground/active application.
- Use low-duty-cycle, bounded CPU work and infrequent network communication.
- Present two badge screens: Mona powering a shared energy sphere; local/global contributions and π estimate.
- Support many concurrent volunteer badges without a central scheduler.
- Count network retries at most once; handle Wi-Fi failures gracefully.
- Provide a lightweight public web dashboard showing cluster activity.
- Make the workload understandable, inspectable, and suitable for community contribution.

### Non-goals for v0.1
- Background work after closing the app.
- Executing server-supplied code, generic remote workloads, or firmware changes.
- Complex job leasing, task scheduling, or queues.
- Precise battery-life guarantees without device measurements.
- Identity verification, prizes, or competitive anti-cheating guarantees.
- Continuous full-screen animation, per-point network uploads, or production-grade distributed consensus.

## 3. Target and compatibility

- Hardware: GitHub Universe 2026 badge, RP2350B, 320x240 display, wireless module and rechargeable battery.
- Badge firmware: 2026 MicroPython/Badgeware variant; use current `badger/home` examples, not 2025 API assumptions.
- App layout: `badge/apps/badgecluster/__init__.py`, `icon.png` (24x24), and `assets/`.
- Drawing/input: `run(update)`, `badge.pressed(BUTTON_LEFT/RIGHT/BACK)`, `screen`, `image.load`, with default 160x120 logical mode initially. Validate orientation and navigation on hardware.
- Networking/TLS and battery interfaces MUST be checked on the actual 2026 firmware before final implementation. Do not assume the upstream Tufty APIs are identical.

Reference: https://github.com/badger/home

## 4. Architecture

```text
Badge 1..N (MicroPython)
  ├─ UI state: Mona / Statistics
  ├─ cooperative compute scheduler
  ├─ local counters + session ID + sequence
  └─ bounded HTTPS sync client
           │ POST /api/v1/contributions (every ~60 s)
           ▼
Azure Container Apps: ASP.NET Core Minimal API
  ├─ input validation and idempotent per-session counters
  ├─ global aggregation, activity tracking, π estimate
  ├─ read-only cluster status endpoint
  └─ web dashboard (periodic refresh)
           │
           ▼
Persistent store (recommended: Azure Table Storage or SQLite on durable backing)
```

The badge calculates autonomously; the coordinator never assigns individual points. A single contribution request also returns the updated cluster statistics. In-memory state is acceptable only for a throwaway single-instance demo; persistent state is recommended before inviting multiple users because container restarts and replicas can break totals.

## 5. Monte Carlo workload

For each sample generate two independent uniform values `x,y` in [0,1). Count inside if `x*x + y*y <= 1`.

- Local result: `samples`, `inside` where `0 <= inside <= samples`.
- Global estimate: `pi = 4 * globalInside / globalSamples` for `globalSamples > 0`; otherwise `null`.
- No individual points are transmitted.
- Use a firmware-supported pseudorandom generator; validate its seed behavior and use independent per-session seeds or streams. Quality of randomness affects convergence.
- Display estimated π as an approximation, not a claim of exact decimal accuracy.

### Compute defaults (experimental, to be tuned)

| Parameter | Initial value | Rationale |
|---|---:|---|
| Samples per scheduled batch | 250 | Small workload |
| Target compute interval | 60 seconds | Low duty cycle |
| Maximum samples per update slice | 25 | Avoid long frame stalls |
| Target time budget per update slice | 5 ms | Preserve UI responsiveness; measured, not guaranteed |
| Sync interval | 60 seconds | Upload each completed batch; failure backoff still applies |
| Timeout for network request | 5 seconds | Avoid long UI stalls; may require transport-specific adaptation |
| Network retry backoff | 30, 60, 120, 300 seconds max | Avoid rapid retries |
| LED effects | Off by default | Conserve battery |

Implementation must NOT block the badge UI for a full work batch or for an unbounded network request. If the available MicroPython HTTP client is synchronous, investigate nonblocking sockets or isolate brief network operations with explicit timeouts; don't falsely assume the synchronous HTTP call is frame-safe.

Schedule work with monotonic ticks and wrap-safe comparisons. Missed intervals do not accumulate: no catch-up burst after the device resumes or Wi-Fi reconnects. Compute cadence is independent of which page is visible.

## 6. Lifecycle and battery rules

States: `STARTING`, `ACTIVE`, `SYNCING`, `OFFLINE`, `PAUSED_LOW_BATTERY`, `STOPPING`.

- Launch: load assets, initialise counters/session, check connectivity asynchronously where possible, show UI immediately.
- Active: compute periodic bounded samples, locally accumulate counts, sync when due.
- Offline: remain responsive, show last received cluster values as stale, retain bounded unsent cumulative counters, back off retries.
- App exit/home: stop scheduling computations and communications immediately; release resources if supported; never leave a background worker.
- Navigation between Mona and Stats must not alter worker state or reset counters.
- When battery telemetry is available and reliable: >60% normal Eco; 30–60% reduce frequency (e.g. 120 s); 20–30% pause with a clear message; <20% no computation or networking. When on USB power, do not automatically raise compute duty cycle in MVP.
- If battery percentage is unavailable, fall back to conservative Eco mode and clearly omit the unsupported indicator; do not guess.
- Avoid frequent flash writes: persist session counters at controlled milestones or on exit if reliable; keep RAM-backed counters as the primary runtime path.

These battery limits are provisional product decisions, not verified board safety specifications. Establish actual firmware APIs, calibration and measurement first.

## 7. Badge UI

### Screen A — `MONA_POWER`
- Title: `BADGECLUSTER`.
- Mona illustration with arms raised, contributing to a shared energy sphere (original/custom artwork or appropriately licensed asset).
- Subtitle: `LEND YOUR POWER!`.
- Status: `CONTRIBUTING`, `CONNECTING`, `OFFLINE`, or `PAUSED`.
- Navigation affordance: `RIGHT: STATS`.
- Sphere uses 3–5 pre-rendered sizes based on global sample milestones, e.g. 0 / 100k / 1M / 10M / 100M (configurable). Occasional short pulse on completed batch. Event-driven redraws preferred over constant animation.

### Screen B — `STATS`
- Current global estimated π (show `—` until samples exist).
- This badge's accepted sample contribution (server-confirmed), plus pending local samples if useful.
- Total cluster samples, active badge count, percentage of total where meaningful.
- Connectivity/staleness indicator and optional power status.
- Navigation affordance: `LEFT: MONA`.

Both screens should support 160x120 logical resolution with legible text and graceful truncation. Use orientation-aware logical input constants. Neither screen should show fictitious precise π digits or claim contribution has been accepted until acknowledged by the server.

## 8. Coordinator HTTP API v1

Base: `/api/v1`; JSON over HTTPS. Enforce request size limits, sensible rate limits and response timeouts.

### `POST /api/v1/contributions`

An upsert of cumulative counters for one locally generated session. Server stores the highest accepted `sequence` and its counters for each `(sessionId, nodeId)`. Newer sequences must have monotonically nondecreasing counters. Duplicate or older requests do not alter totals. This supports network retries and response loss.

Request example:

```json
{
  "protocolVersion": 1,
  "nodeId": "node-7f4c2a",
  "sessionId": "session-c9e132",
  "sequence": 4,
  "samples": 1000,
  "inside": 783
}
```

Response example:

```json
{
  "acceptedSequence": 4,
  "acceptedSamples": 1000,
  "cluster": {
    "samples": 1840000,
    "inside": 1445126,
    "piEstimate": 3.141578260869565,
    "activeNodes": 18,
    "updatedAt": "2026-10-28T16:00:00Z"
  }
}
```

Important: example counters are illustrative. Server validates `0 <= inside <= samples`, sizes and counter monotonicity. Each new session gets a collision-resistant ID generated from available device randomness; do not send MAC addresses or other identifying hardware details. If session recovery is supported, preserve its ID and counters together. Badges may lose unsynced samples on abrupt shutdown; accepted contributions remain intact.

### `GET /api/v1/cluster`
Read-only snapshot for the public web dashboard. Return global samples, inside count, estimate, active count, updatedAt, optionally aggregate rate. Badge does not need this if the contribution response supplies the snapshot.

### `GET /health`
Service liveness/readiness, not a public administrative endpoint.

**Active node definition:** a distinct node ID with an accepted update in the past 5 minutes. Nodes whose app closes will naturally expire; a voluntary final inactivity signal is optional and not required to close the app.

**Concurrency:** updates and global totals must be atomic/consistent. If using Azure Table Storage, use optimistic concurrency/ETags for per-session updates, and derive or transactionally maintain totals (avoid blindly incrementing a shared counter). For a small first release, a single server process with persistent SQLite and transactions may be simpler, but Azure Container Apps storage suitability must be assessed before deployment. Do not scale out an in-memory aggregate.

## 9. Web dashboard

A public, mobile-friendly read-only dashboard is hosted by the API or alongside it. It shows:
- Name/tagline and an illustrated cluster energy sphere.
- Global π estimate prominently (with an `approx.` label).
- Total accepted samples and active badges.
- Optional recent sample throughput and last update timestamp.
- Optional top contributors ONLY if participants intentionally opt in to display names; otherwise use anonymous node IDs or omit rankings.

Refresh at 10–30 second intervals; do not require WebSockets for MVP. Dart graphics on the web are illustrative/simulated—not actual telemetry from badges.

## 10. Privacy, abuse and reliability

- No attendee account, device serial, MAC, location or contact details required.
- Use generated pseudonymous node IDs and per-session IDs. Consider their lifecycle and retention.
- Reject invalid counters, huge increments, negative values, and excessive request rates; configure bounded quotas.
- Since clients are untrusted, server cannot verify that all samples were honestly calculated. Avoid claims of verified computing power or prize-worthy ranking.
- Do not transmit Wi-Fi credentials; store credentials using the badge's existing configuration pattern, not in source control.
- Log error categories and timings, but avoid storing payloads or identifying device metadata unnecessarily.
- If server is unavailable: no crash, no rapid reconnect loops, no misleading global state.

## 11. Suggested repository structure

```text
badgecluster/
├── README.md
├── docs/
│   └── specification.md
├── badge/
│   └── apps/
│       └── badgecluster/
│           ├── __init__.py
│           ├── icon.png
│           └── assets/
│               ├── mona.png
│               └── sphere_*.png
├── src/
│   └── BadgeCluster.Api/
│       ├── Program.cs
│       ├── Contracts/
│       ├── Services/
│       ├── Persistence/
│       └── wwwroot/
├── tests/
│   ├── BadgeCluster.Api.Tests/
│   └── badge_logic/
└── infra/
    └── main.bicep
```

## 12. Implementation order

### Milestone 0 — Device capability spike
- Verify MicroPython networking stack, HTTPS/TLS, timeouts, battery readings and app lifecycle on physical 2026 badge.
- Prove `run(update)` can perform 25-point slices without visual stutter.
- Measure baseline consumption in inactive and connected states.

### Milestone 1 — Local badge prototype
- Two navigable screens, Mona sprite and placeholder stats.
- Cooperative Monte Carlo scheduler, 250 samples/minute.
- Offline behaviour, no Azure dependency yet.

### Milestone 2 — Coordinator and integration
- Implement cumulative contribution endpoint and atomic idempotency.
- Add persistence, validation, health endpoint and automated API tests.
- Connect one badge; verify repeated requests never double-count.

### Milestone 3 — Web dashboard and multi-badge test
- Build dashboard and aggregate status endpoint.
- Connect 2–5 badges or simulated nodes; verify convergence and active-node expiry.
- Test Wi-Fi loss/recovery and repeat submissions.

### Milestone 4 — Battery/UX polish
- Compare idle, connected-idle and Eco compute battery impact.
- Tune compute/network cadence; optimise asset sizes and render frequency.
- Verify application exit stops work and never leaves a background process.

## 13. Acceptance criteria

1. BadgeCluster starts from the 2026 badge menu and renders both screens without errors.
2. LEFT/RIGHT switches screens while computation counters continue uninterrupted.
3. Each active badge executes bounded calculations, targeting 250 samples per minute by default.
4. No calculation is scheduled when BadgeCluster has exited.
5. UI remains usable during compute and network failures; operations are bounded or nonblocking.
6. Contribution requests use monotonically increasing cumulative counters and sequence numbers.
7. Retried or out-of-order contributions do not increase global counts twice.
8. API aggregate π calculation matches `4 * inside / samples`; zero samples is handled safely.
9. Offline state displays cached statistics as stale and uses capped retry backoff.
10. Dashboard displays current aggregate samples, approximation of π, and active node count.
11. Persistent store survives coordinator restart without losing accepted contributions.
12. One physical badge passes a battery comparison against idle baseline before broad rollout.
13. No hardware identifiers or attendee personal information are required.
14. Badge screen doesn't misrepresent unacknowledged local work as server-accepted work.

## 14. Decisions to validate during implementation

- Network/TLS support and whether synchronous calls stall the renderer.
- Battery telemetry API fidelity and threshold behaviour.
- Precise app exit/cleanup semantics in the 2026 badge firmware.
- Practical rendering/asset sizes in 160x120 logical mode.
- Random number generator seeding behaviour.
- Durable Azure storage choice and real deployment cost.

## 15. References

- 2026 badge repository: https://github.com/badger/home
- 2026 hardware reference: https://github.com/badger/home/blob/main/hardware/README.md
- App structure, input, graphics, state, and installation: https://github.com/badger/home#creating-a-2026-app
