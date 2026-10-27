from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# These are display/optimization heuristics, not Google hard limits.
# Google states that titles and snippets are truncated as needed to fit the device.
TITLE_SOFT_CHARS = (20, 60)
TITLE_SOFT_PX = 580
DESCRIPTION_SOFT_CHARS = (70, 160)
DESCRIPTION_SOFT_PX = 980

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "at",
    "by", "from", "is", "are", "be", "your", "our", "you", "we", "this", "that"
}


def _char_px(ch: str, font_px: float) -> float:
    # Calibrated proportional-width approximation for Arial/Roboto-like SERP fonts.
    if ch == " ":
        factor = 0.28
    elif ch in "ilI|!.,'`:\\;":
        factor = 0.27
    elif ch in "fjrt()[]{}":
        factor = 0.38
    elif ch in "mwMW@%&QO0":
        factor = 0.80
    elif ch.isupper():
        factor = 0.61
    elif ch.isdigit():
        factor = 0.56
    else:
        factor = 0.52
    return factor * font_px


def estimate_serp_width(text: str, font_px: float) -> int:
    return int(round(sum(_char_px(ch, font_px) for ch in (text or ""))))


def _issue(code: str, severity: str, category: str, message: str, recommendation: str,
           field: Optional[str] = None, evidence: Optional[str] = None,
           standard: bool = False) -> Dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "category": category,
        "message": message,
        "recommendation": recommendation,
        "field": field,
        "evidence": evidence,
        "standard": bool(standard),
    }


def _word_repetition(text: str) -> Optional[Tuple[str, int]]:
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", text or "") if len(w) > 2]
    words = [w for w in words if w not in STOPWORDS]
    if len(words) < 5:
        return None
    counts = Counter(words)
    word, count = counts.most_common(1)[0]
    if count >= 4 and count / max(len(words), 1) >= 0.32:
        return word, count
    return None


