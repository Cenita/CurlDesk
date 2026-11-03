import unittest

from curldesk.import_curl import CurlImportError, parse_curl


class CurlImportTests(unittest.TestCase):
    def test_get_command_parses_url_query_headers_and_redirect(self):
        request, warnings = parse_curl(
            "curl 'https://api.example.test/items?old=1' -H 'X-Test: yes' -L"
        )
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url, "https://api.example.test/items")
        self.assertEqual(request.params, [("old", "1")])
        self.assertEqual(request.headers, [("X-Test", "yes")])
        self.assertTrue(request.follow_redirects)
        self.assertEqual(warnings, [])

    def test_json_post_and_bearer_header(self):
        request, warnings = parse_curl(
            "curl.exe -X POST https://api.example.test -H 'Content-Type: application/json' "
            "-H 'Authorization: Bearer token-123' --data-raw '{\"name\":\"test\"}'"
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.body_type, "JSON")
        self.assertEqual(request.body_text, '{"name":"test"}')
        self.assertIn(("Authorization", "Bearer token-123"), request.headers)
        self.assertEqual(warnings, [])

    def test_basic_auth_cookie_and_proxy_credentials(self):
        request, warnings = parse_curl(
            "curl https://example.test -u 'api:user:pass' -b 'a=1; b=2' "
            "-x 'socks5h://proxy:secret@127.0.0.1:9050'"
        )
        self.assertEqual(request.auth_type, "Basic Auth")
        self.assertEqual(request.basic_username, "api")
        self.assertEqual(request.basic_password, "user:pass")
        self.assertIn(("Cookie", "a=1; b=2"), request.headers)
        self.assertTrue(request.proxy_enabled)
        self.assertEqual(request.proxy_type, "socks5")
        self.assertEqual(request.proxy_port, 9050)
        self.assertEqual(request.proxy_username, "proxy")
        self.assertEqual(request.proxy_password, "secret")
        self.assertEqual(warnings, [])

    def test_form_and_get_data(self):
        form, _ = parse_curl("curl https://example.test -d 'q=hello+world&n=2'")
        self.assertEqual(form.method, "POST")
        self.assertEqual(form.body_type, "Form URL Encoded")
        self.assertEqual([(item.key, item.value) for item in form.form_items], [("q", "hello world"), ("n", "2")])

        get_request, _ = parse_curl("curl -G https://example.test -d 'q=hello+world'")
        self.assertEqual(get_request.method, "GET")
        self.assertEqual(get_request.params, [("q", "hello world")])
        self.assertEqual(get_request.body_type, "None")

    def test_multipart_file_and_insecure_flag(self):
        request, _ = parse_curl(
            "curl https://example.test -F 'title=hello' -F 'file=@C:\\temp\\a.txt' -k"
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.body_type, "multipart/form-data")
        self.assertEqual(request.multipart_items[0].value, "hello")
        self.assertEqual(request.multipart_items[1].value, "C:\\temp\\a.txt")
        self.assertEqual(request.multipart_items[1].kind, "file")
        self.assertFalse(request.verify_ssl)

    def test_powershell_continuation_and_unsupported_option_warning(self):
        request, warnings = parse_curl(
            "curl.exe --url 'https://example.test' `\n"
            "  -H 'X-Test: one' `\n"
            "  --connect-timeout 3 --http2"
        )
        self.assertEqual(request.url, "https://example.test")
        self.assertEqual(request.headers, [("X-Test", "one")])
        self.assertEqual(request.http_version, "2")
        self.assertTrue(any("connect-timeout" in item for item in warnings))
        self.assertFalse(any("--http2" in item for item in warnings))

    def test_missing_url_and_unclosed_quote_report_errors(self):
        with self.assertRaises(CurlImportError):
            parse_curl("curl -H 'Accept: application/json'")
        with self.assertRaises(CurlImportError):
            parse_curl("curl 'https://example.test")


if __name__ == "__main__":
    unittest.main()
