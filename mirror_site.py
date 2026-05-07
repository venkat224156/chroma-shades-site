#!/usr/bin/env python3
"""
Simple static mirroring script for a single-domain marketing site.

Downloads:
- HTML pages (same-origin)
- linked assets referenced by href/src/srcset and common data-* attrs

Writes output to ./docs (GitHub Pages friendly) and rewrites links in mirrored HTML to be relative.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


ASSET_ATTRS = {
    "href",
    "src",
    "srcset",
    "data-src",
    "data-href",
    "data-background",
    "data-bg",
}


def normalize_url(url: str) -> str:
    # Strip fragments; keep query (may matter for cache-busting filenames)
    parsed = urllib.parse.urlsplit(url)
    parsed = parsed._replace(fragment="")
    return urllib.parse.urlunsplit(parsed)


def is_http_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def same_origin(a: str, b: str) -> bool:
    pa = urllib.parse.urlsplit(a)
    pb = urllib.parse.urlsplit(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def url_to_local_path(url: str, out_dir: Path) -> Path:
    """
    Map a URL to a file path under out_dir preserving path and query.
    """
    u = urllib.parse.urlsplit(url)
    path = u.path or "/"
    # GitHub Pages / static servers treat "directories" as routes:
    #   /about-us  -> /about-us/index.html
    #   /about-us/ -> /about-us/index.html
    # Only keep "file-like" paths (with extensions) as direct files.
    ext = os.path.splitext(path)[1].lower()
    if path.endswith("/") or ext == "":
        if not path.endswith("/"):
            path = path + "/"
        path = path + "index.html"
    # include query in filename for uniqueness (e.g. style.css?v=123)
    if u.query:
        safe_q = re.sub(r"[^a-zA-Z0-9._-]+", "_", u.query)[:80]
        base, ext = os.path.splitext(path)
        path = f"{base}__q_{safe_q}{ext or ''}"
    # ensure no leading slash when joining
    rel = path.lstrip("/")
    return out_dir / rel


def ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def guess_content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in {".html", ".htm"}:
        return "text/html"
    if ext in {".css"}:
        return "text/css"
    if ext in {".js", ".mjs"}:
        return "application/javascript"
    if ext in {".json"}:
        return "application/json"
    if ext in {".svg"}:
        return "image/svg+xml"
    if ext in {".png"}:
        return "image/png"
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext in {".webp"}:
        return "image/webp"
    if ext in {".gif"}:
        return "image/gif"
    if ext in {".woff"}:
        return "font/woff"
    if ext in {".woff2"}:
        return "font/woff2"
    if ext in {".ttf"}:
        return "font/ttf"
    if ext in {".otf"}:
        return "font/otf"
    return "application/octet-stream"


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        for k, v in attr_map.items():
            if k not in ASSET_ATTRS:
                continue
            if not v:
                continue
            if k == "srcset":
                # srcset: "a.jpg 1x, b.jpg 2x" -> extract URLs
                for part in v.split(","):
                    url_part = part.strip().split(" ")[0].strip()
                    if url_part:
                        self.urls.append(url_part)
            else:
                self.urls.append(v.strip())


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    data: bytes


def fetch(url: str, user_agent: str, timeout: float) -> FetchResult:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "*/*",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        data = resp.read()
        return FetchResult(url=url, status=status, content_type=ctype, data=data)


def make_relative(from_url: str, to_url: str) -> str:
    """
    Compute a relative link from from_url's local file to to_url's local file.
    Preserves query mapping (handled in url_to_local_path) by embedding query in filename.
    """
    f = urllib.parse.urlsplit(from_url)
    t = urllib.parse.urlsplit(to_url)

    from_path = f.path or "/"
    from_ext = os.path.splitext(from_path)[1].lower()
    if from_path.endswith("/") or from_ext == "":
        if not from_path.endswith("/"):
            from_path = from_path + "/"
        from_path = from_path + "index.html"

    to_path = t.path or "/"
    to_ext = os.path.splitext(to_path)[1].lower()
    if to_path.endswith("/") or to_ext == "":
        if not to_path.endswith("/"):
            to_path = to_path + "/"
        to_path = to_path + "index.html"

    # incorporate query into target path file name to match url_to_local_path
    if t.query:
        safe_q = re.sub(r"[^a-zA-Z0-9._-]+", "_", t.query)[:80]
        base, ext = os.path.splitext(to_path)
        to_path = f"{base}__q_{safe_q}{ext or ''}"

    rel_file = posixpath.relpath(to_path.lstrip("/"), start=posixpath.dirname(from_path.lstrip("/")))

    # Prefer "pretty" directory links for HTML pages, so navigation hits /route/
    # instead of downloading an extensionless file.
    if rel_file.endswith("/index.html") and rel_file != "index.html":
        return rel_file[: -len("index.html")]
    return rel_file


_ATTR_RE = re.compile(
    r"""(?P<attr>\b(?:href|src|data-src|data-href|data-background|data-bg)\s*=\s*)(?P<q>["'])(?P<val>.*?)(?P=q)""",
    re.IGNORECASE | re.DOTALL,
)

_SRCSET_RE = re.compile(
    r"""(?P<attr>\bsrcset\s*=\s*)(?P<q>["'])(?P<val>.*?)(?P=q)""",
    re.IGNORECASE | re.DOTALL,
)


def _rewrite_attr_url(val: str, page_url: str, start_url: str) -> str:
    v = val.strip()
    if not v or v.startswith("#") or v.startswith("mailto:") or v.startswith("tel:") or v.startswith("javascript:"):
        return val
    abs_u = normalize_url(urllib.parse.urljoin(page_url, v))
    if not is_http_url(abs_u) or not same_origin(start_url, abs_u):
        return val
    return make_relative(page_url, abs_u)


def rewrite_html_links(html: str, page_url: str, start_url: str) -> str:
    """
    Rewrite *only* attribute URL values to relative (safe for static hosting).
    Important: never do global string replace, because links like href="/"
    would corrupt the whole document.
    """

    def repl_attr(m: re.Match) -> str:
        attr = m.group("attr")
        q = m.group("q")
        val = m.group("val")
        new_val = _rewrite_attr_url(val, page_url, start_url)
        return f"{attr}{q}{new_val}{q}"

    def repl_srcset(m: re.Match) -> str:
        attr = m.group("attr")
        q = m.group("q")
        raw = m.group("val")
        parts: list[str] = []
        for part in raw.split(","):
            p = part.strip()
            if not p:
                continue
            segs = p.split()
            url_part = segs[0]
            rest = " ".join(segs[1:])
            new_url = _rewrite_attr_url(url_part, page_url, start_url).strip()
            parts.append((new_url + (" " + rest if rest else "")).strip())
        return f"{attr}{q}{', '.join(parts)}{q}"

    out = _ATTR_RE.sub(repl_attr, html)
    out = _SRCSET_RE.sub(repl_srcset, out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="Start URL, e.g. https://example.com/")
    ap.add_argument("--out", default="docs", help="Output folder (default: docs)")
    ap.add_argument("--max-pages", type=int, default=50, help="Max HTML pages to crawl")
    ap.add_argument("--max-assets", type=int, default=500, help="Max assets to download")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--sleep", type=float, default=0.15, help="Politeness delay between requests")
    ap.add_argument("--user-agent", default="Mozilla/5.0 (static-mirror)")
    args = ap.parse_args()

    start = normalize_url(args.start)
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    page_queue: deque[str] = deque([start])
    pages_downloaded = 0
    assets_downloaded = 0

    def enqueue(u: str) -> None:
        u = normalize_url(u)
        if u in seen:
            return
        if not is_http_url(u):
            return
        if not same_origin(start, u):
            return
        seen.add(u)
        page_queue.append(u)

    # Always include the start URL in seen
    seen.add(start)

    # Separate queue for assets to ensure pages are prioritized
    asset_queue: deque[str] = deque()
    asset_seen: set[str] = set()

    def enqueue_asset(u: str, base: str) -> None:
        abs_u = normalize_url(urllib.parse.urljoin(base, u))
        if abs_u in asset_seen:
            return
        if not is_http_url(abs_u):
            return
        if not same_origin(start, abs_u):
            return
        asset_seen.add(abs_u)
        asset_queue.append(abs_u)

    # Download pages and discover links/assets
    while page_queue and pages_downloaded < args.max_pages:
        page_url = page_queue.popleft()
        try:
            res = fetch(page_url, args.user_agent, args.timeout)
        except Exception as e:
            print(f"[page] FAIL {page_url} ({e})", file=sys.stderr)
            continue

        # Save page as HTML regardless of Content-Type (Siteswan sometimes serves without it)
        local_path = url_to_local_path(page_url, out_dir)
        ensure_parent(local_path)
        raw = res.data
        # Try decode as utf-8 with fallback
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")

        # Extract links/assets from this page
        extractor = LinkExtractor()
        extractor.feed(text)

        for found in extractor.urls:
            if found.startswith("mailto:") or found.startswith("tel:") or found.startswith("javascript:"):
                continue
            abs_u = normalize_url(urllib.parse.urljoin(page_url, found))
            if not is_http_url(abs_u) or not same_origin(start, abs_u):
                continue
            # Heuristic: treat paths with extension as assets unless .html
            p = urllib.parse.urlsplit(abs_u).path
            ext = os.path.splitext(p)[1].lower()
            if ext and ext not in {".html", ".htm"}:
                enqueue_asset(abs_u, page_url)
            else:
                enqueue(abs_u)

        # Rewrite same-origin absolute links to relative for offline hosting
        rewritten = rewrite_html_links(text, page_url, start)
        local_path.write_text(rewritten, encoding="utf-8")
        pages_downloaded += 1
        print(f"[page] OK   {page_url} -> {local_path.relative_to(out_dir)}")
        time.sleep(args.sleep)

    # Download assets
    while asset_queue and assets_downloaded < args.max_assets:
        asset_url = asset_queue.popleft()
        try:
            res = fetch(asset_url, args.user_agent, args.timeout)
        except Exception as e:
            print(f"[asset] FAIL {asset_url} ({e})", file=sys.stderr)
            continue

        local_path = url_to_local_path(asset_url, out_dir)
        ensure_parent(local_path)
        local_path.write_bytes(res.data)
        assets_downloaded += 1
        print(f"[asset] OK   {asset_url} -> {local_path.relative_to(out_dir)}")
        time.sleep(args.sleep)

    # Ensure a 404 page exists (GitHub Pages fallback). Copy index.html if missing.
    index_path = url_to_local_path(start, out_dir)
    not_found_path = out_dir / "404.html"
    if index_path.exists() and not not_found_path.exists():
        not_found_path.write_bytes(index_path.read_bytes())

    # Write a minimal README for next steps
    (out_dir / "MIRROR_SUMMARY.txt").write_text(
        "\n".join(
            [
                f"Start URL: {start}",
                f"Pages downloaded: {pages_downloaded}",
                f"Assets downloaded: {assets_downloaded}",
                "",
                "Next:",
                "  - Preview locally:  python3 -m http.server 5173 -d docs",
                "  - If paths look off, re-run with a higher --max-pages/--max-assets",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Done. Output in: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