def audit_page(meta: Dict[str, Any]) -> Dict[str, Any]:
    title = (meta.get("title") or "").strip()
    desc = (meta.get("meta_description") or "").strip()
    h1s = meta.get("h1_values") or ([meta.get("h1")] if meta.get("h1") else [])
    canonical = (meta.get("canonical") or "").strip()
    url = meta.get("url") or ""
    status = meta.get("status")
    robots = (meta.get("robots") or "").lower()
    xrobots = (meta.get("x_robots_tag") or "").lower()
    lang = (meta.get("html_lang") or "").strip()
    viewport = (meta.get("viewport") or "").strip()
    image_alt = meta.get("image_alt") or {}
    structured = meta.get("structured_data") or {}

    issues: List[Dict[str, Any]] = []

    title_chars = len(title)
    title_px = estimate_serp_width(title, 20.0)
    if not title:
        title_status = "fail"
        issues.append(_issue(
            "title_missing", "fail", "title", "Missing <title> element",
            "Add a unique, descriptive and concise page title.", "title", standard=True
        ))
    else:
        title_status = "pass"
        if title_chars < TITLE_SOFT_CHARS[0] or title_chars > TITLE_SOFT_CHARS[1] or title_px > TITLE_SOFT_PX:
            title_status = "warning"
            issues.append(_issue(
                "title_display_length", "warning", "title",
                "Title may be too short or too wide for a typical search-result title link",
                "Keep the title descriptive and concise. This is a display heuristic, not a Google character limit.",
                "title", "%s chars · ~%spx" % (title_chars, title_px)
            ))
        repeated = _word_repetition(title)
        if repeated:
            title_status = "warning"
            issues.append(_issue(
                "title_repetition", "warning", "title",
                "Title repeats the same important term several times",
                "Rewrite the title naturally and avoid keyword stuffing.", "title",
                "%s × %s" % repeated
            ))

    desc_chars = len(desc)
    desc_px = estimate_serp_width(desc, 14.0)
    if not desc:
        description_status = "warning"
        issues.append(_issue(
            "description_missing", "warning", "meta description",
            "Meta description is missing",
            "Add a useful page-specific summary. Search engines may still generate snippets from page content.",
            "meta_description"
        ))
    else:
        description_status = "pass"
        if desc_chars < DESCRIPTION_SOFT_CHARS[0] or desc_chars > DESCRIPTION_SOFT_CHARS[1] or desc_px > DESCRIPTION_SOFT_PX:
            description_status = "warning"
            issues.append(_issue(
                "description_display_length", "warning", "meta description",
                "Meta description may be too short or too wide for a typical search snippet",
                "Write a concise, useful summary. This is a display heuristic, not a Google character limit.",
                "meta_description", "%s chars · ~%spx" % (desc_chars, desc_px)
            ))
        repeated = _word_repetition(desc)
        if repeated:
            description_status = "warning"
            issues.append(_issue(
                "description_repetition", "warning", "meta description",
                "Meta description repeats the same important term heavily",
                "Use natural, page-specific copy instead of a keyword list.",
                "meta_description", "%s × %s" % repeated
            ))

    if status is not None:
        try:
            sc = int(status)
        except Exception:
            sc = 0
        if sc >= 500:
            issues.append(_issue("http_5xx", "fail", "http", "Server error response", "Fix the origin/server error.", evidence="HTTP %s" % sc, standard=True))
        elif sc >= 400:
            issues.append(_issue("http_4xx", "fail", "http", "Client error response", "Restore, redirect, or intentionally remove the URL.", evidence="HTTP %s" % sc, standard=True))
        elif 300 <= sc < 400:
            issues.append(_issue("http_redirect", "warning", "http", "Page resolves through a redirect", "Update internal links and sitemap entries to the final URL where practical.", evidence="HTTP %s" % sc))

    if not h1s:
        issues.append(_issue("h1_missing", "warning", "headings", "No H1/main page heading found", "Use a clear primary heading that describes the page.", "h1"))
    elif len(h1s) > 1:
        issues.append(_issue("h1_multiple", "warning", "headings", "Multiple H1 headings found", "Ensure the page has one clearly dominant main heading, even if the markup technically permits more.", "h1", "%s H1 elements" % len(h1s)))

    canonical_status = "pass"
    if not canonical:
        canonical_status = "warning"
        issues.append(_issue("canonical_missing", "warning", "canonical", "Canonical link is missing", "Add a self-referencing or intentional canonical where appropriate.", "canonical"))
    else:
        try:
            cp = urlparse(canonical)
            if cp.scheme not in {"http", "https"} or not cp.netloc:
                canonical_status = "fail"
                issues.append(_issue("canonical_invalid", "fail", "canonical", "Canonical URL is invalid", "Use one absolute HTTP(S) canonical URL.", "canonical", canonical, standard=True))
        except Exception:
            canonical_status = "fail"
            issues.append(_issue("canonical_invalid", "fail", "canonical", "Canonical URL is invalid", "Use one absolute HTTP(S) canonical URL.", "canonical", canonical, standard=True))
    if (meta.get("canonical_count") or 0) > 1:
        canonical_status = "fail"
        issues.append(_issue("canonical_multiple", "fail", "canonical", "Multiple canonical links found", "Keep only one intended canonical URL.", "canonical", "%s canonical tags" % meta.get("canonical_count"), standard=True))

    noindex = "noindex" in robots or "noindex" in xrobots
    index_status = "warning" if noindex else "pass"
    if noindex:
        issues.append(_issue("noindex", "warning", "indexability", "Page declares noindex", "Confirm that excluding this URL from search results is intentional.", "robots", robots or xrobots, standard=True))

    if not lang:
        issues.append(_issue("html_lang_missing", "warning", "accessibility/seo", "HTML lang attribute is missing", "Declare the page language on the <html> element.", "lang"))
    if not viewport:
        issues.append(_issue("viewport_missing", "warning", "mobile", "Viewport meta tag is missing", "Add a responsive viewport meta tag for mobile rendering.", "viewport"))

    missing_alt = int(image_alt.get("missing", 0) or 0)
    total_images = int(image_alt.get("total", 0) or 0)
    if missing_alt:
        issues.append(_issue(
            "image_alt_missing", "warning", "images",
            "%s image%s missing an alt attribute" % (missing_alt, "" if missing_alt == 1 else "s"),
            "Add descriptive alt text for informative images; use empty alt text for intentionally decorative images.",
            "images", "%s / %s" % (missing_alt, total_images)
        ))

    if structured.get("invalid_jsonld", 0):
        issues.append(_issue(
            "jsonld_invalid", "warning", "structured data",
            "%s JSON-LD block%s could not be parsed" % (structured["invalid_jsonld"], "" if structured["invalid_jsonld"] == 1 else "s"),
            "Validate the JSON-LD syntax and supported structured-data properties.", "structured_data"
        ))

    return {
        "title": {
            "value": title,
            "chars": title_chars,
            "estimated_px": title_px,
            "status": title_status,
            "soft_range": "%s–%s chars / ~%spx" % (TITLE_SOFT_CHARS[0], TITLE_SOFT_CHARS[1], TITLE_SOFT_PX),
            "hard_limit": False,
        },
        "description": {
            "value": desc,
            "chars": desc_chars,
            "estimated_px": desc_px,
            "status": description_status,
            "soft_range": "%s–%s chars / ~%spx" % (DESCRIPTION_SOFT_CHARS[0], DESCRIPTION_SOFT_CHARS[1], DESCRIPTION_SOFT_PX),
            "hard_limit": False,
        },
        "canonical": {"value": canonical, "status": canonical_status},
        "indexability": {"status": index_status, "noindex": noindex, "robots": robots, "x_robots_tag": xrobots},
        "h1_count": len(h1s),
        "issues": issues,
    }


def _add_issue(page: Dict[str, Any], issue: Dict[str, Any]) -> None:
    seo = page.setdefault("seo", {})
    issues = seo.setdefault("issues", [])
    key = (issue.get("code"), issue.get("evidence"))
    if not any((x.get("code"), x.get("evidence")) == key for x in issues if isinstance(x, dict)):
        issues.append(issue)


