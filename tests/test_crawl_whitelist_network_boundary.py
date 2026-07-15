import socket
import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import crawl_whitelist
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler


class NetworkBoundaryTest(unittest.TestCase):
    @staticmethod
    def certificate_error(code, message):
        error = ssl.SSLCertVerificationError(1, message)
        error.verify_code = code
        error.verify_message = message
        return URLError(error)

    def test_rejects_non_public_and_metadata_addresses(self):
        blocked = [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "100.64.0.1",
            "100.100.100.200",
            "::1",
            "fc00::1",
            "fe80::1",
            "fec0::1",
            "::ffff:127.0.0.1",
            "224.0.0.1",
            "239.255.255.250",
            "ff02::1",
        ]
        for address in blocked:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            answer = [(family, socket.SOCK_STREAM, 6, "", (address, 443))]
            with self.subTest(address=address), patch("crawl_whitelist.socket.getaddrinfo", return_value=answer):
                with self.assertRaises(ValueError):
                    crawl_whitelist.validate_public_target("https://allowed.example/x", "allowed.example")

    def test_rejects_dns_answer_when_any_address_is_not_public(self):
        answer = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        ]
        with patch("crawl_whitelist.socket.getaddrinfo", return_value=answer):
            with self.assertRaises(ValueError):
                crawl_whitelist.validate_public_target("https://allowed.example/x", "allowed.example")

    def test_redirect_rejects_external_location_before_following(self):
        handler = crawl_whitelist.SafeRedirectHandler("allowed.example")
        request = MagicMock(full_url="https://allowed.example/start")
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/private")

    def test_redirect_validates_relative_location_on_every_hop(self):
        handler = crawl_whitelist.SafeRedirectHandler("allowed.example")
        request = MagicMock(full_url="https://allowed.example/start")
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with (
            patch("crawl_whitelist.socket.getaddrinfo", return_value=public),
            patch.object(HTTPRedirectHandler, "redirect_request", return_value="next") as parent,
        ):
            result = handler.redirect_request(request, None, 302, "Found", {}, "/next")
        self.assertEqual("next", result)
        self.assertEqual("https://allowed.example/next", parent.call_args.args[-1])

    def test_connection_revalidates_and_rejects_rebound_private_answer(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with (
            patch("crawl_whitelist.socket.getaddrinfo", side_effect=[public, private]),
            patch("crawl_whitelist.socket.socket") as socket_factory,
        ):
            crawl_whitelist.validate_public_target("https://allowed.example/start", "allowed.example")
            with self.assertRaises(ValueError):
                crawl_whitelist._create_public_connection("allowed.example", 443, 15)
        socket_factory.assert_not_called()

    def test_safe_open_closes_response_when_final_url_is_rejected(self):
        response = MagicMock()
        response.geturl.return_value = "https://evil.example/private"
        opener = MagicMock()
        opener.open.return_value = response
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with (
            patch("crawl_whitelist.socket.getaddrinfo", return_value=public),
            patch("crawl_whitelist.build_opener", return_value=opener) as build,
        ):
            with self.assertRaises(ValueError):
                crawl_whitelist.safe_open("https://allowed.example/start", "allowed.example")
        response.close.assert_called_once_with()
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(item, crawl_whitelist.ProxyHandler) for item in handlers))
        self.assertTrue(any(isinstance(item, crawl_whitelist.PublicHTTPHandler) for item in handlers))
        self.assertTrue(any(isinstance(item, crawl_whitelist.PublicHTTPSHandler) for item in handlers))

    def test_https_wraps_pinned_socket_with_original_hostname(self):
        context = MagicMock()
        pinned_socket = MagicMock()
        connection = crawl_whitelist.PublicHTTPSConnection("allowed.example", context=context)
        with patch("crawl_whitelist._create_public_connection", return_value=pinned_socket):
            connection.connect()
        context.wrap_socket.assert_called_once_with(pinned_socket, server_hostname="allowed.example")

    def test_robots_uses_safe_request_chain(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"User-agent: *\nDisallow: /private\n"
        with patch("crawl_whitelist.safe_open", return_value=response) as safe_open:
            allowed = crawl_whitelist.can_fetch("https://allowed.example/private", "crawler")
        self.assertFalse(allowed)
        safe_open.assert_called_once_with("https://allowed.example/robots.txt", "allowed.example")

    def test_robots_security_failure_is_fail_closed(self):
        with patch("crawl_whitelist.safe_open", side_effect=crawl_whitelist.UnsafeTargetError("blocked")):
            self.assertFalse(crawl_whitelist.can_fetch("https://allowed.example/private", "crawler"))

    def test_robots_http_error_is_closed_before_fail_open(self):
        error = HTTPError("https://allowed.example/robots.txt", 404, "Not Found", {}, None)
        with (
            patch.object(error, "close", wraps=error.close) as close,
            patch("crawl_whitelist.safe_open", side_effect=error),
        ):
            self.assertTrue(crawl_whitelist.can_fetch("https://allowed.example/public", "crawler"))
        close.assert_called_once_with()

    def test_robots_errors_fail_closed_except_missing_policy(self):
        for code, expected in [(401, False), (403, False), (404, True), (410, True), (429, False), (500, False), (503, False)]:
            error = HTTPError("https://allowed.example/robots.txt", code, "error", {}, None)
            with patch("crawl_whitelist.safe_open", side_effect=error):
                with self.subTest(code=code):
                    self.assertEqual(expected, crawl_whitelist.can_fetch("https://allowed.example/public", "crawler"))
        for error in [URLError("offline"), TimeoutError("timeout"), OSError("socket")]:
            with patch("crawl_whitelist.safe_open", side_effect=error):
                with self.subTest(error=type(error).__name__):
                    self.assertFalse(crawl_whitelist.can_fetch("https://allowed.example/public", "crawler"))

    def test_known_qianzhan_chain_failure_is_skipped_with_known_limit(self):
        site = crawl_whitelist.Site("前瞻网", "https://www.qianzhan.com", "qianzhan.com", "报告", "page", "yes", 5, "")
        error = self.certificate_error(20, "unable to get local issuer certificate")
        with (
            patch("crawl_whitelist.can_fetch", return_value=True),
            patch("crawl_whitelist.fetch_html", side_effect=error) as fetch,
            patch("crawl_whitelist.extract_page") as extract,
            patch("crawl_whitelist.ssl._create_unverified_context") as insecure_context,
        ):
            row = crawl_whitelist.crawl_url(site, "空调", ["空调"], "https://www.qianzhan.com/report/1", "detail")
        self.assertEqual("skipped", row["status"])
        self.assertIn("TLS证书链不可用", row["known_limit"])
        self.assertEqual("", row["content"])
        fetch.assert_called_once()
        extract.assert_not_called()
        insecure_context.assert_not_called()

    def test_dangerous_certificate_error_is_failed_not_known_limit(self):
        site = crawl_whitelist.Site("前瞻网", "https://www.qianzhan.com", "qianzhan.com", "报告", "page", "yes", 5, "")
        error = self.certificate_error(62, "hostname mismatch")
        with patch("crawl_whitelist.can_fetch", return_value=True), patch("crawl_whitelist.fetch_html", side_effect=error):
            row = crawl_whitelist.crawl_url(site, "空调", ["空调"], "https://www.qianzhan.com/report/1", "detail")
        self.assertEqual("failed", row["status"])
        self.assertEqual("", row["known_limit"])
        self.assertEqual("yes", row["review_required"])

    def test_chain_failure_on_unregistered_domain_is_not_known_limit(self):
        site = crawl_whitelist.Site("测试站", "https://example.com", "example.com", "报告", "page", "yes", 5, "")
        error = self.certificate_error(20, "unable to get local issuer certificate")
        with patch("crawl_whitelist.can_fetch", return_value=True), patch("crawl_whitelist.fetch_html", side_effect=error):
            row = crawl_whitelist.crawl_url(site, "空调", ["空调"], "https://example.com/report/1", "detail")
        self.assertEqual("failed", row["status"])
        self.assertEqual("", row["known_limit"])
        self.assertEqual("yes", row["review_required"])

    def test_tls_known_limit_requires_exact_domain_and_explicit_chain_code(self):
        cases = [
            ("qianzhan.com", 21, "unable to verify the first certificate", "skipped"),
            ("sub.qianzhan.com", 20, "unable to get local issuer certificate", "skipped"),
            ("evilqianzhan.com", 20, "unable to get local issuer certificate", "failed"),
            ("qianzhan.com", 10, "certificate has expired", "failed"),
            ("qianzhan.com", 18, "self signed certificate", "failed"),
            ("qianzhan.com", 23, "certificate revoked", "failed"),
            ("qianzhan.com", 62, "hostname mismatch", "failed"),
            ("qianzhan.com", 999, "unknown", "failed"),
            ("qianzhan.com", None, "unable to get local issuer certificate", "failed"),
        ]
        for domain, code, message, expected_status in cases:
            site = crawl_whitelist.Site("测试站", f"https://{domain}", domain, "报告", "page", "yes", 5, "")
            error = self.certificate_error(code, message)
            with (
                self.subTest(domain=domain, code=code),
                patch("crawl_whitelist.can_fetch", return_value=True),
                patch("crawl_whitelist.fetch_html", side_effect=error) as fetch,
            ):
                row = crawl_whitelist.crawl_url(site, "空调", ["空调"], f"https://{domain}/report/1", "detail")
            self.assertEqual(expected_status, row["status"])
            self.assertEqual(expected_status == "skipped", bool(row["known_limit"]))
            fetch.assert_called_once()

    def test_generic_network_error_is_not_mislabeled_as_tls_limit(self):
        site = crawl_whitelist.Site("前瞻网", "https://www.qianzhan.com", "qianzhan.com", "报告", "page", "yes", 5, "")
        with patch("crawl_whitelist.can_fetch", return_value=True), patch("crawl_whitelist.fetch_html", side_effect=URLError("timeout")):
            row = crawl_whitelist.crawl_url(site, "空调", ["空调"], "https://www.qianzhan.com/report/1", "detail")
        self.assertEqual("failed", row["status"])
        self.assertEqual("", row["known_limit"])

    def test_robots_tls_failure_reaches_the_same_tls_classifier(self):
        site = crawl_whitelist.Site("前瞻网", "https://www.qianzhan.com", "qianzhan.com", "报告", "page", "yes", 5, "")
        cases = [
            (self.certificate_error(20, "unable to get local issuer certificate"), "skipped", True, ""),
            (self.certificate_error(62, "hostname mismatch"), "failed", False, "yes"),
            (URLError("offline"), "skipped", False, ""),
        ]
        for error, expected_status, has_limit, review in cases:
            with (
                self.subTest(error=repr(error)),
                patch("crawl_whitelist.safe_open", side_effect=error),
                patch("crawl_whitelist.fetch_html") as fetch,
            ):
                row = crawl_whitelist.crawl_url(site, "空调", ["空调"], "https://www.qianzhan.com/report/1", "detail")
            self.assertEqual(expected_status, row["status"])
            self.assertEqual(has_limit, bool(row["known_limit"]))
            self.assertEqual(review, row["review_required"])
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
