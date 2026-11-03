from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from .models import FormItem, RequestData


class CurlImportError(ValueError):
    pass


def _tokens(command: str) -> list[str]:
    command = re.sub(r"(?:\\|`|\^)\r?\n\s*", " ", command)
    result: list[str] = []
    token: list[str] = []
    quote = ""
    index = 0
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == "'":
                if index + 1 < len(command) and command[index + 1] == "'":
                    token.append("'")
                    index += 1
                else:
                    quote = ""
            else:
                token.append(char)
        elif quote == '"':
            if char == '"':
                quote = ""
            elif char == "`" and index + 1 < len(command):
                index += 1
                token.append(command[index])
            elif char == "\\" and index + 1 < len(command) and command[index + 1] in ('"', "\\"):
                index += 1
                token.append(command[index])
            else:
                token.append(char)
        elif char in ("'", '"'):
            quote = char
        elif char.isspace():
            if token:
                result.append("".join(token))
                token.clear()
        elif char == "`" and index + 1 < len(command) and command[index + 1].isspace():
            index += 1
        else:
            token.append(char)
        index += 1
    if quote:
        raise CurlImportError("curl 命令中有未闭合的引号")
    if token:
        result.append("".join(token))
    return result


def _split_option(token: str) -> tuple[str, str | None]:
    if token.startswith("--") and "=" in token:
        return tuple(token.split("=", 1))  # type: ignore[return-value]
    short_with_value = "XHduAFbexm"
    if len(token) > 2 and token.startswith("-") and not token.startswith("--"):
        if token[1] in short_with_value:
            return "-" + token[1], token[2:]
    return token, None


