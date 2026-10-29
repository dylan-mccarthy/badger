# BadgeCluster device capability gate

A physical badge is attached locally. Follow `hardware/USB_SERIAL.md` for serial
and USB Disk Mode operations. Do not invite participants until the remaining
live-network and battery gates below are measured.

## Local device observations (2026-10-28)

- Firmware: GitHub Badger 2350, MicroPython 1.29.0-preview, build 2026-10-13.
- `os.urandom(16)` returns 16 bytes; the original app ran 120 native frames
  and the user confirmed it works on the device.
- Firmware `fetch.AsyncFetch` uses nonblocking sockets but blocking
  `getaddrinfo` and `CERT_NONE`; the app replaces those connection steps.
- The device accepts the bundled DER CA through the positional
  `SSLContext.load_verify_locations(der)` API with `CERT_REQUIRED`.
- RTC initially reported May 2025, so the adapter performs cooperative NTP
  before HTTPS. NTP is standard unauthenticated network time.
- Updated USB Wi-Fi fields were synchronized into runtime `/secrets.py`;
  other settings were preserved. The configured open access point was not
  visible reliably, and association reported `-2` (AP not found). The user
  requested deploying the networking build and deferring live connectivity.
- Azure `/health` returned HTTP 200 from the host with the bundled CA.
  This does not prove a badge HTTPS request or acknowledgement.
- The networking build was deployed with an on-device backup of the prior app;
  all 15 installed files matched their source SHA-256 hashes.
- A bounded native launch exercised 314 frames over 7.5 seconds, both pages,
  250 genuinely computed samples, a five-second unavailable-network timeout,
  and exit cleanup with no open UDP/HTTP resources. The test accelerated only
  the initial compute/sync schedule, not sampling or networking.
- Maximum measured update duration was 406 ms during initial Wi-Fi radio
  startup. This is a known launch/association hitch, not evidence that every
  network step meets the compute worker's 5 ms slice target. Live DNS/NTP/TLS
  and failure-case timing remain unmeasured.
- The updated simulator ran both pages without tracebacks; it remains offline
  and cannot validate the device transport.

## Live device sync (2026-10-28, after Wi-Fi update)

- The replacement access point was visible and association succeeded (status 3).
  Only the Wi-Fi fields were synchronized from the USB config into runtime
  `/secrets.py`; credentials were not printed or copied into source control.
- The installed app completed NTP, UDP DNS, CA/hostname-verified TLS and a real
  Azure contribution. Azure acknowledged all 250 genuinely computed samples;
  the returned cluster total was 500 samples with one active node.
- The bounded test accelerated only the initial compute/sync schedule. It
  rendered 182 frames before acknowledgement, reported no session error, and
  confirmed exit cleanup. Maximum update time was 692 ms; TLS/radio work does
  not satisfy a strict 5 ms per-frame networking budget on this firmware.
- This proves one successful live request, not the remaining loss/retry,
  certificate-rejection, prolonged outage, power or orientation checks.

## Normal cold-start correction (2026-10-28)

- An unaccelerated launch reproduced the reported OFFLINE display: no Wi-Fi
  work occurred until the 120-second sync interval, and cold association used
  most of the five-second request deadline, resulting in a timeout.
- Wi-Fi is now prepared during the initial compute interval. Status distinguishes
  JOINING WIFI and WAITING SYNC from a real failed request. The first sync is due
  with the first 60-second batch, with later syncs still two minutes apart.
- After deployment, a real cold launch with no schedule overrides connected
  Wi-Fi at 3.495 seconds, started its first sync at 60.069 seconds and reached
  CONTRIBUTING at 62.150 seconds. Azure acknowledged all 250 computed samples,
  with 2,699 frames rendered and zero failures or retries.

## Persistent node identity (2026-10-28)

- Earlier launches generated distinct node IDs, causing one physical badge to
  appear as multiple recently active nodes. The existing Azure store counts
  node records separately from per-launch sessions.
- The app now saves a random node ID once in
  `/state/badgecluster_identity.json`, independently of hardware identifiers.
  Session IDs and RAM contribution counters still restart on every launch.
- Deployed all 16 app files and verified their SHA-256 hashes. Two separate
  real app launches each received Azure acknowledgement for 250 computed samples,
  using the same saved node ID and different session IDs. Only the initial
  scheduling delay was accelerated in this check.
- The identity file's SHA-256 matched before and after a hard reset. The badge
  was reset to the normal menu afterward. No identity file is stored in source
  control or included in app deployment copies.