def finalize_site_seo(pages: List[Dict[str, Any]], redirects: List[Dict[str, Any]], sitemap_urls: Optional[set] = None) -> Dict[str, Any]:
    sitemap_urls = sitemap_urls or set()
    url_map = {p.get("url"): p for p in pages if p.get("url")}
    title_map = defaultdict(list)
    desc_map = defaultdict(list)
    redirect_from = {x.get("from"): x.get("to") for x in redirects if x.get("from")}

    for p in pages:
        if not p.get("seo"):
            p["seo"] = audit_page(p)
        t = (p.get("title") or "").strip().casefold()
        d = (p.get("meta_description") or "").strip().casefold()
        if t:
            title_map[t].append(p)
        if d:
            desc_map[d].append(p)

    for group in title_map.values():
        if len(group) > 1:
            urls = [p.get("url") for p in group]
            for p in group:
                p.setdefault("seo", {}).setdefault("title", {})["status"] = "fail"
                _add_issue(p, _issue(
                    "title_duplicate", "fail", "title", "Duplicate page title found",
                    "Give indexable pages distinct titles that describe their individual content.",
                    "title", "%s pages share this title" % len(group), standard=True
                ))

    for group in desc_map.values():
        if len(group) > 1:
            for p in group:
                if p.setdefault("seo", {}).setdefault("description", {}).get("status") == "pass":
                    p["seo"]["description"]["status"] = "warning"
                _add_issue(p, _issue(
                    "description_duplicate", "warning", "meta description", "Duplicate meta description found",
                    "Use a page-specific description where practical.", "meta_description",
                    "%s pages share this description" % len(group)
                ))

    for p in pages:
        url = p.get("url")
        internal = p.get("internal_links") or []
        broken = []
        redirected = []
        for link in internal:
            target = url_map.get(link)
            if target and int(target.get("status") or 0) >= 400:
                broken.append(link)
            elif link in redirect_from:
                redirected.append(link)
        if broken:
            _add_issue(p, _issue(
                "internal_broken_links", "fail", "links", "Broken internal links found",
                "Update or remove links that point to 4xx/5xx pages.", "links",
                "%s broken link%s" % (len(set(broken)), "" if len(set(broken)) == 1 else "s"), standard=True
            ))
        if redirected:
            _add_issue(p, _issue(
                "internal_redirect_links", "warning", "links", "Internal links point through redirects",
                "Link directly to the final destination URL where practical.", "links",
                "%s redirected link%s" % (len(set(redirected)), "" if len(set(redirected)) == 1 else "s")
            ))
        if url in sitemap_urls and p.get("seo", {}).get("indexability", {}).get("noindex"):
            _add_issue(p, _issue(
                "sitemap_noindex_conflict", "fail", "indexability", "Noindex page is listed in a sitemap",
                "Remove the URL from the sitemap or remove noindex if the page should be indexed.",
                "robots", "sitemap + noindex", standard=True
            ))

    # Inlink/orphan calculation using discovered internal-link graph.
    inlinks = Counter()
    for p in pages:
        for link in set(p.get("internal_links") or []):
            if link in url_map:
                inlinks[link] += 1
    homepage_urls = {p.get("url") for p in pages if urlparse(p.get("url") or "").path in {"", "/"}}
    for p in pages:
        p["inlinks"] = inlinks[p.get("url")]
        if p.get("url") not in homepage_urls and p.get("url") in sitemap_urls and inlinks[p.get("url")] == 0:
            _add_issue(p, _issue(
                "orphan_sitemap_page", "warning", "links", "Sitemap page has no discovered internal inlinks",
                "Add crawlable internal links from relevant pages if this page should be part of the site hierarchy.",
                "links"
            ))

    counts = Counter()
    category_counts = Counter()
    issue_rows = []
    for p in pages:
        for issue in p.get("seo", {}).get("issues", []):
            sev = issue.get("severity", "warning")
            counts[sev] += 1
            category_counts[issue.get("category", "other")] += 1
            issue_rows.append({"url": p.get("url"), "title": p.get("title"), **issue})
        # Keep legacy issue strings synchronized for older UI/export consumers.
        p["issues"] = [i.get("message") for i in p.get("seo", {}).get("issues", [])]

    total_checks = max(1, len(pages) * 12)
    weighted = counts["fail"] * 2.5 + counts["warning"] * 0.5
    score = max(0, min(100, int(round(100 - (weighted / total_checks * 100)))))
    return {
        "score": score,
        "passed_pages": sum(1 for p in pages if not p.get("seo", {}).get("issues")),
        "failed": counts["fail"],
        "warnings": counts["warning"],
        "info": counts["info"],
        "issues_total": sum(counts.values()),
        "categories": dict(category_counts),
        "issues": issue_rows,
        "note": "Length checks are display heuristics. Google does not publish fixed title or meta-description character limits.",
    }
