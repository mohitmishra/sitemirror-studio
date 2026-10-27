from __future__ import annotations
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse


def _rel_key(url: str) -> str:
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return path + ("?" + p.query if p.query else "")


def compare_projects(a: Path, b: Path) -> dict:
    pa = json.loads((a / "pages.json").read_text(encoding="utf-8"))
    pb = json.loads((b / "pages.json").read_text(encoding="utf-8"))
    ma = {_rel_key(x["url"]): x for x in pa}
    mb = {_rel_key(x["url"]): x for x in pb}
    keys = sorted(set(ma) | set(mb))
    rows = []
    for k in keys:
        x, y = ma.get(k), mb.get(k)
        if x and not y:
            status = "missing_target"
        elif y and not x:
            status = "new_target"
        else:
            diffs = [field for field in ("title", "h1", "meta_description") if (x.get(field) or "") != (y.get(field) or "")]
            status = "changed" if diffs else "match"
        rows.append({"path": k, "status": status, "source": x, "target": y})
    return {
        "summary": {
            "source_pages": len(pa), "target_pages": len(pb),
            "matches": sum(r["status"] == "match" for r in rows),
            "changed": sum(r["status"] == "changed" for r in rows),
            "missing": sum(r["status"] == "missing_target" for r in rows),
            "new": sum(r["status"] == "new_target" for r in rows),
        },
        "rows": rows,
    }
