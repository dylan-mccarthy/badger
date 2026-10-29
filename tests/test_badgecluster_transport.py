"""Device transport: DNS/NTP validation, bounded I/O, TLS and cancellation."""
import errno
import importlib.util
import json
from pathlib import Path
import socket
import ssl
import struct
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

APP = Path(__file__).resolve().parents[1] / "badge/apps/badgecluster"
spec = importlib.util.spec_from_file_location("cluster_transport", APP / "cluster_transport.py")
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


def answer(query, ip=b"\x01\x02\x03\x04"):
    return (query[:2] + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00" +
            query[12:] + b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + ip)


def ntp(nonce, seconds=1793232000):
    packet = bytearray(48)
    packet[0:2] = b"\x24\x01"
    packet[24:32] = nonce
    packet[40:44] = struct.pack(">I", seconds + 2208988800)
    return packet


class Datagram:
    def __init__(self):
        self.sent = []
        self.responses = []
        self.closed = False

    def setblocking(self, value):
        assert value is False

    def connect(self, address):
        self.address = address

    def send(self, data):
        self.sent.append(data)

    def recv(self, maximum):
        if not self.responses:
            raise OSError(errno.EAGAIN)
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class Http:
    instances = []

    def __init__(self, host, **kwargs):
        self.host, self.options = host, kwargs
        self.done = False
        self.reset_called = False
        self.instances.append(self)

    def fetch(self, path, **kwargs):
        self.path, self.request = path, kwargs

    def update(self):
        pass

    def to_json(self):
        return {"acceptedSamples": 250}

    def reset(self):
        self.reset_called = True


class DeviceTransportTests(unittest.TestCase):
    def setUp(self):
        self.sockets = []
        def new_socket(*args):
            sock = Datagram()
            self.sockets.append(sock)
            return sock
        self.connected = True
        self.now = 0
        self.failure = None
        self.rtc = []
        wifi = SimpleNamespace(connect=lambda **kw: self.connected,
                               nameserver=lambda: "192.0.2.53",
                               is_connected=lambda: self.connected,
                               failure=lambda: self.failure,
                               disconnect=Mock())
        self.clock = SimpleNamespace(
            gmtime=time.gmtime, ticks_ms=lambda: self.now,
            ticks_add=lambda a, b: (a + b) % (1 << 30),
            ticks_diff=lambda a, b: (a - b + (1 << 29)) % (1 << 30) - (1 << 29))
        sockets = SimpleNamespace(socket=new_socket, AF_INET=2, SOCK_DGRAM=2)
        machine = SimpleNamespace(RTC=lambda: SimpleNamespace(datetime=self.rtc.append))
        Http.instances = []
        self.device = transport.DeviceTransport(
            wifi, sockets, self.clock, machine, lambda n: bytes(range(n)), Http)

    def begin(self):
        self.device.start({"samples": 250})
        self.device.poll()

    def reach_http(self):
        self.begin()
        self.sockets[0].responses.append(ntp(self.device.nonce))
        self.device.poll()
        self.device.poll()
        self.sockets[1].responses.append(answer(self.device.query))
        self.device.poll()

    def test_wifi_pending_creates_no_sockets(self):
        self.connected = False
        self.begin()
        self.assertEqual(self.device.phase, "wifi")
        self.assertEqual(self.sockets, [])

    def test_full_request_verifies_clock_dns_limits_and_cleanup(self):
        self.reach_http()
        self.assertEqual(self.rtc[0][:3], (2026, 10, 29))
        client = self.device.http
        self.assertEqual(client.host, transport.HOST)
        self.assertEqual(client.address, ("1.2.3.4", 443))
        self.assertEqual(client.options["max_bytes"], 8192)
        self.assertEqual(client.request["method"], "POST")
        self.assertEqual(json.loads(client.request["data"]), {"samples": 250})
        client.done = True
        self.assertEqual(self.device.poll(), ("ok", {"acceptedSamples": 250}))
        self.assertTrue(client.reset_called)
        self.assertTrue(all(sock.closed for sock in self.sockets))
        self.assertIsNone(self.device.phase)

    def test_pending_datagram_returns_immediately(self):
        self.begin()
        self.assertIsNone(self.device.poll())
        self.assertEqual(self.device.phase, "time")

    def test_cancel_during_bootstrap_closes_socket(self):
        self.begin()
        self.device.cancel()
        self.assertTrue(self.sockets[0].closed)
        self.assertIsNone(self.device.payload)

    def test_cancel_during_http_closes_client(self):
        self.reach_http()
        client = self.device.http
        self.device.cancel()
        self.assertTrue(client.reset_called)

    def test_no_overlapping_requests(self):
        self.begin()
        with self.assertRaises(OSError):
            self.device.start({})

    def test_retry_resolves_dns_again_without_repeating_clock_sync(self):
        self.reach_http()
        self.device.cancel()
        self.begin()
        self.assertEqual(self.device.phase, "dns")
        self.assertEqual(self.sockets[-1].address, ("192.0.2.53", 53))
        self.assertEqual(len(self.rtc), 1)

    def test_ntp_2000_epoch_port_sets_same_rtc(self):
        self.device.clock.gmtime = lambda value: time.gmtime(value + 946684800)
        self.reach_http()
        self.assertEqual(self.rtc[0][:3], (2026, 10, 29))

    def test_terminal_wifi_failure_clears_firmware_state_for_next_attempt(self):
        self.connected = False
        self.failure = "AP unavailable"
        with self.assertRaises(OSError):
            self.device.prepare()
        self.device.wifi.disconnect.assert_called_once()
        self.assertIsNone(self.device.wifi_deadline)
        self.failure = None
        self.assertFalse(self.device.prepare())
        self.connected = True
        self.assertTrue(self.device.prepare())
        self.assertIsNone(self.device.wifi_deadline)

    def test_failure_reported_during_connect_is_not_silently_pending(self):
        self.connected = False
        def fail(**kwargs):
            self.failure = "AP unavailable"
            return False
        self.device.wifi.connect = fail
        with self.assertRaises(OSError):
            self.device.prepare()
        self.device.wifi.disconnect.assert_called_once()

    def test_wifi_join_deadline_is_bounded_even_without_firmware_failure(self):
        self.connected = False
        self.assertFalse(self.device.prepare())
        self.now = transport.WIFI_JOIN_MS - 1
        self.assertFalse(self.device.prepare())
        self.device.wifi.disconnect.assert_not_called()
        self.now += 1
        with self.assertRaises(OSError):
            self.device.prepare()
        self.device.wifi.disconnect.assert_called_once()

    def test_wifi_join_deadline_survives_ticks_wrap(self):
        self.connected = False
        self.now = (1 << 30) - 100
        self.assertFalse(self.device.prepare())
        self.now = transport.WIFI_JOIN_MS - 100
        with self.assertRaises(OSError):
            self.device.prepare()

    def test_connected_wifi_does_not_restart_or_disconnect_radio(self):
        self.device.wifi.connect = Mock()
        self.assertTrue(self.device.prepare())
        self.device.wifi.connect.assert_not_called()
        self.device.wifi.disconnect.assert_not_called()


