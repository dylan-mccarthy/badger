"""Cooperative foreground computation and transport-independent Azure sync."""

REQUEST_MS = 5000


class Session:
    def __init__(self, worker, contributions, transport=None):
        self.worker = worker
        self.contributions = contributions
        self.transport = transport
        self.inflight = False
        self.deadline = None
        self.stopped = False
        self.error = None
        self.ready = False

    def _fail(self, now, category):
        print("BadgeCluster: sync failed (%s); retrying with backoff." % category)
        self.transport.cancel()
        self.inflight = False
        self.error = category
        self.ready = False
        self.contributions.failed(now)

    def step(self, now):
        if self.stopped:
            return
        self.worker.step(now)
        if self.transport is None:
            return
        if not self.worker.network_allowed:
            if self.inflight:
                self._fail(now, "low_battery")
            return
        if not self.inflight:
            if self.error is not None and not self.contributions.due(now):
                return
            try:
                prepare = getattr(self.transport, "prepare", None)
                self.ready = prepare() if prepare is not None else True
            except OSError as error:
                print("BadgeCluster: Wi-Fi preparation error:", error)
                self._fail(now, "network")
                return
            if not self.ready:
                return
        if self.inflight:
            if self.worker.diff(now, self.deadline) >= 0:
                self._fail(now, "timeout")
                return
            try:
                result = self.transport.poll()
                if result is not None:
                    kind, response = result
                    if kind != "ok":
                        self._fail(now, "network")
                    else:
                        self.contributions.acknowledge(response, now)
                        if self.worker.diff(self.worker.next_batch,
                                            self.contributions.next_sync) < 0:
                            self.contributions.next_sync = self.worker.next_batch
                        self.inflight = False
                        self.error = None
                        print("BadgeCluster: Azure accepted %d samples." %
                              self.contributions.accepted_samples)
            except OSError as error:
                print("BadgeCluster: network error:", error)
                self._fail(now, "network")
            except (ValueError, TypeError, KeyError) as error:
                print("BadgeCluster: transport/response error:", error)
                self._fail(now, "invalid_response")
            return
        # Prefer a complete batch; never wait for network to continue computing.
        if self.contributions.due(now) and not self.worker.remaining:
            try:
                snapshot = self.contributions.request(self.worker.samples, self.worker.inside)
                self.transport.start(snapshot)
                self.inflight = True
                self.error = None
                self.deadline = self.worker.add(now, REQUEST_MS)
            except (OSError, ValueError):
                self._fail(now, "network")

    def status(self):
        if self.stopped:
            return "STOPPING"
        if self.worker.paused:
            return "PAUSED"
        if self.inflight:
            return "CONNECTING"
        if self.transport is None or self.error is not None:
            return "OFFLINE"
        if not self.ready:
            return "JOINING WIFI"
        if self.contributions.received_at is None:
            return "WAITING SYNC"
        if self.contributions.offline:
            return "OFFLINE"
        return "CONTRIBUTING"

    def close(self):
        self.stopped = True
        self.worker.close()
        self.contributions.close()
        if self.transport is not None:
            self.transport.cancel()
        self.inflight = False
