#!/usr/bin/env python3
"""Opt-in real HTTPS test of actual badge app, hosted through the Pillow adapter.

Networking runs in a disposable host subprocess, never in badge firmware.
No timer/network threads are shipped with or injected into a physical badge.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from preview_badgecluster import preview

DEFAULT_URL = 'https://badgecluster-37nlkk2a4ucny.azurewebsites.net'


def https_request(base_url, payload):
    request = urllib.request.Request(base_url + '/api/v1/contributions',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=5) as response:
        data = response.read(8193)
        if len(data) > 8192:
            raise ValueError('Oversized response')
        return json.loads(data)


class HostTransport:
    test_mode = True

    def __init__(self, base_url):
        self.base_url = base_url
        self.process = None
        self.retired = []
        self.requests = []
        self.responses = []
        self.drop_next_response = False
        self.dropped_response = None
        self.cancelled = 0

    def start(self, payload):
        if self.process is not None:
            raise OSError('Request already running')
        self.requests.append(dict(payload))
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
            '--http-worker', '--base-url', self.base_url], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.process.stdin.write(json.dumps(payload))
        self.process.stdin.close()

    def poll(self):
        # poll() never waits on DNS, TLS, HTTP, or a host process.
        remaining = []
        for process in self.retired:
            if process.poll() is None:
                remaining.append(process)
            else:
                process.stdout.close()
                process.stderr.close()
        self.retired = remaining
        if self.process is None or self.process.poll() is None:
            return None
        process, self.process = self.process, None
        try:
            if process.returncode:
                return ('error', None)
            response = json.loads(process.stdout.read(8193))
            self.responses.append(response)
            if self.drop_next_response:
                self.drop_next_response = False
                self.dropped_response = response
                return ('error', None)
            return ('ok', response)
        except (ValueError, TypeError):
            return ('error', None)
        finally:
            process.stdout.close()
            process.stderr.close()

    def cancel(self):
        if self.process is not None:
            self.process.kill()
            self.retired.append(self.process)
            self.process = None
            self.cancelled += 1

    def close(self):
        self.cancel()
        for process in self.retired:
            process.wait(timeout=2)
            process.stdout.close()
            process.stderr.close()
        self.retired.clear()


def run_live(base_url, output):
    transport = HostTransport(base_url)
    report = {}

    def driver(update, screen, state, output):
        app = update.__globals__
        worker, session, client = app['worker'], app['session'], app['client']
        update()
        # Accelerate only the monotonic schedule; every point is really computed.
        state.now = 60000
        client.next_sync = state.now
        transport.drop_next_response = True
        for _ in range(10):
            update()
        assert worker.samples == 250 and session.inflight
        original = dict(transport.requests[-1])

        def finish_request():
            start = time.monotonic()
            initial_tick = state.now
            frames = 0
            while session.inflight:
                if time.monotonic() - start > 7:
                    raise AssertionError('Host request exceeded bounded test deadline')
                state.now = initial_tick + int((time.monotonic() - start) * 1000)
                # Prove real page navigation can continue during HTTPS.
                state.button = 'right' if frames % 2 else 'left'
                update()
                frames += 1
                time.sleep(0.01)
            state.button = None
            return frames

        frames = finish_request()
        assert transport.dropped_response is not None, 'Azure request did not succeed'
        assert client.accepted_samples == 0 and client.offline
        assert client.pending == original
        state.button = 'right'
        update()
        screen.canvas.save(output / 'azure-response-lost.png')
        # Retry exactly the same cumulative snapshot after the 30-second backoff.
        state.now = client.next_sync
        state.button = None
        update()
        assert session.inflight
        assert transport.requests[-1] == original
        frames += finish_request()
        assert not client.offline and client.accepted_samples == 250
        assert transport.responses[-1]['acceptedSamples'] == 250
        assert transport.responses[-1]['acceptedSequence'] == original['sequence']
        aggregate_stable = (transport.responses[-1]['cluster']['samples'] ==
                            transport.dropped_response['cluster']['samples'])
        state.button = 'right'
        update()
        screen.canvas.save(output / 'azure-stats.png')
        assert app['page'] == 1
        state.button = 'left'
        update()
        screen.canvas.save(output / 'azure-mona.png')
        assert app['page'] == 0
        report.update(nodeId=client.node_id, sessionId=client.session_id,
            samples=worker.samples, inside=worker.inside, acceptedSamples=client.accepted_samples,
            requests=transport.requests, responses=transport.responses,
            responsiveFramesDuringHttps=frames, cluster=client.cluster,
            aggregateStableAcrossRetry=aggregate_stable,
            testScope='CPython actual app + Pillow graphics + real Azure HTTPS; not firmware simulation')
        state.button = 'back'
        assert update() is False
        assert worker.stopped and session.stopped and client.stopped
        before = len(transport.requests)
        state.now += 180000
        state.button = None
        update()
        assert len(transport.requests) == before and worker.samples == 250
        report['exitStopsWork'] = True
        (output / 'azure-test-report.json').write_text(json.dumps(report, indent=2) + '\n')

    try:
        preview(output=output, transport=transport, driver=driver)
    finally:
        transport.close()
    print(json.dumps({key: report[key] for key in ('samples', 'acceptedSamples', 'responsiveFramesDuringHttps', 'exitStopsWork')}, indent=2))
    print('Azure report and screenshots:', output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Opt in to adding 250 real computed samples to the Azure cluster')
    parser.add_argument('--base-url', default=DEFAULT_URL)
    parser.add_argument('--output', type=Path, default=Path('/tmp/badgecluster-azure-test'))
    parser.add_argument('--http-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    url = urlparse(args.base_url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
        parser.error('Expected an HTTPS base URL without credentials, query, or path')
    base = args.base_url.rstrip('/')
    if args.http_worker:
        try:
            print(json.dumps(https_request(base, json.load(sys.stdin))))
        except (OSError, ValueError, urllib.error.URLError):
            sys.exit(1)
    elif args.live:
        run_live(base, args.output)
    else:
        parser.error('Use --live to opt in; the test adds 250 actual Monte Carlo samples to Azure')


if __name__ == '__main__':
    main()
