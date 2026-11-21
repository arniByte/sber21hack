"""First-seen capture of timestamped items from configured public RSS and Atom feeds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree as ET

from .data import Event, MSK, load_events, write_events


MAX_BYTES = 2_000_000
FEED_TIMEOUT = 12


@dataclass(frozen=True, slots=True)
class Feed:
    url: str
    symbol: str
    source: str


def _public_https(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.port not in (None, 443):
        raise ValueError("для ленты нужен общедоступный HTTPS URL без учётных данных")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if ("." not in host or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid"))
                or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)
                or not any(letter.isalpha() for letter in host.split(".")[-1])):
            raise ValueError("для ленты укажите общедоступное имя хоста")
    else:
        if not address.is_global:
            raise ValueError("локальные адреса нельзя использовать как источник ленты")
    return url


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str) -> bytes:
    opener = build_opener(PublicRedirects())
    request = Request(
        _public_https(url),
        headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
                 "User-Agent": "Kontur/1.0"},
    )
    with opener.open(request, timeout=FEED_TIMEOUT) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError(f"лента превышает {MAX_BYTES} байт")
    return body


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(value: str) -> str:
    parser = _Text()
    parser.feed(value)
    parser.close()
    return " ".join(unescape(" ".join(parser.parts)).split())


def _node(parent: ET.Element, *names: str) -> ET.Element | None:
    children = {child.tag.rsplit("}", 1)[-1]: child for child in parent}
    return next((children[name] for name in names if name in children), None)


def _value(parent: ET.Element, *names: str) -> str:
    child = _node(parent, *names)
    return " ".join(child.itertext()).strip() if child is not None else ""


def _published(entry: ET.Element) -> datetime | None:
    value = _value(entry, "published", "pubDate", "date")
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return result.astimezone(MSK) if result.utcoffset() is not None else None


def _article_url(entry: ET.Element, feed_url: str) -> str:
    for child in entry:
        if child.tag.rsplit("}", 1)[-1] != "link" or child.get("rel", "alternate") != "alternate":
            continue
        link = (child.get("href") or child.text or "").strip()
        if not link:
            continue
        url = urljoin(feed_url, link)
        try:
            return _public_https(url)
        except ValueError:
            continue
    return ""


def _entries(feed: Feed, payload: bytes, seen_at: datetime) -> list[Event]:
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", payload.replace(b"\x00", b""), flags=re.IGNORECASE):
        raise ValueError("DTD и XML-сущности в ленте не поддерживаются")
    root = ET.fromstring(payload)
    root_name = root.tag.rsplit("}", 1)[-1]
    if root_name not in ("rss", "feed", "RDF"):
        raise ValueError("ожидалась лента RSS или Atom")
    events = []
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] not in ("entry", "item"):
            continue
        published = _published(entry)
        link = _article_url(entry, feed.url)
        native_id = _value(entry, "guid", "id") or link
        title = _plain(_value(entry, "title"))
        if published is None or published > seen_at or not native_id or not title:
            continue
        key = "\0".join((feed.source, feed.url, native_id))
        event_id = "feed-" + sha256(key.encode("utf-8")).hexdigest()
        author = _node(entry, "author")
        name = _value(author, "name") if author is not None else ""
        events.append(Event(
            event_id=event_id,
            symbol=feed.symbol,
            published_at=published,
            available_at=seen_at,
            source=feed.source,
            author=_plain(name or _value(entry, "author", "creator")),
            title=title,
            text=_plain(_value(entry, "content", "encoded", "summary", "description")),
            url=link,
        ))
    return events


def _load_config(path: Path) -> list[Feed]:
    with Path(path).open(encoding="utf-8-sig") as stream:
        config = json.load(stream)
    if not isinstance(config, list) or not config:
        raise ValueError("файл источников должен содержать непустой массив лент")
    feeds = []
    for index, row in enumerate(config, 1):
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in ("url", "symbol", "source")
        ):
            raise ValueError(f"лента №{index}: требуются url, symbol и source")
        url = _public_https(row["url"].strip())
        feeds.append(Feed(url, row["symbol"].strip(), row["source"].strip()))
    return feeds


def collect_feeds(config_path: Path, output_path: Path, *, now: datetime | None = None) -> int:
    """Append new feed records without treating older publications as earlier receipts."""
    if now is not None and now.utcoffset() is None:
        raise ValueError("время получения должно включать часовой пояс")
    feeds = _load_config(config_path)
    output_path = Path(output_path)
    existing = load_events(output_path) if output_path.exists() else []
    events = {(event.symbol, event.event_id): event for event in existing}
    for feed in feeds:
        try:
            payload = _fetch(feed.url)
            seen_at = now.astimezone(MSK) if now is not None else datetime.now(MSK)
            for event in _entries(feed, payload, seen_at):
                events.setdefault((event.symbol, event.event_id), event)
        except (ET.ParseError, URLError, OSError, ValueError) as exc:
            raise ValueError(f"{feed.source}: не удалось обработать ленту ({exc})") from exc
    added = len(events) - len(existing)
    if added or not output_path.exists():
        write_events(output_path, events.values())
    return added
