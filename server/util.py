"""Small text/HTTP helpers used by the research adapters."""
from __future__ import annotations

import html
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(value: str | None, limit: int | None = None) -> str:
    if not value:
        return ""
    if "<" in value and ">" in value:
        parser = _TextExtractor()
        try:
            parser.feed(value)
            text = " ".join(parser.parts)
        except Exception:
            text = _TAG.sub(" ", value)
    else:
        text = value
    text = html.unescape(text)
    text = _WS.sub(" ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def clean_title(title: str | None, publisher: str | None = None) -> str:
    text = strip_html(title)
    if publisher:
        for suffix in (f" - {publisher}", f" | {publisher}"):
            if text.endswith(suffix):
                text = text[: -len(suffix)].rstrip()
    return text.strip(" -–—|")


def ensure_http(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        return None
    return url


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in node.iter():
        if _local(child.tag) in names and (child.text or "").strip():
            return (child.text or "").strip()
    return ""


def _parse_date(value: str | None) -> str:
    value = (value or "").strip()
    now = datetime.now(timezone.utc)
    dt: datetime | None = None
    if value:
        try:
            dt = parsedate_to_datetime(value)
        except Exception:
            dt = None
        if dt is None:
            cleaned = value.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(cleaned)
            except Exception:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(value, fmt)
                        break
                    except Exception:
                        continue
    if dt is None:
        dt = now
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if dt.year < 2000 or dt > now + timedelta(minutes=15):
        dt = now
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _detect_proxy() -> str | None:
    for name in (
        "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
        "ALL_PROXY", "all_proxy",
    ):
        value = os.environ.get(name)
        if value:
            return value
    if os.name != "nt":
        return None
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
        if not enabled or not server:
            return None
        if "=" in server:
            parts = dict(part.split("=", 1) for part in server.split(";") if "=" in part)
            server = parts.get("https") or parts.get("http") or ""
        if not server:
            return None
        return server if "://" in server else "http://" + server
    except Exception:
        return None


_PROXY = _detect_proxy()
_PROXY_OPENER = (
    urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": _PROXY, "https": _PROXY})
    )
    if _PROXY
    else urllib.request.build_opener()
)
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_DIRECT_SUFFIXES = (".gov", ".mil")


def _open(request: urllib.request.Request, timeout: int):
    host = (urlsplit(request.full_url).hostname or "").lower()
    if host.endswith(_DIRECT_SUFFIXES):
        return _DIRECT_OPENER.open(request, timeout=timeout)
    try:
        return _PROXY_OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 407, 451):
            raise
        return _DIRECT_OPENER.open(request, timeout=timeout)
    except (urllib.error.URLError, OSError):
        return _DIRECT_OPENER.open(request, timeout=timeout)
