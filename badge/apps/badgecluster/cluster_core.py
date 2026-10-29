"""Hardware-independent Eco scheduler and cumulative contribution protocol."""

BATCH_SIZE = 250
SLICE_SIZE = 25
SLICE_MS = 5
SYNC_MS = 60000
BACKOFF_MS = (30000, 60000, 120000, 300000)
MAX_SAMPLES = 1000000000000


class Worker:
    def __init__(self, now, ticks_add, ticks_diff, random_value, clock):
        self.add = ticks_add
        self.diff = ticks_diff
        self.random = random_value
        self.clock = clock
        self.next_batch = ticks_add(now, 60000)
        self.remaining = 0
        self.samples = 0
        self.inside = 0
        self.interval = 60000
        self.paused = False
        self.network_allowed = True
        self.stopped = False
        self.completed = 0

    def power(self, percent=None):
        # Only pass calibrated, device-verified percentages. None means Eco.
        interval = 120000 if percent is not None and percent <= 60 else 60000
        paused = percent is not None and percent <= 30
        if interval != self.interval or paused != self.paused:
            self.next_batch = self.add(self.clock(), interval)
            self.remaining = 0
        self.interval = interval
        self.paused = paused
        self.network_allowed = percent is None or percent >= 20

    def step(self, now):
        if self.stopped or self.paused or self.samples >= MAX_SAMPLES:
            return 0
        if not self.remaining and self.diff(now, self.next_batch) >= 0:
            self.remaining = min(BATCH_SIZE, MAX_SAMPLES - self.samples)
            # Schedule from now: missed intervals never create catch-up work.
            self.next_batch = self.add(now, self.interval)
        start = self.clock()
        count = 0
        while self.remaining and count < SLICE_SIZE:
            if self.diff(self.clock(), start) >= SLICE_MS:
                break
            x, y = self.random(), self.random()
            self.inside += int(x * x + y * y <= 1)
            self.samples += 1
            self.remaining -= 1
            count += 1
            if not self.remaining:
                self.completed += 1
        return count

    def close(self):
        self.stopped = True
        self.remaining = 0


class Contributions:
    """An immutable pending snapshot survives retries; transport is separate."""
    def __init__(self, node_id, session_id, now, ticks_add, ticks_diff):
        self.node_id = node_id
        self.session_id = session_id
        self.add, self.diff = ticks_add, ticks_diff
        self.next_sync = ticks_add(now, SYNC_MS)
        self.sequence = 0
        self.pending = None
        self.accepted_samples = 0
        self.cluster = None
        self.received_at = None
        self.failures = 0
        self.offline = True
        self.stopped = False

    def due(self, now):
        return not self.stopped and self.diff(now, self.next_sync) >= 0

    def request(self, samples, inside):
        if self.stopped:
            return None
        if (type(samples) is not int or type(inside) is not int or
                not 0 <= inside <= samples <= MAX_SAMPLES):
            raise ValueError("Invalid local counters")
        if self.pending is None:
            self.sequence += 1
            self.pending = dict(protocolVersion=1, nodeId=self.node_id,
                                sessionId=self.session_id, sequence=self.sequence,
                                samples=samples, inside=inside)
        return dict(self.pending)

    def acknowledge(self, response, now):
        pending = self.pending
        if pending is None:
            raise ValueError("No pending contribution")
        # An acknowledgement must match our immutable snapshot exactly.
        if not isinstance(response, dict):
            raise ValueError("Invalid acknowledgement")
        if (type(response.get("acceptedSequence")) is not int or
                type(response.get("acceptedSamples")) is not int or
                response.get("acceptedSequence") != pending["sequence"] or
                response.get("acceptedSamples") != pending["samples"]):
            raise ValueError("Unexpected acknowledgement")
        cluster = response.get("cluster")
        if not isinstance(cluster, dict):
            raise ValueError("Missing cluster")
        samples, inside = cluster.get("samples"), cluster.get("inside")
        active = cluster.get("activeNodes")
        if (type(samples) is not int or type(inside) is not int or
                type(active) is not int or not 0 <= inside <= samples or
                samples < pending["samples"] or inside < pending["inside"] or active < 0):
            raise ValueError("Invalid cluster counters")
        # Derive pi locally instead of trusting an arbitrary numeric value.
        self.cluster = dict(cluster)
        self.cluster["piEstimate"] = 4 * inside / samples if samples else None
        self.accepted_samples = pending["samples"]
        self.pending = None
        self.received_at = now
        self.offline = False
        self.failures = 0
        self.next_sync = self.add(now, SYNC_MS)

    def failed(self, now):
        delay = BACKOFF_MS[min(self.failures, len(BACKOFF_MS) - 1)]
        self.failures += 1
        self.offline = True
        self.next_sync = self.add(now, delay)

    def stale(self, now):
        return (self.offline or self.received_at is None or
                self.diff(now, self.received_at) >= SYNC_MS * 2)

    def close(self):
        self.stopped = True
        self.pending = None
