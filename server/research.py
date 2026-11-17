"""Research reports and papers ingestion (phase 1).

A standalone service with its own config, tables, API and page:

- config: config/research.json
- tables: research_items / research_sources
- API: /api/research
- page: /research.html

Phase 1 only stores metadata, abstract, authors, institutions, date, DOI /
report number, original URL and a PDF link. It does not download or extract
full text.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db import connect, init_db, utcnow
from util import _first_text, _local, _open, _parse_date, clean_title, ensure_http, strip_html

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("RESEARCH_CONFIG_PATH", ROOT / "config" / "research.json"))
LOG = logging.getLogger("research")
NO_FETCH = os.environ.get("RESEARCH_NO_FETCH", "").lower() in ("1", "true", "yes")

_lock = threading.Lock()
_refreshing = False
_last_result: dict[str, Any] = {}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _clean(value: Any, limit: int = 4000) -> str:
    text = strip_html(str(value or ""), limit)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _list(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        text = _clean(item, 300)
        if text and text not in result:
            result.append(text)
    return result[:30]


def _from_date(days: int) -> str:
    days = max(1, min(int(days or 30), 365))
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def _date(value: str | None) -> str:
    if not value:
        return utcnow()
    return _parse_date(str(value))


def load_config() -> dict:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("research config unavailable: %s", exc)
        raw = {}
    settings = raw.get("settings") if isinstance(raw, dict) else {}
    settings = settings if isinstance(settings, dict) else {}
    default_refresh = int(settings.get("default_refresh_minutes", 360))
    default_max = int(settings.get("max_items_per_source", 20))
    sources = []
    for item in raw.get("sources", []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        adapter = str(item.get("adapter") or "")
        if adapter not in ADAPTERS:
            LOG.warning("unknown research adapter: %s", adapter)
            continue
        sources.append(
            {
                "id": str(item["id"]),
                "name": str(item.get("name") or item["id"]),
                "publisher": str(item.get("publisher") or item["id"]),
                "adapter": adapter,
                "query": str(item.get("query") or ""),
                "homepage": str(item.get("homepage") or ""),
                "country": str(item.get("country") or ""),
                "language": str(item.get("language") or "en"),
                "doc_type": str(item.get("doc_type") or "paper"),
                "days": int(item.get("days", 30)),
                "refresh_minutes": int(item.get("refresh_minutes", default_refresh)),
                "max_items": int(item.get("max_items", default_max)),
                "enabled": bool(item.get("enabled", True)),
                "note": str(item.get("note") or ""),
            }
        )
    return {
        "settings": {
            "request_timeout": int(settings.get("request_timeout", 30)),
            "user_agent": str(settings.get("user_agent") or "GlobalResearchTimeline/1.0"),
            "contact_email": str(settings.get("contact_email") or ""),
            "default_refresh_minutes": default_refresh,
            "max_items_per_source": default_max,
        },
        "sources": sources,
    }


def _request(url: str, settings: dict, accept: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": settings.get("user_agent") or "GlobalResearchTimeline/1.0",
            "Accept": accept,
        },
    )
    with _open(request, int(settings.get("request_timeout", 30))) as response:
        return response.read(8 * 1024 * 1024)


def _json(url: str, settings: dict) -> dict:
    raw = _request(url, settings, "application/json, */*")
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("接口返回的不是 JSON") from exc
    return body if isinstance(body, dict) else {}


def _xml(url: str, settings: dict) -> ET.Element:
    return ET.fromstring(_request(url, settings, "application/atom+xml, application/xml, text/xml, */*"))


def _item(source: dict, **fields) -> dict | None:
    title = clean_title(fields.get("title"))
    if not title:
        return None
    url = ensure_http(fields.get("url") or "")
    doi = str(fields.get("doi") or "").strip()
    if doi.lower().startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    if doi.lower().startswith("http://doi.org/"):
        doi = doi[len("http://doi.org/"):]
    if not url and doi:
        url = "https://doi.org/" + doi
    if not url:
        return None
    key = source["id"] + "\0" + (doi.lower() if doi else url)
    return {
        "id": hashlib.sha1(key.encode("utf-8")).hexdigest(),
        "source_id": source["id"],
        "source_name": source["name"],
        "publisher": str(fields.get("publisher") or source["publisher"]),
        "title": title,
        "abstract": _clean(fields.get("abstract"), 5000),
        "authors": _list(fields.get("authors")),
        "institutions": _list(fields.get("institutions")),
        "published_at": _date(fields.get("published_at")),
        "doi": doi,
        "report_no": str(fields.get("report_no") or "").strip(),
        "url": url,
        "pdf_url": ensure_http(fields.get("pdf_url") or "") or "",
        "doc_type": source["doc_type"],
        "language": source["language"],
        "country": source["country"],
        "topics": _list(fields.get("topics")),
        "fetched_at": utcnow(),
        "fingerprint": hashlib.sha1(
            (title.casefold() + "\0" + doi.casefold()).encode("utf-8")
        ).hexdigest(),
    }


def fetch_arxiv(source: dict, settings: dict) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "search_query": source["query"] or "cat:cs.AI",
            "start": 0,
            "max_results": source["max_items"],
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    root = _xml("https://export.arxiv.org/api/query?" + params, settings)
    items = []
    for entry in root.iter():
        if _local(entry.tag) != "entry":
            continue
        title = _first_text(entry, ("title",))
        summary = _first_text(entry, ("summary",))
        published = _first_text(entry, ("published", "updated"))
        abs_url = _first_text(entry, ("id",))
        pdf_url = ""
        for child in entry.iter():
            if _local(child.tag) != "link":
                continue
            href = child.get("href") or ""
            if child.get("title") == "pdf" or (child.get("type") or "").startswith("application/pdf"):
                pdf_url = href
        authors = [
            _first_text(author, ("name",))
            for author in entry.iter()
            if _local(author.tag) == "author"
        ]
        topics = [
            child.get("term") or ""
            for child in entry.iter()
            if _local(child.tag) == "category" and child.get("term")
        ]
        report_no = abs_url.rsplit("/abs/", 1)[-1] if "/abs/" in abs_url else ""
        item = _item(
            source,
            title=title,
            abstract=summary,
            published_at=published,
            url=abs_url,
            pdf_url=pdf_url,
            authors=authors,
            doi=_first_text(entry, ("doi",)),
            report_no=report_no,
            topics=topics,
            publisher=source["publisher"],
        )
        if item:
            items.append(item)
    return items


def _inverted_abstract(value) -> str:
    if not isinstance(value, dict):
        return ""
    positions = {}
    for word, indexes in value.items():
        for index in indexes or []:
            try:
                positions[int(index)] = str(word)
            except (TypeError, ValueError):
                continue
    return " ".join(positions[index] for index in sorted(positions))


def fetch_openalex(source: dict, settings: dict) -> list[dict]:
    params = {
        "search": source["query"] or "research",
        "filter": f"from_publication_date:{_from_date(source['days'])},type:article",
        "sort": "publication_date:desc",
        "per-page": source["max_items"],
        "select": (
            "id,doi,title,publication_date,authorships,primary_location,"
            "best_oa_location,abstract_inverted_index,primary_topic,type"
        ),
    }
    if settings.get("contact_email"):
        params["mailto"] = settings["contact_email"]
    body = _json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), settings)
    items = []
    for work in body.get("results", []) or []:
        if not isinstance(work, dict):
            continue
        primary = work.get("primary_location") or {}
        best = work.get("best_oa_location") or {}
        source_info = primary.get("source") or {}
        authors = []
        institutions = []
        for authorship in work.get("authorships", []) or []:
            author = authorship.get("author") or {}
            if author.get("display_name"):
                authors.append(author["display_name"])
            for institution in authorship.get("institutions", []) or []:
                if institution.get("display_name"):
                    institutions.append(institution["display_name"])
        topic = work.get("primary_topic") or {}
        topics = [topic.get("display_name")] if topic.get("display_name") else []
        doi = str(work.get("doi") or "")
        url = primary.get("landing_page_url") or work.get("id") or ""
        item = _item(
            source,
            title=work.get("title"),
            abstract=_inverted_abstract(work.get("abstract_inverted_index")),
            published_at=work.get("publication_date"),
            url=url,
            pdf_url=primary.get("pdf_url") or best.get("pdf_url") or "",
            doi=doi,
            authors=authors,
            institutions=institutions,
            topics=topics,
            publisher=source_info.get("display_name") or source["publisher"],
        )
        if item:
            items.append(item)
    return items


def _pubmed_date(node: ET.Element) -> str:
    year = _first_text(node, ("year",))
    month = _first_text(node, ("month",))
    day = _first_text(node, ("day",))
    if not year:
        return _parse_date(_first_text(node, ("medlinedate",)))
    try:
        month_num = int(month)
    except (TypeError, ValueError):
        month_num = _MONTHS.get((month or "")[:3].lower(), 1)
    try:
        day_num = int(day) if day else 1
    except ValueError:
        day_num = 1
    try:
        return datetime(
            int(year), month_num, day_num, tzinfo=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return _parse_date(year)


def fetch_pubmed(source: dict, settings: dict) -> list[dict]:
    term = source["query"] or "research"
    search = urllib.parse.urlencode(
        {
            "db": "pubmed",
            "term": term,
            "retmax": source["max_items"],
            "sort": "date",
            "retmode": "json",
        }
    )
    search_body = _json(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + search,
        settings,
    )
    ids = (search_body.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []
    fetch = urllib.parse.urlencode(
        {"db": "pubmed", "id": ",".join(map(str, ids)), "retmode": "xml"}
    )
    root = _xml(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + fetch,
        settings,
    )
    items = []
    for article in root.iter():
        if _local(article.tag) not in ("pubmedarticle", "pubmedbookarticle"):
            continue
        pmid = _first_text(article, ("pmid",))
        title = ""
        for node in article.iter():
            if _local(node.tag) == "articletitle":
                title = "".join(node.itertext())
                break
        abstract_parts = []
        for node in article.iter():
            if _local(node.tag) != "abstracttext":
                continue
            text = "".join(node.itertext()).strip()
            if text:
                label = (node.get("Label") or "").strip()
                abstract_parts.append(f"{label}: {text}" if label else text)
        journal = ""
        for node in article.iter():
            if _local(node.tag) == "journal":
                journal = _first_text(node, ("title",))
                break
        authors = []
        institutions = []
        for node in article.iter():
            if _local(node.tag) != "author":
                continue
            last = _first_text(node, ("lastname",))
            fore = _first_text(node, ("forename",))
            collective = _first_text(node, ("collectivename",))
            name = f"{fore} {last}".strip() or collective
            if name:
                authors.append(name)
            affiliation = _first_text(node, ("affiliation",))
            if affiliation:
                institutions.append(affiliation)
        doi = ""
        for node in article.iter():
            if _local(node.tag) == "articleid" and (node.get("IdType") or "").lower() == "doi":
                doi = "".join(node.itertext()).strip()
                break
        if not doi:
            for node in article.iter():
                if _local(node.tag) == "elocationid" and (node.get("EIdType") or "").lower() == "doi":
                    doi = "".join(node.itertext()).strip()
                    break
        topics = [
            "".join(node.itertext()).strip()
            for node in article.iter()
            if _local(node.tag) == "keyword" and "".join(node.itertext()).strip()
        ]
        pubdate = None
        for node in article.iter():
            if _local(node.tag) == "pubdate":
                pubdate = node
                break
        published = _pubmed_date(pubdate) if pubdate is not None else utcnow()
        item = _item(
            source,
            title=title,
            abstract=" ".join(abstract_parts),
            published_at=published,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            doi=doi,
            report_no=f"PMID {pmid}" if pmid else "",
            authors=authors,
            institutions=institutions,
            topics=topics,
            publisher=journal or source["publisher"],
        )
        if item:
            items.append(item)
    return items


def fetch_federal_register(source: dict, settings: dict) -> list[dict]:
    params = [
        ("per_page", str(source["max_items"])),
        ("order", "newest"),
        ("conditions[publication_date][gte]", _from_date(source["days"])),
        ("fields[]", "title"),
        ("fields[]", "abstract"),
        ("fields[]", "publication_date"),
        ("fields[]", "html_url"),
        ("fields[]", "pdf_url"),
        ("fields[]", "document_number"),
        ("fields[]", "agencies"),
        ("fields[]", "type"),
    ]
    body = _json(
        "https://www.federalregister.gov/api/v1/documents.json?"
        + urllib.parse.urlencode(params),
        settings,
    )
    items = []
    for doc in body.get("results", []) or []:
        if not isinstance(doc, dict):
            continue
        agencies = []
        for agency in doc.get("agencies", []) or []:
            if isinstance(agency, dict):
                name = agency.get("raw_name") or agency.get("name")
                if name:
                    agencies.append(name)
        item = _item(
            source,
            title=doc.get("title"),
            abstract=doc.get("abstract"),
            published_at=doc.get("publication_date"),
            url=doc.get("html_url"),
            pdf_url=doc.get("pdf_url"),
            report_no=doc.get("document_number"),
            institutions=agencies,
            topics=[doc.get("type")] if doc.get("type") else [],
            publisher=source["publisher"],
        )
        if item:
            items.append(item)
    return items


def fetch_crossref(source: dict, settings: dict) -> list[dict]:
    params = {
        "query": source["query"] or "research",
        "filter": f"from-pub-date:{_from_date(source['days'])},type:journal-article",
        "rows": source["max_items"],
        "sort": "published",
        "order": "desc",
        "select": "DOI,title,abstract,author,published,published-online,URL,container-title,subject,link,publisher",
    }
    if settings.get("contact_email"):
        params["mailto"] = settings["contact_email"]
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    body = None
    for attempt in range(2):
        try:
            body = _json(url, settings)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                time.sleep(3)
                continue
            raise
    if not body:
        return []
    items = []
    for work in (body.get("message") or {}).get("items", []) or []:
        if not isinstance(work, dict):
            continue
        titles = work.get("title") or []
        title = titles[0] if titles else ""
        author_names = []
        institutions = []
        for author in work.get("author", []) or []:
            name = " ".join(
                part for part in [author.get("given"), author.get("family")] if part
            ).strip()
            if name:
                author_names.append(name)
            for affiliation in author.get("affiliation", []) or []:
                if affiliation.get("name"):
                    institutions.append(affiliation["name"])
        published = work.get("published") or work.get("published-online") or {}
        parts = (published.get("date-parts") or [[None]])[0]
        published_at = ""
        if parts and parts[0]:
            published_at = "-".join(
                str(part).zfill(2) for part in parts[:3]
            ).replace("-", "-", 1)
        pdf_url = ""
        for link in work.get("link", []) or []:
            if "pdf" in str(link.get("content-type") or "").lower():
                pdf_url = link.get("URL") or ""
                break
        doi = work.get("DOI") or ""
        item = _item(
            source,
            title=title,
            abstract=work.get("abstract"),
            published_at=published_at,
            url=work.get("URL") or (("https://doi.org/" + doi) if doi else ""),
            pdf_url=pdf_url,
            doi=doi,
            authors=author_names,
            institutions=institutions,
            topics=work.get("subject") or [],
            publisher=(work.get("container-title") or [source["publisher"]])[0],
        )
        if item:
            items.append(item)
    return items


ADAPTERS = {
    "arxiv": fetch_arxiv,
    "openalex": fetch_openalex,
    "pubmed": fetch_pubmed,
    "federal_register": fetch_federal_register,
    "crossref": fetch_crossref,
}


def sync_sources(conn, sources: list[dict]) -> None:
    for source in sources:
        conn.execute(
            """
            INSERT INTO research_sources (
                id, name, publisher, adapter, query, homepage, country, language,
                doc_type, refresh_minutes, max_items, enabled
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, publisher=excluded.publisher, adapter=excluded.adapter,
                query=excluded.query, homepage=excluded.homepage, country=excluded.country,
                language=excluded.language, doc_type=excluded.doc_type,
                refresh_minutes=excluded.refresh_minutes, max_items=excluded.max_items,
                enabled=excluded.enabled
            """,
            (
                source["id"], source["name"], source["publisher"], source["adapter"],
                source["query"], source["homepage"], source["country"], source["language"],
                source["doc_type"], source["refresh_minutes"], source["max_items"],
                1 if source["enabled"] else 0,
            ),
        )
    conn.commit()


def source_states(conn) -> dict[str, dict]:
    return {row["id"]: dict(row) for row in conn.execute("SELECT * FROM research_sources")}


def update_source_state(
    conn,
    source_id: str,
    *,
    status: str,
    error: str | None = None,
    count: int = 0,
) -> None:
    conn.execute(
        """
        UPDATE research_sources SET last_fetch_at=?, last_status=?, last_error=?,
            last_count=?, total_fetched=total_fetched+?
        WHERE id=?
        """,
        (utcnow(), status, error, count, count, source_id),
    )
    conn.commit()


def insert_items(conn, items: list[dict]) -> int:
    new = 0
    for item in items:
        if item.get("doi"):
            existing = conn.execute(
                "SELECT 1 FROM research_items WHERE lower(doi)=lower(?) LIMIT 1",
                (item["doi"],),
            ).fetchone()
            if existing:
                continue
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO research_items (
                id, source_id, source_name, publisher, title, abstract, authors,
                institutions, published_at, doi, report_no, url, pdf_url, doc_type,
                language, country, topics, fetched_at, fingerprint
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                item["id"], item["source_id"], item["source_name"], item["publisher"],
                item["title"], item["abstract"],
                json.dumps(item["authors"], ensure_ascii=False),
                json.dumps(item["institutions"], ensure_ascii=False),
                item["published_at"], item["doi"], item["report_no"], item["url"],
                item["pdf_url"], item["doc_type"], item["language"], item["country"],
                json.dumps(item["topics"], ensure_ascii=False), item["fetched_at"],
                item["fingerprint"],
            ),
        )
        if conn.total_changes > before:
            new += 1
    conn.commit()
    return new


def _loads(value: Any) -> list:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def query_items(conn, filters: dict) -> tuple[list[dict], int, dict]:
    where = []
    params: list[Any] = []
    q = str(filters.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        where.append(
            "(title LIKE ? OR abstract LIKE ? OR authors LIKE ? OR institutions LIKE ? OR topics LIKE ?)"
        )
        params.extend([like] * 5)
    source = str(filters.get("source") or "").strip()
    if source:
        where.append("source_id=?")
        params.append(source)
    doc_type = str(filters.get("doc_type") or "").strip()
    if doc_type:
        where.append("doc_type=?")
        params.append(doc_type)
    try:
        days = int(filters.get("days") or 90)
    except (TypeError, ValueError):
        days = 90
    days = max(1, min(days, 3650))
    if days < 3650:
        where.append("published_at >= ?")
        params.append(_from_date(days) + "T00:00:00Z")
    clause = " WHERE " + " AND ".join(where) if where else ""
    total = conn.execute(
        "SELECT COUNT(*) FROM research_items" + clause, params
    ).fetchone()[0]
    try:
        limit = max(1, min(int(filters.get("limit") or 50), 100))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = max(0, int(filters.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    rows = conn.execute(
        "SELECT * FROM research_items"
        + clause
        + " ORDER BY published_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        for key in ("authors", "institutions", "topics"):
            item[key] = _loads(item.get(key))
        items.append(item)
    doc_types = {
        row["doc_type"]: row["n"]
        for row in conn.execute(
            "SELECT doc_type, COUNT(*) AS n FROM research_items GROUP BY doc_type"
        )
    }
    sources = {
        row["source_id"]: row["n"]
        for row in conn.execute(
            "SELECT source_id, COUNT(*) AS n FROM research_items GROUP BY source_id"
        )
    }
    return items, total, {"doc_types": doc_types, "sources": sources}


def source_list(conn) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM research_sources ORDER BY name")]


def stats(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM research_items").fetchone()[0]
    papers = conn.execute(
        "SELECT COUNT(*) FROM research_items WHERE doc_type='paper'"
    ).fetchone()[0]
    reports = conn.execute(
        "SELECT COUNT(*) FROM research_items WHERE doc_type='report'"
    ).fetchone()[0]
    sources = conn.execute("SELECT COUNT(*) FROM research_sources").fetchone()[0]
    return {"items": total, "papers": papers, "reports": reports, "sources": sources}


def _due(source: dict, state: dict | None, now: datetime) -> bool:
    if not state or not state.get("last_fetch_at"):
        return True
    try:
        last = datetime.fromisoformat(str(state["last_fetch_at"]).replace("Z", "+00:00"))
    except ValueError:
        return True
    return now - last >= timedelta(minutes=int(source["refresh_minutes"]))


def _fetch_source(source: dict, settings: dict) -> dict:
    started = time.time()
    try:
        items = ADAPTERS[source["adapter"]](source, settings)
        return {
            "source": source,
            "status": "ok" if items else "empty",
            "items": items,
            "seconds": round(time.time() - started, 1),
            "error": None,
        }
    except Exception as exc:
        LOG.warning("research source %s failed: %s", source["id"], exc)
        return {
            "source": source,
            "status": "error",
            "items": [],
            "seconds": round(time.time() - started, 1),
            "error": str(exc)[:300],
        }


def refresh(force: bool = False, only_ids: list[str] | None = None) -> dict:
    global _refreshing, _last_result
    if not _lock.acquire(blocking=False):
        return {"running": True, "message": "A research refresh is already running"}
    _refreshing = True
    started = time.time()
    conn = None
    try:
        config = load_config()
        sources = [source for source in config["sources"] if source["enabled"]]
        if only_ids:
            wanted = set(only_ids)
            sources = [source for source in sources if source["id"] in wanted]
        conn = connect()
        init_db(conn)
        sync_sources(conn, config["sources"])
        states = source_states(conn)
        now = datetime.now(timezone.utc)
        due = [source for source in sources if force or _due(source, states.get(source["id"]), now)]
        results = []
        if due:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                futures = [
                    pool.submit(_fetch_source, source, config["settings"])
                    for source in due
                ]
                for future in concurrent.futures.as_completed(futures):
                    results.append(future.result())
        total_items = total_new = ok = errors = 0
        for result in results:
            source = result["source"]
            items = result["items"]
            total_items += len(items)
            if result["status"] == "error":
                errors += 1
                update_source_state(conn, source["id"], status="error", error=result["error"])
            else:
                ok += 1
                new = insert_items(conn, items)
                total_new += new
                update_source_state(conn, source["id"], status=result["status"], count=len(items))
        _last_result = {
            "at": utcnow(),
            "sources": len(results),
            "ok": ok,
            "errors": errors,
            "items": total_items,
            "new": total_new,
            "seconds": round(time.time() - started, 1),
        }
        if results:
            LOG.info("research refresh done: %s", _last_result)
        return _last_result
    except Exception as exc:
        LOG.exception("research refresh failed")
        _last_result = {"at": utcnow(), "error": str(exc)}
        return _last_result
    finally:
        if conn is not None:
            conn.close()
        _refreshing = False
        _lock.release()


def refresh_async(force: bool = False) -> dict:
    if _refreshing:
        return {"running": True, "message": "A research refresh is already running"}
    thread = threading.Thread(target=refresh, kwargs={"force": force}, daemon=True)
    thread.start()
    return {"running": True, "message": "Research refresh started"}


def bootstrap() -> None:
    config = load_config()
    conn = connect()
    try:
        init_db(conn)
        sync_sources(conn, config["sources"])
    finally:
        conn.close()


def scheduler_loop() -> None:
    if NO_FETCH:
        LOG.info("RESEARCH_NO_FETCH set; research scheduler disabled")
        return
    time.sleep(5)
    while True:
        try:
            refresh(force=False)
        except Exception:
            LOG.exception("research scheduler iteration failed")
        time.sleep(300)
