from __future__ import annotations

import json
import time
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import quote

from curl_cffi import CurlECode, CurlHttpVersion, CurlMime, requests

from .models import FormItem, RequestData, ResponseData


def _headers(rows: list[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in rows:
        name = key.strip()
        if not name:
            continue
        if name.lower() == "cookie":
            existing_name = next(
                (header_name for header_name in result if header_name.lower() == "cookie"),
                None,
            )
            if existing_name is not None:
                cookie_parts = [
                    part.strip(" ;")
                    for part in (result[existing_name], value)
                    if part.strip(" ;")
                ]
                result[existing_name] = "; ".join(cookie_parts)
                continue
        result[name] = value
    return result


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


def _multipart(request: RequestData) -> CurlMime:
    multipart = CurlMime()
    try:
        for item in request.multipart_items:
            if not item.key:
                continue
            if item.kind == "file":
                path = Path(item.value).expanduser()
                if not path.is_file():
                    raise FileNotFoundError(f"Multipart 文件不存在: {path}")
                content_type = guess_type(path.name)[0] or "application/octet-stream"
                multipart.addpart(
                    name=item.key,
                    local_path=path,
                    filename=path.name,
                    content_type=content_type,
                )
            else:
                multipart.addpart(name=item.key, data=item.value.encode("utf-8"))
    except Exception:
        multipart.close()
        raise
    return multipart


def _prepare_request(request: RequestData) -> tuple[dict[str, object], CurlMime | None]:
    if not request.url.strip():
        raise ValueError("请输入请求 URL")

    headers = _headers(request.headers)
    auth = None
    if request.auth_type == "Bearer Token":
        headers["Authorization"] = f"Bearer {request.bearer_token}"
    elif request.auth_type == "Basic Auth":
        auth = (request.basic_username, request.basic_password)

    options: dict[str, object] = {
        "params": request.params,
        "headers": headers,
        "timeout": request.timeout,
        "allow_redirects": request.follow_redirects,
        "verify": request.verify_ssl,
    }
    if request.http_version == "1.1":
        options["http_version"] = CurlHttpVersion.V1_1
    elif request.http_version == "2":
        options["http_version"] = CurlHttpVersion.V2_0
    if auth is not None:
        options["auth"] = auth
    if request.proxy_enabled and request.proxy_host.strip():
        proxy = _proxy_url(request)
        options["proxy"] = proxy

    multipart: CurlMime | None = None
    if request.body_type == "JSON" and request.body_text:
        options["json"] = json.loads(request.body_text)
    elif request.body_type == "Form URL Encoded":
        options["data"] = request.form_items and [
            (item.key, item.value) for item in request.form_items if item.key
        ] or []
    elif request.body_type == "multipart/form-data":
        multipart = _multipart(request)
        options["multipart"] = multipart
    elif request.body_type == "Raw" and request.body_text:
        options["data"] = request.body_text.encode("utf-8")

    return options, multipart


def _response_data(response: object, start: float) -> ResponseData:
    elapsed = (time.perf_counter() - start) * 1000
    response_headers = [(str(key), str(value)) for key, value in response.headers.items()]
    return ResponseData(
        status_code=response.status_code,
        reason=response.reason or "",
        elapsed_ms=elapsed,
        headers=response_headers,
        body=response.content,
        final_url=str(response.url),
    )


def _retryable(request: RequestData, error: Exception, start: float) -> bool:
    return (
        request.method.upper() in {"GET", "HEAD"}
        and request.body_type == "None"
        and isinstance(error, requests.exceptions.RequestException)
        and error.code == CurlECode.RECV_ERROR
        and time.perf_counter() - start < request.timeout
    )


def perform_request(request: RequestData) -> ResponseData:
    options, multipart = _prepare_request(request)
    start = time.perf_counter()
    try:
        with requests.Session(impersonate=request.impersonate or None) as session:
            try:
                response = session.request(request.method.upper(), request.url.strip(), **options)
            except requests.exceptions.RequestException as exc:
                if not _retryable(request, exc, start):
                    raise
                options["timeout"] = max(0.001, request.timeout - (time.perf_counter() - start))
                response = session.request(request.method.upper(), request.url.strip(), **options)
        return _response_data(response, start)
    finally:
        if multipart is not None:
            multipart.close()


async def perform_request_async(request: RequestData) -> ResponseData:
    options, multipart = _prepare_request(request)
    start = time.perf_counter()
    try:
        async with requests.AsyncSession(impersonate=request.impersonate or None) as session:
            try:
                response = await session.request(request.method.upper(), request.url.strip(), **options)
            except requests.exceptions.RequestException as exc:
                if not _retryable(request, exc, start):
                    raise
                options["timeout"] = max(0.001, request.timeout - (time.perf_counter() - start))
                response = await session.request(request.method.upper(), request.url.strip(), **options)
        return _response_data(response, start)
    finally:
        if multipart is not None:
            multipart.close()
