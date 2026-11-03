from __future__ import annotations

from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .models import RequestData


def _proxy_url(request: RequestData) -> str:
    scheme = request.proxy_type.lower()
    if scheme == "socks5":
        scheme = "socks5h"

    credentials = ""
    if request.proxy_username:
        credentials = quote(request.proxy_username, safe="")
        if request.proxy_password:
            credentials += ":" + quote(request.proxy_password, safe="")
        credentials += "@"

    host = request.proxy_host.strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{credentials}{host}:{request.proxy_port}"


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def export_curl(request: RequestData) -> tuple[str, list[str]]:
    """Create a PowerShell-compatible curl.exe command and any fidelity notes."""
    url = request.url.strip()
    query = urlencode(request.params, doseq=True)
    if query:
        parts = urlsplit(url)
        separator = "" if not parts.query or parts.query.endswith("&") else "&"
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, parts.query + separator + query, parts.fragment)
        )

    options: list[tuple[str, str | None]] = [
        ("--request", request.method.upper()),
        ("--url", url),
    ]

    headers = list(request.headers)
    if request.auth_type == "Bearer Token":
        headers = [(key, value) for key, value in headers if key.lower() != "authorization"]
        headers.append(("Authorization", f"Bearer {request.bearer_token}"))
    if request.body_type == "JSON" and request.body_text:
        if not any(key.lower() == "content-type" for key, _ in headers):
            headers.append(("Content-Type", "application/json"))

    for key, value in headers:
        if key.strip():
            options.append(("--header", f"{key.strip()}: {value}"))

    if request.auth_type == "Basic Auth":
        options.append(("--user", f"{request.basic_username}:{request.basic_password}"))

    if request.body_type in {"JSON", "Raw"} and request.body_text:
        options.append(("--data-raw", request.body_text))
    elif request.body_type == "Form URL Encoded":
        for item in request.form_items:
            if item.key:
                options.append(("--data-urlencode", f"{item.key}={item.value}"))
    elif request.body_type == "multipart/form-data":
        for item in request.multipart_items:
            if not item.key:
                continue
            if item.kind == "file":
                options.append(("--form", f"{item.key}=@{item.value}"))
            else:
                options.append(("--form-string", f"{item.key}={item.value}"))

    if request.proxy_enabled and request.proxy_host.strip():
        options.append(("--proxy", _proxy_url(request)))
    if request.timeout > 0:
        options.append(("--max-time", f"{request.timeout:g}"))
    if request.follow_redirects:
        options.append(("--location", None))
    if not request.verify_ssl:
        options.append(("--insecure", None))
    if request.http_version == "1.1":
        options.append(("--http1.1", None))
    elif request.http_version == "2":
        options.append(("--http2", None))

    lines = ["curl.exe `"]
    for index, (option, value) in enumerate(options):
        line = f"  {option}"
        if value is not None:
            line += f" {_powershell_quote(value)}"
        if index < len(options) - 1:
            line += " `"
        lines.append(line)

    notes: list[str] = []
    if request.impersonate:
        notes.append(
            f"TLS 指纹 {request.impersonate} 属于 curl_cffi 功能，标准 curl 命令无法设置此指纹。"
        )
    if request.body_type == "multipart/form-data" and any(
        item.kind == "file" and item.value for item in request.multipart_items
    ):
        notes.append("multipart 文件上传使用原本地文件路径；在其他电脑执行时需要调整路径。")

    return "\n".join(lines), notes