def parse_curl(command: str) -> tuple[RequestData, list[str]]:
    tokens = _tokens(command.strip())
    if not tokens:
        raise CurlImportError("请粘贴 curl 命令")
    if tokens[0].lower().split("\\")[-1] in {"curl", "curl.exe"}:
        tokens.pop(0)
    if not tokens:
        raise CurlImportError("curl 命令中没有请求 URL")

    warnings: list[str] = []
    headers: list[tuple[str, str]] = []
    raw_data: list[str] = []
    encoded_data: list[str] = []
    multipart: list[FormItem] = []
    url = ""
    method = ""
    basic_auth: tuple[str, str] | None = None
    cookie_values: list[str] = []
    proxy_value = ""
    proxy_user = ""
    timeout = 30.0
    verify_ssl = True
    follow_redirects = False
    http_version = "auto"
    use_get_data = False
    head_request = False
    ignored_flags = {"-s", "-S", "--silent", "--show-error", "--compressed", "--no-progress-meter"}
    no_value_flags = {
        "-L", "--location", "-G", "--get", "-I", "--head", "-k", "--insecure",
        "-i", "--include", "--http1.1", "--http2",
    }
    value_options = {
        "-X", "--request", "-H", "--header", "-d", "--data", "--data-raw",
        "--data-ascii", "--data-binary", "--data-urlencode", "-F", "--form", "--form-string",
        "-u", "--user", "-b", "--cookie", "-A", "--user-agent", "-e",
        "--referer", "--url", "-x", "--proxy", "--proxy-user", "-m",
        "--max-time", "--connect-timeout", "--max-redirs", "-w", "--write-out",
        "-o", "--output", "--cacert", "--cert", "--key", "--resolve",
        "--url-query", "--interface", "--limit-rate", "--retry", "--retry-delay",
    }
    index = 0

    def value_for(option: str, inline: str | None) -> str:
        nonlocal index
        if inline is not None:
            return inline
        if index + 1 >= len(tokens):
            raise CurlImportError(f"选项 {option} 缺少参数")
        index += 1
        return tokens[index]

    while index < len(tokens):
        token = tokens[index]
        option, inline = _split_option(token)
        if option in ignored_flags:
            index += 1
            continue
        if option in no_value_flags:
            if option in ("-L", "--location"):
                follow_redirects = True
            elif option in ("-G", "--get"):
                use_get_data = True
            elif option in ("-I", "--head"):
                head_request = True
            elif option in ("-k", "--insecure"):
                verify_ssl = False
            elif option == "--http1.1":
                http_version = "1.1"
            elif option == "--http2":
                http_version = "2"
            else:
                warnings.append(f"已忽略输出格式选项 {option}")
            index += 1
            continue
        if option in value_options:
            value = value_for(option, inline)
            if option in ("-X", "--request"):
                method = value.upper()
            elif option in ("-H", "--header"):
                if ":" not in value:
                    warnings.append(f"无法解析 Header: {value}")
                else:
                    key, content = value.split(":", 1)
                    headers.append((key.strip(), content.lstrip()))
            elif option in ("-d", "--data", "--data-raw", "--data-ascii", "--data-binary"):
                raw_data.append(value)
            elif option == "--data-urlencode":
                encoded_data.append(value)
            elif option in ("-F", "--form", "--form-string"):
                if "=" not in value:
                    warnings.append(f"无法解析 multipart 字段: {value}")
                else:
                    key, content = value.split("=", 1)
                    if content.startswith("@") and option != "--form-string":
                        path = content[1:].split(";", 1)[0]
                        multipart.append(FormItem(key=key, value=path, kind="file"))
                    else:
                        multipart.append(FormItem(key=key, value=content, kind="text"))
            elif option in ("-u", "--user"):
                username, separator, password = value.partition(":")
                basic_auth = (username, password if separator else "")
            elif option in ("-b", "--cookie"):
                if value.startswith("@"):
                    warnings.append("Cookie 文件未导入；请将 Cookie 内容粘贴到 Header")
                else:
                    cookie_values.append(value)
            elif option in ("-A", "--user-agent"):
                headers.append(("User-Agent", value))
            elif option in ("-e", "--referer"):
                headers.append(("Referer", value))
            elif option in ("-x", "--proxy"):
                proxy_value = value
            elif option == "--proxy-user":
                proxy_user = value
            elif option in ("-m", "--max-time"):
                try:
                    timeout = float(value)
                except ValueError:
                    warnings.append(f"无法解析超时值: {value}")
            elif option in ("--connect-timeout", "--max-redirs"):
                warnings.append(f"{option} 没有独立映射，使用应用默认值")
            elif option in ("--url", "--url-query"):
                if option == "--url":
                    url = value
                else:
                    raw_data.append(value)
                    use_get_data = True
            elif option in ("-w", "--write-out", "-o", "--output"):
                warnings.append(f"响应输出选项 {option} 未导入")
            else:
                warnings.append(f"curl 选项 {option} 未导入")
            index += 1
            continue
        if token.startswith("-") and token != "-":
            warnings.append(f"未知选项未导入: {token}")
            index += 1
            continue
        if not url:
            url = token
        else:
            warnings.append(f"忽略了额外参数: {token}")
        index += 1

    if not url:
        raise CurlImportError("curl 命令中没有请求 URL")

    parts = urlsplit(url)
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    form_pairs: list[tuple[str, str]] = []
    body_text = ""
    body_type = "None"
    all_data = raw_data
    for item in encoded_data:
        if "=" in item:
            key, value = item.split("=", 1)
            form_pairs.append((key, value))
        else:
            form_pairs.append(("", item))
    for item in all_data:
        form_pairs.extend(parse_qsl(item, keep_blank_values=True))

    content_type = next(
        (value.lower() for key, value in reversed(headers) if key.lower() == "content-type"),
        "",
    )
    if multipart:
        body_type = "multipart/form-data"
    elif raw_data or encoded_data:
        if "application/json" in content_type:
            body_type = "JSON"
            body_text = "&".join(raw_data)
            try:
                json.loads(body_text)
            except json.JSONDecodeError:
                warnings.append("Body 标记为 JSON，但内容不是有效 JSON")
        elif "application/x-www-form-urlencoded" in content_type or (
            not content_type and form_pairs and all("=" in item for item in raw_data)
        ) or encoded_data:
            body_type = "Form URL Encoded"
        else:
            body_type = "Raw"
            body_text = "&".join(raw_data)
        if body_type == "JSON" and not body_text and raw_data:
            body_text = "&".join(raw_data)

    if use_get_data:
        query_items.extend(form_pairs)
        body_type = "None"
        body_text = ""
    elif body_type == "Form URL Encoded":
        pass
    elif body_type == "Raw" and not content_type:
        try:
            json.loads(body_text)
            body_type = "JSON"
        except (json.JSONDecodeError, TypeError):
            pass

    if method:
        final_method = method
    elif head_request:
        final_method = "HEAD"
    elif (raw_data or encoded_data or multipart) and not use_get_data:
        final_method = "POST"
    else:
        final_method = "GET"

    if cookie_values:
        existing_index = next((i for i, (key, _) in enumerate(headers) if key.lower() == "cookie"), None)
        cookie = "; ".join(cookie_values)
        if existing_index is None:
            headers.append(("Cookie", cookie))
        else:
            key, existing = headers[existing_index]
            headers[existing_index] = (key, "; ".join((existing, cookie)))

    request = RequestData(
        method=final_method,
        url=clean_url,
        params=query_items,
        headers=headers,
        auth_type="Basic Auth" if basic_auth else "None",
        basic_username=basic_auth[0] if basic_auth else "",
        basic_password=basic_auth[1] if basic_auth else "",
        body_type=body_type,
        body_text=body_text,
        form_items=[FormItem(key=k, value=v) for k, v in form_pairs] if body_type == "Form URL Encoded" else [],
        multipart_items=multipart,
        timeout=timeout,
        verify_ssl=verify_ssl,
        follow_redirects=follow_redirects,
        http_version=http_version,
    )
    if proxy_value:
        normalized = proxy_value if "://" in proxy_value else f"http://{proxy_value}"
        proxy = urlsplit(normalized)
        scheme = proxy.scheme.lower()
        if scheme == "socks5h":
            scheme = "socks5"
        if scheme not in {"http", "https", "socks5"}:
            warnings.append(f"不支持的代理协议 {proxy.scheme}，使用 HTTP")
            scheme = "http"
        request.proxy_enabled = True
        request.proxy_type = scheme
        request.proxy_host = proxy.hostname or ""
        request.proxy_port = proxy.port or (443 if scheme == "https" else 1080 if scheme == "socks5" else 8080)
        request.proxy_username = unquote(proxy.username or "")
        request.proxy_password = unquote(proxy.password or "")
    if proxy_user:
        request.proxy_username, separator, request.proxy_password = proxy_user.partition(":")
        if not separator:
            request.proxy_password = ""
    return request, warnings