- Unit tests cover one-time creation, no-write reload, independent devices,
  interrupted rename recovery, invalid files, permission errors and short
  entropy. Corrupt identity does not silently rotate to another node.

## Wi-Fi failure recovery (2026-10-28, later reliability investigation)

- USB inspection was user-approved; installation and restart were separately
  approved. Saved Wi-Fi settings and node identity were not changed.
- The configured access point returned driver status `-2` (AP not found).
  Firmware `wifi.connect(timeout=4, retries=0)` exhausted association, disabled
  its radio, and retained a terminal failure. Repeated calls over 45 seconds
  did not restart joining. `wifi.disconnect()` also retained the failure latch;
  passing a positive retry count did not recover it.
- The app now owns association through `network.WLAN`, reads the same saved
  credentials, bounds each join to 15 seconds, and clears failed driver state.
  Session failures trigger existing 30/60/120/300-second backoff rather than
  remaining indefinitely on JOINING WIFI.
- Deployed code was exercised without timing overrides for 100 seconds.
  Three actual association attempts began at 0.403, 31.465 and 92.523 seconds;
  each failed, and the app remained responsive for 4,362 frames, computed 250
  local samples, and cleaned up its request resources on exit. No contribution
  was accepted or submitted during these failed associations.
- This verifies automatic retry, not restored connectivity. The saved access
  point remains unavailable to the badge and must be made reachable before
  another end-to-end acknowledgement can be verified.
- Fifty-six targeted tests cover direct-driver association, existing-connection
  reuse, failure/reset/retry, 15-second deadline including tick wrap, unchanged
  credentials, cancellation and contribution protocol regressions.

## One-minute upload cadence verification

After the user reported restored connectivity and selected one upload per
completed batch, the updated app was installed through USB Disk Mode. All
16 installed files matched their source SHA-256 hashes. The 24x24 icon and
existing custom-icon menu support were retained; this was not a new visual
launcher-icon check.

The native installed app ran for 125.062 seconds without clock or scheduling
overrides. An in-RAM runner wrapper only observed state and bounded the run:

- Wi-Fi was ready at 3.723 seconds.
- Requests started at 60.472 and 120.476 seconds: 60.004 seconds apart.
- Azure acknowledged cumulative totals of 250 samples at 64.857 seconds and
  500 samples at 125.062 seconds, with consecutive sequences 1 and 2.
- The app rendered 5,436 frames with zero failures. Session cleanup stopped
  computation and cleared its pending snapshot.
- Saved identity and runtime Wi-Fi file hashes were unchanged. The badge
  was reset to the normal menu afterward.

Healthy uploads now align to the next batch rather than waiting a full minute
after acknowledgement. The compute rate remains 250 samples per minute and
failure backoff remains 30/60/120/300 seconds. Sixty targeted host tests and
both static/README validators passed. A restarted simulator runtime loaded
the 60,000 ms sync interval and rendered the Stats page without a traceback;
its networking remains disabled.

## Remaining hardware gates

- Verify `os.urandom(16)`, independent random streams across launches, 25-point
  slice duration, 250 samples/minute, and responsive LEFT/RIGHT on both pages.
- Verify BACK/HOME cleanup and re-open: no computation or communication after
  exit. Test both physical orientations and capacitive/physical controls.
- Identify calibrated battery percentage API; compare readings on USB and
  battery. Wire the core policy only after verification: >60% 60 seconds,
  30–60% 120 seconds, <=30% paused, <20% networking disabled. No USB boost.
- Identify Wi-Fi configuration using the existing device settings. Never
  commit credentials. Inspect TLS CA validation, hostname verification, DNS,
  handshake, socket timeouts, and resource cleanup on the actual firmware.
- Verify the cooperative transport state machine by proving DNS,
  connection, TLS and reads cannot block the renderer. A timeout on a synchronous
  HTTPS request alone does not prove UI responsiveness. No background thread.
- Bound a request to 5 seconds end to end; limit response size and per-frame
  network work. Test AP loss, DNS failure, TLS rejection, delayed/partial
  responses, response loss after acceptance, retries and exit during sync.
- Confirm counters acknowledged by the coordinator match immutable snapshots;
  retain pending snapshots on failure and display cached values as stale.
- Compare idle, connected-idle and Eco compute battery consumption over equal
  intervals with identical brightness; document duration and charge readings.
- Verify icon installation and launcher rendering, following the menu patch
  instructions if stock firmware ignores custom icons.

Simulator/static/host tests are useful but cannot close these hardware gates.

## Hosting decision

User instruction selects Azure App Service instead of the Container Apps example
in spec v0.1. Keep service code, infrastructure and dashboard in a separate repo.
