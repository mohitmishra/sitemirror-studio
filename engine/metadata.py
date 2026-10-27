from __future__ import annotations

import datetime as dt
import json
import re
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
try:
    import tldextract
except Exception:
    tldextract = None

UA = "SiteMirrorStudio/1.5 (+website migration audit)"


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.()/-]*)?(?:\(?\d{2,4}\)?[\s.()/-]*){1,4}\d{2,4}(?!\w)")


def _clean_email(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = unquote(str(value)).strip()
    if raw.lower().startswith("mailto:"):
        raw = raw[7:]
    raw = raw.split("?", 1)[0].strip().strip("<>.,;:()[]{}\"'")
    m = EMAIL_RE.search(raw)
    if not m:
        return None
    return m.group(0).lower()


def _clean_phone(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = unquote(str(value)).strip()
    if raw.lower().startswith("tel:"):
        raw = raw[4:]
    raw = raw.split("?", 1)[0].strip()
    digits = re.sub(r"\D", "", raw)
    if not (7 <= len(digits) <= 15):
        return None
    # Avoid treating ISO-like dates and obvious version/date fragments as phones.
    if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", raw):
        return None
    cleaned = re.sub(r"\s+", " ", raw).strip(" ,.;")
    return cleaned or None


def _extract_contacts(soup: BeautifulSoup) -> Dict[str, List[Dict[str, str]]]:
    emails: Dict[str, Dict[str, str]] = {}
    phones: Dict[str, Dict[str, str]] = {}

    def add_email(value: Any, source: str):
        email = _clean_email(value)
        if email:
            emails.setdefault(email, {"value": email, "source": source})

    def add_phone(value: Any, source: str):
        phone = _clean_phone(value)
        if phone:
            key = re.sub(r"\D", "", phone)
            phones.setdefault(key, {"value": phone, "source": source})

    # Strongest contact signals first.
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        low = href.lower()
        if low.startswith("mailto:"):
            for part in href[7:].split(","):
                add_email(part, "mailto")
        elif low.startswith("tel:"):
            add_phone(href, "tel")

    # Structured data commonly carries the organisation's primary contact details.
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        for node in _walk_jsonld(data):
            add_email(node.get("email"), "json-ld")
            add_phone(node.get("telephone"), "json-ld")
            cp = node.get("contactPoint")
            cps = cp if isinstance(cp, list) else [cp] if isinstance(cp, dict) else []
            for item in cps:
                if isinstance(item, dict):
                    add_email(item.get("email"), "json-ld:contactPoint")
                    add_phone(item.get("telephone"), "json-ld:contactPoint")

    # Visible text catches plain-text contact details that are not linked. Scripts/styles are
    # removed first to avoid harvesting values from analytics or source code.
    clone = BeautifulSoup(str(soup), "html.parser")
    for tag in clone(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    text = clone.get_text(" ", strip=True)
    for match in EMAIL_RE.finditer(text):
        add_email(match.group(0), "visible-text")
    for match in PHONE_RE.finditer(text):
        candidate = match.group(0).strip()
        digits = re.sub(r"\D", "", candidate)
        # Plain-text extraction is intentionally stricter than tel:/JSON-LD extraction.
        if len(digits) >= 8 and (candidate.startswith("+") or re.search(r"[\s().-]", candidate)):
            add_phone(candidate, "visible-text")

    return {
        "emails": list(emails.values())[:30],
        "phones": list(phones.values())[:30],
    }


def registrable_domain(host: str) -> str:
    host = host.lower().split(":")[0].strip(".")
    if tldextract is not None:
        try:
            ext = tldextract.extract(host)
            if ext.domain and ext.suffix:
                return "%s.%s" % (ext.domain, ext.suffix)
        except Exception:
            pass
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def parse_iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(v)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat()
    except Exception:
        try:
            parsed = parsedate_to_datetime(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc).isoformat()
        except Exception:
            return str(value).strip()


def age_details(date_value: Optional[str]) -> Dict[str, Any]:
    result = {"label": None, "days": None, "years": None, "months": None}
    if not date_value:
        return result
    try:
        d = dt.datetime.fromisoformat(str(date_value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        now = dt.datetime.now(dt.timezone.utc)
        days = (now - d.astimezone(dt.timezone.utc)).days
        if days < 0:
            return result
        years = days // 365
        months = (days % 365) // 30
        if years:
            label = "%sy %sm" % (years, months)
        elif months:
            label = "%sm" % months
        else:
            label = "%sd" % days
        return {"label": label, "days": days, "years": years, "months": months}
    except Exception:
        return result


def age_label(registered: Optional[str]) -> Optional[str]:
    return age_details(registered).get("label")


@lru_cache(maxsize=1)
def _rdap_bootstrap() -> Dict[str, Any]:
    r = requests.get("https://data.iana.org/rdap/dns.json", timeout=12, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.json()


def rdap_lookup(domain: str) -> Dict[str, Any]:
    domain = registrable_domain(domain)
    tld = domain.rsplit(".", 1)[-1].lower()
    result = {
        "domain": domain,
        "registered_at": None,
        "expires_at": None,
        "changed_at": None,
        "domain_age": None,
        "domain_age_days": None,
        "registrar": None,
        "status": [],
        "nameservers": [],
        "source": "RDAP",
        "source_url": None,
        "available": False,
        "confidence": "authoritative-when-published",
    }
    try:
        bootstrap = _rdap_bootstrap()
        base = None
        for tlds, urls in bootstrap.get("services", []):
            if tld in [x.lower() for x in tlds] and urls:
                base = urls[0]
                break
        candidates = []
        if base:
            candidates.append(urljoin(base.rstrip("/") + "/", "domain/" + domain))
        candidates.append("https://rdap.org/domain/" + domain)

        data = None
        endpoint_used = None
        for endpoint in candidates:
            try:
                r = requests.get(endpoint, timeout=15, headers={"User-Agent": UA, "Accept": "application/rdap+json, application/json"})
                if r.ok:
                    data = r.json()
                    endpoint_used = endpoint
                    break
            except Exception:
                continue
        if not data:
            return result

        for event in data.get("events", []):
            action = (event.get("eventAction") or "").lower()
            date = parse_iso(event.get("eventDate"))
            if action in {"registration", "registered"} and not result["registered_at"]:
                result["registered_at"] = date
            elif action in {"expiration", "expiry"} and not result["expires_at"]:
                result["expires_at"] = date
            elif action in {"last changed", "changed", "updated"} and not result["changed_at"]:
                result["changed_at"] = date

        for ent in data.get("entities", []):
            roles = [str(x).lower() for x in ent.get("roles", [])]
            if "registrar" not in roles:
                continue
            vcard = ent.get("vcardArray")
            if isinstance(vcard, list) and len(vcard) == 2:
                for row in vcard[1]:
                    if row and row[0] == "fn" and len(row) > 3:
                        result["registrar"] = row[3]
                        break
        result["status"] = data.get("status") or []
        result["nameservers"] = [x.get("ldhName") for x in data.get("nameservers", []) if x.get("ldhName")][:12]
        details = age_details(result["registered_at"])
        result["domain_age"] = details.get("label")
        result["domain_age_days"] = details.get("days")
        result["available"] = bool(result["registered_at"] or data)
        result["source_url"] = endpoint_used
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def _walk_jsonld(data: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        out.append(data)
        if isinstance(data.get("@graph"), list):
            for child in data["@graph"]:
                out.extend(_walk_jsonld(child))
    elif isinstance(data, list):
        for child in data:
            out.extend(_walk_jsonld(child))
    return out


def _jsonld_data(soup: BeautifulSoup) -> Tuple[Optional[str], Optional[str], Optional[str], int, int, List[str]]:
    published = modified = logo = None
    valid = invalid = 0
    types: List[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            raw = script.string or script.get_text()
            data = json.loads(raw)
            valid += 1
        except Exception:
            invalid += 1
            continue
        for node in _walk_jsonld(data):
            published = published or parse_iso(node.get("datePublished"))
            modified = modified or parse_iso(node.get("dateModified"))
            typ = node.get("@type")
            if isinstance(typ, list):
                types.extend([str(x) for x in typ])
            elif typ:
                types.append(str(typ))
            if not logo and node.get("logo"):
                l = node.get("logo")
                if isinstance(l, str):
                    logo = l
                elif isinstance(l, dict):
                    logo = l.get("url") or l.get("contentUrl")
    return published, modified, logo, valid, invalid, list(dict.fromkeys(types))[:30]


def _time_candidates(soup: BeautifulSoup) -> Tuple[Optional[str], Optional[str]]:
    pub = mod = None
    for tag in soup.find_all("time"):
        raw = tag.get("datetime") or tag.get("content") or tag.get_text(" ", strip=True)
        if not raw:
            continue
        hay = " ".join([
            str(tag.get("itemprop") or ""), str(tag.get("class") or ""),
            str(tag.get("id") or ""), str(tag.get("property") or "")
        ]).lower()
        if not pub and any(x in hay for x in ("publish", "datepublished", "entry-date", "posted")):
            pub = parse_iso(raw)
        if not mod and any(x in hay for x in ("modified", "updated", "datemodified")):
            mod = parse_iso(raw)
    return pub, mod


def _meta_content(soup: BeautifulSoup, name: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": re.compile("^" + re.escape(name) + "$", re.I)})
    if not tag:
        tag = soup.find("meta", attrs={"name": re.compile("^" + re.escape(name) + "$", re.I)})
    return (tag.get("content") or "").strip() if tag else None


def extract_page_metadata(url: str, html: str, headers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = headers or {}
    soup = BeautifulSoup(html, "html.parser")
    contacts = _extract_contacts(soup)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1_values = [x.get_text(" ", strip=True) for x in soup.find_all("h1") if x.get_text(" ", strip=True)]
    desc_tags = soup.find_all("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = (desc_tags[0].get("content") or "").strip() if desc_tags else ""

    json_pub, json_mod, json_logo, jsonld_valid, jsonld_invalid, jsonld_types = _jsonld_data(soup)
    time_pub, time_mod = _time_candidates(soup)

    pub_sources = [
        ("meta:article:published_time", parse_iso(_meta_content(soup, "article:published_time"))),
        ("json-ld:datePublished", json_pub),
        ("html:time", time_pub),
    ]
    published_source, published = next(((src, val) for src, val in pub_sources if val), (None, None))

    mod_sources = [
        ("meta:article:modified_time", parse_iso(_meta_content(soup, "article:modified_time"))),
        ("meta:og:updated_time", parse_iso(_meta_content(soup, "og:updated_time"))),
        ("json-ld:dateModified", json_mod),
        ("html:time", time_mod),
        ("http:last-modified", parse_iso(headers.get("last-modified") or headers.get("Last-Modified"))),
    ]
    modified_source, modified = next(((src, val) for src, val in mod_sources if val), (None, None))

    favicons: List[str] = []
    logo_candidates: List[str] = []
    for link in soup.find_all("link", href=True):
        rels = " ".join(link.get("rel") or []).lower()
        href = urljoin(url, link["href"])
        if "icon" in rels:
            favicons.append(href)

    if json_logo:
        logo_candidates.append(urljoin(url, json_logo))
    for img in soup.find_all("img", src=True):
        hay = " ".join([
            " ".join(img.get("class") or []), img.get("id") or "", img.get("alt") or "", img.get("src") or ""
        ]).lower()
        if "logo" in hay or "brand" in hay:
            logo_candidates.append(urljoin(url, img["src"]))
    if not favicons:
        favicons.append(urljoin(url, "/favicon.ico"))

    canon_tags = [x for x in soup.find_all("link", href=True) if "canonical" in [str(v).lower() for v in (x.get("rel") or [])]]
    canonical = urljoin(url, canon_tags[0]["href"]) if canon_tags else None

    html_tag = soup.find("html")
    viewport_tag = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    robots = _meta_content(soup, "robots") or ""
    x_robots = headers.get("x-robots-tag") or headers.get("X-Robots-Tag") or ""

    images = soup.find_all("img")
    missing_alt = sum(1 for img in images if "alt" not in img.attrs)
    empty_alt = sum(1 for img in images if img.has_attr("alt") and not str(img.get("alt") or "").strip())

    # URL age is evidence-based, never fabricated. Publication time is the strongest
    # creation-like signal available on-page; a historical first-seen check is optional.
    url_age = age_details(published)
    age_evidence = None
    if published:
        age_evidence = {
            "date": published,
            "source": published_source,
            "confidence": "high" if published_source in {"meta:article:published_time", "json-ld:datePublished"} else "medium",
            "label": url_age.get("label"),
            "days": url_age.get("days"),
            "meaning": "Published-date evidence, not a registry creation date",
        }

    return {
        "url": url,
        "title": title,
        "h1": h1_values[0] if h1_values else "",
        "h1_values": h1_values,
        "meta_description": desc,
        "meta_description_count": len(desc_tags),
        "canonical": canonical,
        "canonical_count": len(canon_tags),
        "robots": robots,
        "x_robots_tag": str(x_robots),
        "html_lang": (html_tag.get("lang") or "").strip() if html_tag else "",
        "viewport": (viewport_tag.get("content") or "").strip() if viewport_tag else "",
        "published_at": published,
        "published_source": published_source,
        "modified_at": modified,
        "modified_source": modified_source,
        "url_age": age_evidence,
        "favicons": list(dict.fromkeys(favicons))[:8],
        "logo_candidates": list(dict.fromkeys(logo_candidates))[:8],
        "image_alt": {"total": len(images), "missing": missing_alt, "empty": empty_alt},
        "contacts": contacts,
        "structured_data": {
            "valid_jsonld": jsonld_valid,
            "invalid_jsonld": jsonld_invalid,
            "types": jsonld_types,
        },
    }


@lru_cache(maxsize=512)
def wayback_first_seen(url: str) -> Dict[str, Any]:
    result = {
        "url": url,
        "first_seen_at": None,
        "url_age": None,
        "url_age_days": None,
        "source": "Internet Archive Wayback CDX",
        "available": False,
        "confidence": "historical-first-observation",
        "note": "Earliest public archive capture is not the same as the URL's exact creation date.",
    }
    try:
        params = {
            "url": url,
            "output": "json",
            "fl": "timestamp,original,statuscode,mimetype",
            "filter": ["statuscode:200", "mimetype:text/html"],
            "limit": "1",
        }
        r = requests.get("https://web.archive.org/cdx/search/cdx", params=params, timeout=8, headers={"User-Agent": UA})
        if not r.ok:
            result["error"] = "HTTP %s" % r.status_code
            return result
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            return result
        header = data[0]
        row = data[1]
        item = dict(zip(header, row))
        ts = item.get("timestamp")
        if ts and re.match(r"^\d{14}$", ts):
            d = dt.datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc).isoformat()
            age = age_details(d)
            result.update({
                "first_seen_at": d,
                "url_age": age.get("label"),
                "url_age_days": age.get("days"),
                "available": True,
                "capture_url": "https://web.archive.org/web/%s/%s" % (ts, item.get("original") or url),
            })
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
