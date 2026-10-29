"""Foreground orchestration, loss/retry, timeout, pause and exit behavior."""
import importlib.util
from pathlib import Path
import unittest
import test_badgecluster as base

spec = importlib.util.spec_from_file_location('cluster_session', base.APP / 'cluster_session.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Transport:
    def __init__(self):
        self.requests = []
        self.result = None
        self.cancels = 0

    def start(self, request):
        self.requests.append(request)

    def poll(self):
        result, self.result = self.result, None
        return result

    def cancel(self):
        self.cancels += 1


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.worker = base.core.Worker(0, base.add, base.diff, lambda: 0.5, lambda: self.now)
        self.client = base.core.Contributions('node-test', 'session-test', 0, base.add, base.diff)
        self.transport = Transport()
        self.session = module.Session(self.worker, self.client, self.transport)

    def start_request(self):
        self.now = 60000
        self.client.next_sync = self.now
        for _ in range(10):
            self.session.step(self.now)
        self.assertTrue(self.session.inflight)
        self.assertEqual(self.worker.samples, 250)

    def response(self):
        pending = self.client.pending
        return dict(acceptedSequence=pending['sequence'], acceptedSamples=pending['samples'],
                    cluster=dict(samples=pending['samples'], inside=pending['inside'], activeNodes=1))

    def test_waits_for_batch_to_finish_and_for_server_ack(self):
        self.start_request()
        self.assertEqual(self.client.accepted_samples, 0)
        self.assertEqual(self.session.status(), 'CONNECTING')
        self.transport.result = ('ok', self.response())
        self.session.step(self.now)
        self.assertEqual(self.client.accepted_samples, 250)
        self.assertEqual(self.session.status(), 'CONTRIBUTING')

    def test_uploads_each_batch_without_adding_request_duration_to_interval(self):
        self.start_request()
        self.now = 62000
        self.transport.result = ('ok', self.response())
        self.session.step(self.now)
        self.assertEqual(self.client.next_sync, 120000)
        self.now = 119999
        self.session.step(self.now)
        self.assertEqual(len(self.transport.requests), 1)
        self.now = 120000
        for _ in range(9):
            self.session.step(self.now)
        self.assertEqual(len(self.transport.requests), 1)
        self.session.step(self.now)
        self.assertEqual(len(self.transport.requests), 2)
        self.assertEqual(self.transport.requests[-1]['samples'], 500)
        self.assertEqual(self.transport.requests[-1]['sequence'], 2)

    def test_successful_retry_waits_for_next_batch_not_another_full_minute(self):
        self.start_request()
        self.transport.result = ('error', None)
        self.session.step(self.now)
        self.now = 90000
        self.session.step(self.now)
        self.now = 92000
        self.transport.result = ('ok', self.response())
        self.session.step(self.now)
        self.assertEqual(self.client.next_sync, 120000)

    def test_batch_alignment_does_not_skip_failure_backoff(self):
        self.start_request()
        for delay in (30000, 60000, 120000):
            self.transport.result = ('error', None)
            self.session.step(self.now)
            deadline = base.add(self.now, delay)
            self.assertEqual(self.client.next_sync, deadline)
            count = len(self.transport.requests)
            self.now = base.add(deadline, -1)
            self.session.step(self.now)
            self.assertEqual(len(self.transport.requests), count)
            self.now = deadline
            for _ in range(10):
                self.session.step(self.now)
            self.assertTrue(self.session.inflight)
            self.assertEqual(self.transport.requests[-1]['samples'], 250)

    def test_batch_alignment_is_wrap_safe(self):
        self.start_request()
        self.now = base.WRAP - 1000
        self.worker.next_batch = 1000
        self.transport.result = ('ok', self.response())
        self.session.deadline = base.add(self.now, 5000)
        self.session.step(self.now)
        self.assertEqual(self.client.next_sync, 1000)

    def test_prepares_wifi_before_first_sync_without_claiming_offline(self):
        ready = [False]
        self.transport.prepare = lambda: ready[0]
        self.session.step(0)
        self.assertEqual(self.session.status(), 'JOINING WIFI')
        self.assertEqual(self.transport.requests, [])
        ready[0] = True
        self.session.step(1)
        self.assertEqual(self.session.status(), 'WAITING SYNC')
        self.assertEqual(self.transport.requests, [])

    def test_wifi_preparation_failure_uses_backoff(self):
        calls = []
        def fail():
            calls.append(True)
            raise OSError("AP unavailable")
        self.transport.prepare = fail
        self.session.step(0)
        self.session.step(1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.session.status(), 'OFFLINE')
        self.assertEqual(self.client.next_sync, 30000)

    def test_due_sync_waits_for_wifi_and_does_not_consume_request_deadline(self):
        self.transport.prepare = lambda: False
        self.now = 120000
        self.session.step(self.now)
        self.assertFalse(self.session.inflight)
        self.assertIsNone(self.session.deadline)
        self.assertEqual(self.client.sequence, 0)
        self.transport.prepare = lambda: True
        for _ in range(10):
            self.session.step(self.now)
        self.assertTrue(self.session.inflight)
        self.assertEqual(self.session.deadline, 125000)

    def test_response_loss_retries_the_same_snapshot_with_backoff(self):
        self.start_request()
        original = dict(self.transport.requests[-1])
        self.transport.result = ('error', None)
        self.session.step(self.now)
        self.assertTrue(self.client.offline)
        self.assertEqual(self.client.next_sync, 90000)
        self.now = 90000
        self.session.step(self.now)
        self.assertEqual(self.transport.requests[-1], original)
        self.transport.result = ('ok', self.response())
        self.session.step(self.now)
        self.assertEqual(self.client.accepted_samples, 250)
        self.assertEqual(self.client.failures, 0)

    def test_bounded_timeout_keeps_snapshot_and_cancels_transport(self):
        self.start_request()
        self.now += 5000
        self.session.step(self.now)
        self.assertFalse(self.session.inflight)
        self.assertEqual(self.session.error, 'timeout')
        self.assertEqual(self.transport.cancels, 1)
        self.assertEqual(self.client.pending['samples'], 250)
        self.assertEqual(self.client.accepted_samples, 0)

    def test_compute_continues_while_transport_is_pending(self):
        self.start_request()
        self.worker.next_batch = self.now
        self.session.step(self.now)
        self.assertEqual(self.worker.samples, 275)
        self.assertEqual(self.client.pending['samples'], 250)

    def test_close_during_request_stops_compute_and_all_future_sends(self):
        self.start_request()
        self.session.close()
        self.session.step(240000)
        self.assertEqual(self.worker.samples, 250)
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(self.transport.cancels, 1)
        self.assertEqual(self.session.status(), 'STOPPING')

    def test_low_battery_cancels_request_and_prevents_new_sends(self):
        self.start_request()
        self.worker.power(19)
        self.session.step(self.now)
        self.session.step(240000)
        self.assertEqual(len(self.transport.requests), 1)
        self.assertFalse(self.session.inflight)
        self.assertEqual(self.session.status(), 'PAUSED')

    def test_bad_ack_does_not_accept_local_work_or_crash(self):
        self.start_request()
        response = self.response()
        response['acceptedSamples'] = True
        self.transport.result = ('ok', response)
        self.session.step(self.now)
        self.assertEqual(self.client.accepted_samples, 0)
        self.assertEqual(self.session.error, 'invalid_response')
        self.assertEqual(self.session.status(), 'OFFLINE')

    def test_no_transport_is_offline_and_never_attempts_network(self):
        session = module.Session(self.worker, self.client)
        self.now = 120000
        for _ in range(10):
            session.step(self.now)
        self.assertEqual(self.worker.samples, 250)
        self.assertEqual(self.client.sequence, 0)
        self.assertEqual(session.status(), 'OFFLINE')

    def test_timeout_and_retry_across_ticks_wrap(self):
        self.now = base.WRAP - 1000
        self.worker.next_batch = base.add(self.now, 60000)
        self.client.next_sync = self.now
        self.session.step(self.now)
        self.assertTrue(self.session.inflight)
        self.now = base.add(self.now, 5000)
        self.session.step(self.now)
        self.assertEqual(self.session.error, 'timeout')
        self.assertEqual(self.client.next_sync, base.add(self.now, 30000))

    def test_wrong_global_inside_count_is_rejected(self):
        self.start_request()
        response = self.response()
        response['cluster']['inside'] = 0
        self.transport.result = ('ok', response)
        self.session.step(self.now)
        self.assertTrue(self.client.offline)
        self.assertEqual(self.client.accepted_samples, 0)


if __name__ == '__main__':
    unittest.main()
