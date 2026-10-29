"""Cooperative device HTTPS, using the firmware's bounded HTTP parser."""
import errno
import json
import struct
import sys

HOST = "badgecluster-37nlkk2a4ucny.azurewebsites.net"
PATH = "/api/v1/contributions"
CA_FILE = "assets/digicert_global_root_g2.pem"
NTP_SERVER = "162.159.200.1"
MAX_RESPONSE = 8192
DNS_LIMIT = 2048
WIFI_JOIN_MS = 15000
PENDING = (errno.EAGAIN, errno.EINPROGRESS)


def dns_query(host, token):
    question = b"".join(bytes((len(label),)) + label.encode() for label in host.split("."))
    return token + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + question + b"\x00\x00\x01\x00\x01"


def _skip_name(packet, offset):
    for _ in range(128):
        if offset >= len(packet):
            raise ValueError("Truncated DNS name")
        length = packet[offset]
        offset += 1
        if length == 0:
            return offset
        if length & 0xC0 == 0xC0:
            if offset >= len(packet):
                raise ValueError("Truncated DNS pointer")
            return offset + 1
        if length > 63 or offset + length > len(packet):
            raise ValueError("Invalid DNS name")
        offset += length
    raise ValueError("DNS name too long")


def dns_address(packet, query):
    if len(packet) < 12:
        raise ValueError("Truncated DNS response")
    if packet[:2] != query[:2]:
        return None
    flags, questions, answers = struct.unpack(">HHH", packet[2:8])
    if flags & 0xFA0F != 0x8000 or questions != 1:
        raise ValueError("DNS resolution failed")
    offset = _skip_name(packet, 12) + 4
    if packet[12:offset] != query[12:]:
        raise ValueError("DNS question mismatch")
    for _ in range(answers):
        offset = _skip_name(packet, offset)
        if offset + 10 > len(packet):
            raise ValueError("Truncated DNS answer")
        kind, family, _, length = struct.unpack(">HHIH", packet[offset:offset + 10])
        offset += 10
        if offset + length > len(packet):
            raise ValueError("Truncated DNS address")
        if kind == 1 and family == 1 and length == 4:
            return ".".join(str(value) for value in packet[offset:offset + 4])
        offset += length
    raise ValueError("No IPv4 address in DNS response")


def ntp_seconds(packet, nonce):
    if len(packet) < 48 or packet[24:32] != nonce:
        raise ValueError("Invalid NTP response")
    if packet[0] >> 6 == 3 or packet[0] & 7 != 4 or not 1 <= packet[1] <= 15:
        raise ValueError("Unsynchronised NTP server")
    seconds = struct.unpack(">I", packet[40:44])[0] - 2208988800
    if not 1767225600 <= seconds < 2145916800:
        raise ValueError("NTP time outside supported 2026-2037 range")
    return seconds


def verified_client(fetch, socket, context):
    class VerifiedFetch(fetch.AsyncFetch):
        def _connect(self):
            # DNS is resolved separately over nonblocking UDP, never getaddrinfo.
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            self._sock = sock
            try:
                try:
                    sock.connect(self.address)
                except OSError as error:
                    if error.args[0] not in PENDING:
                        raise
                self._sock = context.wrap_socket(
                    sock, server_hostname=HOST, do_handshake_on_connect=False)
            except OSError:
                self._close_socket()
                raise
            yield

        def _read_line(self):
            line = b""
            while True:
                part = self._sock.read(1)
                if part == b"":
                    raise OSError("Connection closed mid-line")
                if part:
                    line += part
                    if len(line) > 2048:
                        raise ValueError("HTTP line too long")
                    if line.endswith(b"\n"):
                        return line
                else:
                    self._check_deadline()
                    yield
                    continue
                if self._budget_spend(1):
                    yield
                    self._budget_reset()

    return VerifiedFetch


class WifiConnection:
    """Own association retries without the firmware helper's latched failure."""
    def __init__(self, network, secrets):
        self.network, self.secrets = network, secrets
        self.wlan = None

    def is_connected(self):
        return self.wlan is not None and self.wlan.isconnected()

    def connect(self):
        if self.wlan is None:
            ssid = getattr(self.secrets, "WIFI_SSID", "")
            password = getattr(self.secrets, "WIFI_PASSWORD", "")
            if not ssid:
                raise OSError("Wi-Fi network is not configured")
            self.wlan = self.network.WLAN(self.network.STA_IF)
            self.wlan.active(True)
            if not self.wlan.isconnected():
                self.wlan.connect(ssid, password)
        return self.wlan.isconnected()

    def failure(self):
        return self.wlan is not None and self.wlan.status() < 0

    def disconnect(self):
        if self.wlan is not None:
            self.wlan.disconnect()
            self.wlan.active(False)
            self.wlan = None

    def nameserver(self):
        return self.wlan.ifconfig()[3]


