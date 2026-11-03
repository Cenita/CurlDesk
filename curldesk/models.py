from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class FormItem:
    key: str = ""
    value: str = ""
    kind: str = "text"


@dataclass
class RequestData:
    method: str = "GET"
    url: str = ""
    params: list[tuple[str, str]] = field(default_factory=list)
    headers: list[tuple[str, str]] = field(default_factory=list)
    auth_type: str = "None"
    bearer_token: str = ""
    basic_username: str = ""
    basic_password: str = ""
    body_type: str = "None"
    body_text: str = ""
    form_items: list[FormItem] = field(default_factory=list)
    multipart_items: list[FormItem] = field(default_factory=list)
    proxy_enabled: bool = True
    proxy_type: str = "socks5"
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 10809
    proxy_username: str = ""
    proxy_password: str = ""
    impersonate: str = "chrome120"
    http_version: str = "auto"
    verify_ssl: bool = True
    timeout: float = 30.0
    follow_redirects: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RequestData:
        data = dict(value)
        data["params"] = [tuple(row) for row in data.get("params", [])]
        data["headers"] = [tuple(row) for row in data.get("headers", [])]
        data["form_items"] = [
            item if isinstance(item, FormItem) else FormItem(**item)
            for item in data.get("form_items", [])
        ]
        data["multipart_items"] = [
            item if isinstance(item, FormItem) else FormItem(**item)
            for item in data.get("multipart_items", [])
        ]
        known = cls.__dataclass_fields__
        return cls(**{key: val for key, val in data.items() if key in known})


@dataclass
class ResponseData:
    status_code: int = 0
    reason: str = ""
    elapsed_ms: float = 0.0
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    final_url: str = ""
    error: str = ""
