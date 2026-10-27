import platform
import socket
import subprocess
import unittest
from unittest.mock import Mock, patch

from icmplib import NameLookupError, SocketPermissionError
from monitor.ping import PingError, ping
from monitor.proxmox import collect

TARGET = {"url": "https://192.0.2.1:8006", "node": "pve-01"}


class PingTests(unittest.TestCase):
    def test_linux_uses_unprivileged_echo_and_never_starts_executable(self):
        with patch("monitor.ping.platform.system", return_value="Linux"), \
                patch("monitor.ping.icmp_ping", return_value=Mock(is_alive=True, avg_rtt=24.125)) as probe, \
                patch("monitor.ping.subprocess.run") as executable:
            self.assertEqual(ping(TARGET), 24.125)
            probe.assert_called_once_with("192.0.2.1", count=1, interval=0, timeout=2, privileged=False)
            executable.assert_not_called()

    def test_linux_no_reply_is_distinct_from_local_probe_errors(self):
        with patch("monitor.ping.platform.system", return_value="Linux"):
            with patch("monitor.ping.icmp_ping", return_value=Mock(is_alive=False)):
                self.assertIsNone(ping(TARGET))
            for error, message in ((SocketPermissionError(privileged=False), "unprivileged ICMP"), (NameLookupError("test"), "lookup failed"), (OSError(), "could not send")):
                with patch("monitor.ping.icmp_ping", side_effect=error):
                    with self.assertRaisesRegex(PingError, message):
                        ping(TARGET)

    def test_windows_errors_and_rtt_formats(self):
        with patch("monitor.ping.platform.system", return_value="Windows"):
            for error in (FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired("ping", 3)):
                with patch("monitor.ping.subprocess.run", side_effect=error):
                    with self.assertRaises(PingError):
                        ping(TARGET)
            for output, expected in (("time<1ms TTL=64", 1), ("temps=12,5 ms TTL=64", 12.5), ("时间=24毫秒 TTL=64", 24)):
                with patch("monitor.ping.subprocess.run", return_value=Mock(returncode=0, stdout=output)):
                    self.assertEqual(ping(TARGET), expected)
            with patch("monitor.ping.subprocess.run", return_value=Mock(returncode=2, stdout="")):
                with self.assertRaises(PingError):
                    ping(TARGET)

    def test_windows_only_passes_resolved_numeric_destination(self):
        with patch("monitor.ping.platform.system", return_value="Windows"), \
                patch("monitor.ping.socket.getaddrinfo", return_value=[(socket.AF_INET6, socket.SOCK_DGRAM, 0, "", ("::1", 0, 0, 0))]), \
                patch("monitor.ping.subprocess.run", return_value=Mock(returncode=0, stdout="time=1ms")) as run:
            self.assertEqual(ping(dict(TARGET, url="https://pve.example.net")), 1)
            self.assertEqual(run.call_args.args[0][-1], "::1")
        with patch("monitor.ping.platform.system", return_value="Windows"), \
                patch("monitor.ping.socket.getaddrinfo", side_effect=socket.gaierror()):
            with self.assertRaisesRegex(PingError, "lookup failed"):
                ping(dict(TARGET, url="https://pve.example.net"))

    def test_local_ping_error_does_not_hide_cpu_ram_or_fail_api_checks(self):
        def reply(_, endpoint):
            return {"cpu": .1, "memory": {"total": 100, "used": 25}} if endpoint.endswith("/status") else []
        with patch("monitor.proxmox.get", side_effect=reply), \
                patch("monitor.proxmox.ping", side_effect=PingError("ICMP unavailable on monitor")):
            result = collect(TARGET)
        self.assertEqual(result["cpu"]["usage"], .1)
        self.assertEqual(result["memory"]["total"], 100)
        self.assertEqual(result["errors"], {})
        self.assertIsNone(result["ping_ms"])
        self.assertEqual(result["ping_error"], "ICMP unavailable on monitor")

    @unittest.skipUnless(platform.system() == "Linux", "Linux unprivileged ICMP integration")
    def test_real_linux_loopback_ipv4_and_ipv6(self):
        for address in ("127.0.0.1", "[::1]"):
            value = ping({"url": "https://" + address})
            self.assertIsInstance(value, (int, float))
            self.assertGreaterEqual(value, 0)


if __name__ == "__main__":
    unittest.main()