class DeviceTransport:
    def __init__(self, wifi, socket, clock, machine, random_bytes, client_type):
        self.wifi, self.socket, self.clock = wifi, socket, clock
        self.machine, self.random_bytes = machine, random_bytes
        self.client_type = client_type
        self.udp = None
        self.http = None
        self.phase = None
        self.payload = None
        self.time_ready = False
        self.wifi_deadline = None

    def prepare(self):
        if self.wifi.is_connected():
            self.wifi_deadline = None
            return True
        now = self.clock.ticks_ms()
        if self.wifi_deadline is None:
            self.wifi_deadline = self.clock.ticks_add(now, WIFI_JOIN_MS)
        if self.wifi.failure() or self.clock.ticks_diff(now, self.wifi_deadline) >= 0:
            self._wifi_failed()
        connected = self.wifi.connect()
        if connected:
            self.wifi_deadline = None
        elif self.wifi.failure():
            self._wifi_failed()
        return connected

    def _wifi_failed(self):
        # Drop the failed driver association so the next attempt really reconnects.
        print("BadgeCluster: Wi-Fi association failed; restarting after backoff.")
        self.wifi.disconnect()
        self.wifi_deadline = None
        raise OSError("Wi-Fi association failed")

    def start(self, payload):
        if self.phase is not None:
            raise OSError("Request already running")
        self.payload = json.dumps(payload).encode()
        self.phase = "wifi"

    def _query(self, address, data):
        self.udp = self.socket.socket(self.socket.AF_INET, self.socket.SOCK_DGRAM)
        self.udp.setblocking(False)
        self.udp.connect(address)
        self.udp.send(data)

    def _receive(self, maximum):
        try:
            return self.udp.recv(maximum)
        except OSError as error:
            if error.args[0] == errno.EAGAIN:
                return None
            raise

    def _close_udp(self):
        if self.udp is not None:
            self.udp.close()
            self.udp = None

    def poll(self):
        if self.phase == "wifi":
            if not self.prepare():
                return None
            if not self.time_ready:
                self.nonce = self.random_bytes(8)
                self._query((NTP_SERVER, 123), b"\x23" + bytes(39) + self.nonce)
                self.phase = "time"
                return None
            self.phase = "resolve"
        if self.phase == "time":
            packet = self._receive(512)
            if packet is None:
                return None
            seconds = ntp_seconds(packet, self.nonce)
            # MicroPython ports may use either a 1970 or a 2000 epoch.
            epoch_offset = 946684800 if self.clock.gmtime(0)[0] == 2000 else 0
            year, month, day, hour, minute, second, weekday = self.clock.gmtime(seconds - epoch_offset)[:7]
            self.machine.RTC().datetime((year, month, day, weekday, hour, minute, second, 0))
            self.time_ready = True
            self._close_udp()
            self.phase = "resolve"
            return None
        if self.phase == "resolve":
            self.query = dns_query(HOST, self.random_bytes(2))
            self._query((self.wifi.nameserver(), 53), self.query)
            self.phase = "dns"
            return None
        if self.phase == "dns":
            packet = self._receive(DNS_LIMIT)
            if packet is None:
                return None
            address = dns_address(packet, self.query)
            if address is None:
                return None
            self._close_udp()
            self.http = self.client_type(HOST, max_bytes=MAX_RESPONSE, timeout=4, step_bytes=512)
            self.http.address = (address, 443)
            self.http.fetch(PATH, method="POST", data=self.payload,
                            headers={"Content-Type": "application/json",
                                     "User-Agent": "BadgeCluster/1"})
            self.phase = "http"
            return None
        if self.phase == "http":
            self.http.update()
            if not self.http.done:
                return None
            response = self.http.to_json()
            self.cancel()
            return ("ok", response)
        return None

    def cancel(self):
        self._close_udp()
        if self.http is not None:
            self.http.reset()
            self.http = None
        self.phase = None
        self.payload = None
        self.wifi_deadline = None


def create_transport():
    if sys.platform != "rp2":
        print("BadgeCluster: device networking unavailable; simulator is offline.")
        return None
    import binascii
    import fetch
    import machine
    import os
    import socket
    import time
    import tls
    import wifi

    with open(CA_FILE) as source:
        certificate = "".join(line.strip() for line in source if not line.startswith("-----"))
    context = tls.SSLContext(tls.PROTOCOL_TLS_CLIENT)
    context.verify_mode = tls.CERT_REQUIRED
    context.load_verify_locations(binascii.a2b_base64(certificate))
    client_type = verified_client(fetch, socket, context)
    return DeviceTransport(WifiConnection(wifi.network, wifi.secrets),
                           socket, time, machine, os.urandom, client_type)
