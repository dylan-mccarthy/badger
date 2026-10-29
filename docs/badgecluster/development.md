# Resume BadgeCluster development

## Repositories and current state

- **Badge:** [dylan-mccarthy/badger](https://github.com/dylan-mccarthy/badger),
  development branch `feature/badgecluster`, app `badge/apps/badgecluster/`.
  The app and test harness are proposed together in a draft PR for review.
- **Coordinator:** [dylan-mccarthy/BadgeCluster](https://github.com/dylan-mccarthy/BadgeCluster),
  branch `main`, .NET 10 API/dashboard, Azure Table Storage and App Service.
- **Live service:** https://badgecluster-37nlkk2a4ucny.azurewebsites.net,
  Australia East, B1 plan, resource group `badgecluster`.

The deployable app now has a cooperative device HTTPS transport with explicit
CA/hostname verification, UDP DNS and NTP clock setup. An initial offline build
was deployed and confirmed working by the user. The current networking build
has received live device acknowledgements for two normally scheduled batches
one minute apart; see [device-spike.md](device-spike.md) for timings.
The CPython/Pillow host adapter independently passed with 250 actual
samples, a lost response and an idempotent retry; that is not hardware evidence.

## Badge checkout and local checks

Until the PR is merged, check out the feature branch:

```sh
git clone --branch feature/badgecluster https://github.com/dylan-mccarthy/badger.git
cd badger
python -m venv .venv
# Activate .venv for your shell before installing.
python -m pip install -r tools/requirements-badgecluster.txt
python -m unittest discover -s tests -p 'test_badgecluster*.py'
python tools/preview_badgecluster.py
python .github/skills/hyb26-skill/scripts/validate_app.py badge/apps/badgecluster
python .github/scripts/validate_app_readmes.py badge/apps
```

Sixty identity/logic/session/transport tests and both validators pass locally.
The host preview and live host test passed for the original build.
`.github/workflows/badgecluster.yml` repeats these checks on relevant
pushes and PRs. CI never submits samples to production.

To explicitly test against Azure:

```sh
python tools/test_badgecluster_azure.py --live --output /tmp/badgecluster-azure-test
```

This contributes another 250 genuinely computed samples to the shared totals.
Use `--base-url https://your-test-coordinator` to select another coordinator.
[Azure test evidence](azure-testing.md) includes committed screenshots and a
small run summary; a new run writes its full JSON report to the output folder.
No cloud login is needed for the public contribution API.

## Code map

| File | Responsibility |
| --- | --- |
| `badge/apps/badgecluster/__init__.py` | Mona/Stats UI, controls and foreground lifecycle |
| `cluster_core.py` in that directory | Bounded worker, cumulative snapshots, acks and backoff |
| `cluster_session.py` | Cooperative worker/network orchestration and request deadline |
| `cluster_transport.py` | Device verified HTTPS, bounded UDP DNS/NTP, cancellation |
| `cluster_identity.py` | One-time random node identity persisted in `/state`, with atomic writes and validation |
| `tools/preview_badgecluster.py` | CPython/Pillow graphics/input adapter |
| `tools/test_badgecluster_azure.py` | Opt-in live HTTPS adapter/test; host subprocesses only |
| `tools/build_badgecluster_assets.py` | Original reproducible pixel art |

## Service deployment and next work

The service repo's [deployment guide](https://github.com/dylan-mccarthy/BadgeCluster/blob/main/docs/deployment.md)
explains the manual GitHub Actions deploy button and exact branch-bound OIDC
subject. Azure login, infrastructure deployment, app deployment and readiness
have passed in [this run](https://github.com/dylan-mccarthy/BadgeCluster/actions/runs/40728894117).
There are no GitHub secrets or environment variables to re-enter; nonsecret
identifiers are in the service repo's `infra/deployment.json`.

Next: follow [device-spike.md](device-spike.md) to finish the live networking,
failure-injection, battery calibration, input/orientation and exit checks.
Do not treat host tests or offline rendering as live device networking evidence.
Then connect multiple badges and measure Eco power use.
Do not copy the host subprocess transport onto a badge.

The healthy device upload cadence is now aligned with each completed
250-sample batch, roughly every 60 seconds, rather than the original
120-second sync interval. Successful requests do not shift the next upload
past the next batch; failures retain the 30/60/120/300-second retry backoff.
Computation remains at 250 samples per minute. Cached statistics become stale
after two minutes without an acknowledgement.

Read `badge/AGENTS.md`, `hardware/README.md`, and the repository's HYB26 skill
before device work; read `hardware/USB_SERIAL.md` before USB/serial deployment.
See [specification.md](specification.md) for the original baseline; the user's
App Service hosting choice and split repositories supersede its combined layout
and Container Apps example.