class PacketTests(unittest.TestCase):
    def test_dns_compressed_answer(self):
        query = transport.dns_query(transport.HOST, b"\x12\x34")
        self.assertEqual(transport.dns_address(answer(query), query), "1.2.3.4")

    def test_dns_unrelated_id_ignored(self):
        query = transport.dns_query(transport.HOST, b"\x12\x34")
        self.assertIsNone(transport.dns_address(b"\x56\x78" + answer(query)[2:], query))

    def test_dns_rejects_error_truncation_and_question_mismatch(self):
        query = transport.dns_query(transport.HOST, b"\x12\x34")
        packet = answer(query)
        for invalid in (b"", packet[:-1], packet[:3] + b"\x83" + packet[4:],
                        packet[:2] + b"\x83\x80" + packet[4:],
                        packet[:13] + b"x" + packet[14:]):
            with self.subTest(packet=invalid), self.assertRaises(ValueError):
                transport.dns_address(invalid, query)

    def test_ntp_requires_matching_nonce_synchronised_server_and_recent_time(self):
        nonce = b"12345678"
        self.assertEqual(transport.ntp_seconds(ntp(nonce), nonce), 1793232000)
        for invalid in (b"", ntp(b"87654321"), ntp(nonce, 1500000000),
                        b"\xe4" + ntp(nonce)[1:]):
            with self.subTest(packet=invalid), self.assertRaises(ValueError):
                transport.ntp_seconds(invalid, nonce)

    def test_trust_anchor_is_valid_ca_with_expected_fingerprint(self):
        import hashlib
        certificate = (APP / transport.CA_FILE).read_text()
        der = ssl.PEM_cert_to_DER_cert(certificate)
        self.assertEqual(hashlib.sha256(der).hexdigest(),
                         "cb3ccbb76031e5e0138f8dd39a23f9de47ffc35e43c1144cea27d46a5ab1cb5f")
        ssl.create_default_context(cadata=certificate)

    def test_verified_socket_uses_sni_without_blocking_handshake_or_dns(self):
        sock = Datagram()
        calls = []
        def wrap(raw, **kwargs):
            calls.append(kwargs)
            return raw
        class Fetch:
            def __init__(self):
                self._sock = None
            def _close_socket(self):
                self._sock.close()
        context = SimpleNamespace(wrap_socket=wrap)
        sockets = SimpleNamespace(socket=lambda *args: sock, AF_INET=2, SOCK_STREAM=1)
        cls = transport.verified_client(SimpleNamespace(AsyncFetch=Fetch), sockets, context)
        client = cls()
        client.address = ("192.0.2.1", 443)
        list(client._connect())
        self.assertEqual(calls, [{"server_hostname": transport.HOST, "do_handshake_on_connect": False}])
        self.assertEqual(sock.address, client.address)

    def test_device_factory_requires_certificate_validation_with_bundled_root(self):
        context = Mock()
        tls = SimpleNamespace(SSLContext=Mock(return_value=context),
                              PROTOCOL_TLS_CLIENT=1, CERT_REQUIRED=2)
        modules = {"tls": tls, "fetch": SimpleNamespace(AsyncFetch=object),
                   "machine": SimpleNamespace(),
                   "wifi": SimpleNamespace(network=SimpleNamespace(), secrets=SimpleNamespace())}
        certificate = (APP / transport.CA_FILE).read_text()
        with patch.object(transport.sys, "platform", "rp2"), \
                patch.dict(transport.sys.modules, modules), \
                patch("builtins.open", mock_open(read_data=certificate)):
            self.assertIsInstance(transport.create_transport(), transport.DeviceTransport)
        self.assertEqual(context.verify_mode, tls.CERT_REQUIRED)
        context.load_verify_locations.assert_called_once_with(ssl.PEM_cert_to_DER_cert(certificate))

    def test_http_line_without_terminator_yields_at_byte_budget(self):
        class Fetch:
            def __init__(self):
                self.remaining = 32
            def _budget_spend(self, count):
                self.remaining -= count
                return self.remaining <= 0
            def _budget_reset(self):
                self.remaining = 32
        cls = transport.verified_client(SimpleNamespace(AsyncFetch=Fetch), None, None)
        client = cls()
        client._sock = SimpleNamespace(read=lambda count: b"x")
        line = client._read_line()
        next(line)
        self.assertEqual(client.remaining, 0)
        for _ in range(63):
            next(line)
        with self.assertRaises(ValueError):
            next(line)


