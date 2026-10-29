"""Bounded work, wrap-safe schedules, retries and foreground lifecycle."""
import importlib.util
from pathlib import Path
import unittest

APP = Path(__file__).resolve().parents[1] / 'badge/apps/badgecluster'
spec = importlib.util.spec_from_file_location('cluster_core', APP / 'cluster_core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
WRAP = 1 << 30


def add(tick, delta):
    return (tick + delta) % WRAP


def diff(a, b):
    return (a - b + WRAP // 2) % WRAP - WRAP // 2


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.worker = core.Worker(0, add, diff, lambda: 0.5, lambda: self.now)

    def test_slices_and_no_catch_up(self):
        self.assertEqual(self.worker.step(59999), 0)
        self.now = 600000
        for index in range(10):
            self.assertEqual(self.worker.step(self.now), 25)
        self.assertEqual((self.worker.samples, self.worker.inside), (250, 250))
        self.assertEqual(self.worker.step(self.now), 0)
        self.now += 60000
        self.assertEqual(self.worker.step(self.now), 25)

    def test_time_budget_stops_before_25(self):
        def advancing_clock():
            self.now += 1
            return self.now
        self.worker.clock = advancing_clock
        self.assertLessEqual(self.worker.step(60000), 5)

    def test_wrap(self):
        self.worker = core.Worker(WRAP-1000, add, diff, lambda: 1.0, lambda: 59000)
        self.assertEqual(self.worker.step(58999), 0)
        self.assertEqual(self.worker.step(59000), 25)
        self.assertEqual(self.worker.inside, 0)

    def test_stop_mid_batch_prevents_all_future_work(self):
        self.worker.step(60000)
        self.worker.close()
        self.assertEqual(self.worker.step(120000), 0)
        self.assertEqual(self.worker.samples, 25)
        self.assertEqual(self.worker.remaining, 0)

    def test_battery_thresholds_and_resume_without_catch_up(self):
        for percent, interval, paused, network in [
            (None, 60000, False, True), (61, 60000, False, True),
            (60, 120000, False, True), (31, 120000, False, True),
            (30, 120000, True, True), (20, 120000, True, True),
            (19, 120000, True, False)]:
            self.worker.power(percent)
            self.assertEqual((self.worker.interval, self.worker.paused,
                              self.worker.network_allowed), (interval, paused, network))
        self.now = 600000
        self.worker.power(None)
        self.assertEqual(self.worker.step(self.now), 0)

    def test_sample_limit(self):
        self.worker.samples = core.MAX_SAMPLES - 3
        self.assertEqual(self.worker.step(60000), 3)
        self.assertEqual(self.worker.step(120000), 0)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.client = core.Contributions('node-a', 'session-b', 0, add, diff)

    def response(self, request):
        return dict(acceptedSequence=request['sequence'], acceptedSamples=request['samples'],
                    cluster=dict(samples=1000, inside=780, activeNodes=2))

    def test_retry_snapshot_stays_immutable_until_acknowledged(self):
        first = self.client.request(250, 198)
        first['samples'] = 999
        self.client.failed(120000)
        retry = self.client.request(500, 390)
        self.assertEqual((retry['sequence'], retry['samples']), (1, 250))
        self.client.acknowledge(self.response(retry), 150000)
        self.assertEqual(self.client.accepted_samples, 250)
        self.assertEqual(self.client.request(500, 390)['sequence'], 2)

    def test_capped_backoff_and_staleness(self):
        self.assertFalse(self.client.due(59999))
        self.assertTrue(self.client.due(60000))
        for delay in (30000, 60000, 120000, 300000, 300000):
            self.client.failed(0)
            self.assertEqual(self.client.next_sync, delay)
        self.assertTrue(self.client.stale(0))
        req = self.client.request(250, 198)
        self.client.acknowledge(self.response(req), WRAP - 1000)
        self.assertFalse(self.client.stale(0))
        self.assertFalse(self.client.stale(118999))
        self.assertTrue(self.client.stale(119000))

    def test_invalid_ack_does_not_change_state(self):
        req = self.client.request(250, 198)
        bad = self.response(req)
        bad['acceptedSamples'] = 500
        with self.assertRaises(ValueError):
            self.client.acknowledge(bad, 0)
        self.assertEqual(self.client.accepted_samples, 0)
        self.assertEqual(self.client.pending, req)
        bad = self.response(req)
        bad['cluster']['inside'] = 2000
        with self.assertRaises(ValueError):
            self.client.acknowledge(bad, 0)

    def test_close_prevents_further_requests(self):
        self.client.request(250, 198)
        self.client.close()
        self.assertIsNone(self.client.request(500, 390))
        self.assertFalse(self.client.due(999999))


if __name__ == '__main__':
    unittest.main()
