import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from curl_cffi import CurlECode, CurlHttpVersion, requests

from curldesk.models import FormItem, RequestData
from curldesk.transport import _proxy_url, perform_request, perform_request_async


class EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self._reply()

    def _reply(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        result = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "content_type": self.headers.get("Content-Type", ""),
                "body": body.decode("utf-8", errors="replace"),
                "authorization": self.headers.get("Authorization", ""),
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def log_message(self, format, *args):
        pass


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_get_query_and_headers(self):
        response = perform_request(
            RequestData(
                url=f"{self.base_url}/echo",
                params=[("q", "hello world"), ("q", "again")],
                headers=[("X-Test", "present")],
            )
        )
        self.assertEqual(response.status_code, 200)
        parsed = json.loads(response.body)
        self.assertIn("q=hello+world", parsed["path"])
        self.assertIn("q=again", parsed["path"])

    def test_json_form_and_raw_bodies(self):
        json_response = perform_request(
            RequestData(
                method="POST",
                url=f"{self.base_url}/echo",
                body_type="JSON",
                body_text='{"value":1}',
            )
        )
        json_echo = json.loads(json_response.body)
        self.assertEqual(json.loads(json_echo["body"]), {"value": 1})
        self.assertIn("application/json", json_echo["content_type"])

        form_response = perform_request(
            RequestData(
                method="POST",
                url=f"{self.base_url}/echo",
                body_type="Form URL Encoded",
                form_items=[FormItem("a", "two words")],
            )
        )
        form_echo = json.loads(form_response.body)
        self.assertIn("a=two+words", form_echo["body"])

        raw_response = perform_request(
            RequestData(method="POST", url=f"{self.base_url}/echo", body_type="Raw", body_text="raw text")
        )
        self.assertEqual(json.loads(raw_response.body)["body"], "raw text")

    def test_multipart_file_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "upload.txt"
            path.write_text("file contents", encoding="utf-8")
            response = perform_request(
                RequestData(
                    method="POST",
                    url=f"{self.base_url}/echo",
                    body_type="multipart/form-data",
                    multipart_items=[
                        FormItem("title", "sample"),
                        FormItem("file", str(path), "file"),
                    ],
                )
            )
        echoed = json.loads(response.body)
        self.assertIn("multipart/form-data", echoed["content_type"])
        self.assertIn("file contents", echoed["body"])

    def test_async_multipart_file_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "upload.txt"
            path.write_text("async file contents", encoding="utf-8")
            request = RequestData(
                method="POST",
                url=f"{self.base_url}/echo",
                body_type="multipart/form-data",
                multipart_items=[FormItem("file", str(path), "file")],
            )
            loop = asyncio.SelectorEventLoop()
            try:
                response = loop.run_until_complete(perform_request_async(request))
            finally:
                loop.close()
        self.assertIn("async file contents", json.loads(response.body)["body"])

    def test_tls_proxy_and_auth_options_are_forwarded(self):
        class FakeResponse:
            status_code = 200
            reason = "OK"
            headers = {"Content-Type": "application/json"}
            content = b"{}"
            url = "https://example.test/"

        class FakeSession:
            def __init__(self):
                self.call = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def request(self, method, url, **kwargs):
                self.call = (method, url, kwargs)
                return FakeResponse()

        fake_session = FakeSession()
        with patch("curldesk.transport.requests.Session", return_value=fake_session) as session_factory:
            response = perform_request(
                RequestData(
                    method="GET",
                    url="https://example.test/",
                    auth_type="Basic Auth",
                    basic_username="user",
                    basic_password="pass",
                    proxy_enabled=True,
                    proxy_type="http",
                    proxy_host="proxy.test",
                    proxy_port=8080,
                    proxy_username="a@b",
                    proxy_password="p:/s",
                    impersonate="chrome",
                    verify_ssl=False,
                    timeout=7.5,
                )
            )
        session_factory.assert_called_once_with(impersonate="chrome")
        method, _, kwargs = fake_session.call
        self.assertEqual(method, "GET")
        self.assertEqual(kwargs["auth"], ("user", "pass"))
        self.assertFalse(kwargs["verify"])
        self.assertEqual(kwargs["timeout"], 7.5)
        self.assertEqual(
            kwargs["proxy"], "http://a%40b:p%3A%2Fs@proxy.test:8080"
        )
        self.assertEqual(response.status_code, 200)

    def test_proxy_url_ipv6(self):
        request = RequestData(
            proxy_type="socks5",
            proxy_host="::1",
            proxy_port=9050,
            proxy_username="user",
            proxy_password="p@ss",
        )
        self.assertEqual(_proxy_url(request), "socks5h://user:p%40ss@[::1]:9050")

    def test_retry_read_only_after_connection_closed(self):
        class FakeResponse:
            status_code, reason, headers, content, url = 200, "OK", {}, b"ok", "https://example.test/"

        class FakeSession:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def request(self, method, url, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise requests.exceptions.ConnectionError("closed", CurlECode.RECV_ERROR)
                return FakeResponse()

        session = FakeSession()
        with patch("curldesk.transport.requests.Session", return_value=session):
            response = perform_request(RequestData(url="https://example.test/", http_version="1.1"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[1]["http_version"], CurlHttpVersion.V1_1)
        self.assertLessEqual(session.calls[1]["timeout"], 30)

        session = FakeSession()
        with patch("curldesk.transport.requests.Session", return_value=session):
            with self.assertRaises(requests.exceptions.ConnectionError):
                perform_request(RequestData(method="POST", url="https://example.test/"))
        self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