class WifiConnectionTests(unittest.TestCase):
    def setUp(self):
        self.wlan = Mock()
        self.wlan.isconnected.return_value = False
        self.wlan.status.return_value = 1
        self.network = SimpleNamespace(WLAN=Mock(return_value=self.wlan), STA_IF=0)
        self.settings = SimpleNamespace(WIFI_SSID="test-network", WIFI_PASSWORD="")
        self.wifi = transport.WifiConnection(self.network, self.settings)

    def test_starts_once_with_existing_settings_and_empty_password(self):
        self.assertFalse(self.wifi.connect())
        self.assertFalse(self.wifi.connect())
        self.wlan.connect.assert_called_once_with("test-network", "")
        self.wlan.active.assert_called_once_with(True)

    def test_failed_attempt_can_disconnect_and_reassociate(self):
        self.wifi.connect()
        self.wlan.status.return_value = -2
        self.assertTrue(self.wifi.failure())
        self.wifi.disconnect()
        self.assertFalse(self.wifi.failure())
        self.wlan.disconnect.assert_called_once()
        self.wlan.status.return_value = 1
        self.wifi.connect()
        self.assertEqual(self.wlan.connect.call_count, 2)

    def test_existing_connection_is_not_restarted(self):
        self.wlan.isconnected.return_value = True
        self.assertTrue(self.wifi.connect())
        self.wlan.connect.assert_not_called()
        self.assertTrue(self.wifi.is_connected())

    def test_missing_configuration_fails_explicitly_without_radio_change(self):
        self.settings.WIFI_SSID = ""
        with self.assertRaises(OSError):
            self.wifi.connect()
        self.network.WLAN.assert_not_called()

    def test_nameserver_uses_current_dhcp_configuration(self):
        self.wifi.connect()
        self.wlan.ifconfig.return_value = ("192.0.2.1", "255.255.255.0",
                                          "192.0.2.254", "192.0.2.53")
        self.assertEqual(self.wifi.nameserver(), "192.0.2.53")


if __name__ == "__main__":
    unittest.main()
