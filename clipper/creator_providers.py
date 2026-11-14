"""No-cost stock-video providers for Creator Studio.

Adapted selection rules from harry0703/MoneyPrinterTurbo app/services/material.py:
Pexels/Pixabay/Coverr, aspect preference, minimum duration and smallest
usable rendition. This compatibility module deliberately has no paid endpoints.
"""
import json
import os
import urllib.parse
import urllib.request
from . import stock_cache, stock_search_api as legacy

PROVIDERS = {
    "pixabay": {"key": "PIXABAY_API_KEY", "endpoint": legacy.PIXABAY_ENDPOINT},
    "pexels": {"key": "PEXELS_API_KEY", "endpoint": "https://api.pexels.com/videos/search"},
    "coverr": {"key": "COVERR_API_KEY", "endpoint": "https://api.coverr.co/videos"},
}
# Provider-owned CDN hosts. A download must never accept arbitrary external URLs.
HOSTS = {
    "pixabay": ("pixabay.com",),
    "pexels": ("pexels.com", "pexelsvideos.com", "pexelsusercontent.com"),
    "coverr": ("coverr.co", "coverr.com", "coverr.video"),
}


def allowed_url(provider, url):
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        return (provider in HOSTS and parsed.scheme == "https"
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and len(url) <= 2400
                and any(hostname == d or hostname.endswith("." + d) for d in HOSTS[provider])
                and parsed.path.lower().endswith(".mp4"))
    except (TypeError, ValueError):
        return False


def _rendition(v, provider):
    try:
        width, height = int(v.get("width")), int(v.get("height"))
    except (ValueError, TypeError, OverflowError):
        return None
    url = v.get("url") if provider != "pexels" else v.get("link")
    if width < 360 or height < 360 or width * height > 3840 * 2160:
        return None
    if not isinstance(url, str) or not allowed_url(provider, url):
        return None
    return width, height, url


def _pick(variants, provider):
    valid = [v for raw in variants if (v := _rendition(raw, provider))]
    if not valid:
        return None
    # MoneyPrinterTurbo prefers portrait for vertical outputs and the smallest
    # adequate rendition. Use landscape only as centre-crop fallback.
    portrait = [x for x in valid if x[1] > x[0]]
    return min(portrait or valid, key=lambda item: item[0]*item[1])


def parse(provider, data):
    if not isinstance(data, dict):
        return []
    field = "hits" if provider in ("pixabay", "coverr") else "videos"
    hits = data.get(field)
    if not isinstance(hits, list):
        return []
    results = []
    for hit in hits[:50]:
        if not isinstance(hit, dict):
            continue
        try:
            duration = float(hit.get("duration", 0))
        except (TypeError, ValueError):
            continue
        if not 1 <= duration <= 3600:
            continue
        if provider == "pixabay":
            src = (hit.get("videos") or {})
            variants = src.values() if isinstance(src, dict) else []
            source = hit.get("pageURL")
            creator = hit.get("user")
        elif provider == "pexels":
            variants = hit.get("video_files") or []
            source = hit.get("url")
            creator = (hit.get("user") or {}).get("name") if isinstance(hit.get("user"), dict) else None
        else:
            urls = hit.get("urls") or {}
            dims = hit.get("max_width"), hit.get("max_height")
            variants = [{"width": dims[0], "height": dims[1], "url": urls.get("mp4_download")}] if isinstance(urls, dict) else []
            source = hit.get("url")
            creator = hit.get("user")
        choice = _pick(variants, provider)
        if choice is None:
            continue
        w, h, url = choice
        results.append({
            "id": str(hit.get("id") or ""), "duration": duration, "width": w,
            "height": h, "download_url": url, "source_page": str(source or "")[:300],
            "creator": str(creator or "")[:100], "portrait": h > w,
            "provider": provider,
        })
    return results


def configured():
    return [name for name, spec in PROVIDERS.items() if os.environ.get(spec["key"], "").strip()]


def search(provider, query):
    if provider not in PROVIDERS:
        raise ValueError("Unknown free footage provider.")
    key = os.environ.get(PROVIDERS[provider]["key"], "").strip()
    if not key:
        raise ValueError("Free API key missing for " + provider)
    filters = {"aspect": "portrait", "per_page": 20}
    def remote():
        if provider == "pixabay":
            url = PROVIDERS[provider]["endpoint"] + "?" + urllib.parse.urlencode(
                {"key": key, "q": query, "video_type": "film", "per_page": 30})
            headers = {}
        elif provider == "pexels":
            url = PROVIDERS[provider]["endpoint"] + "?" + urllib.parse.urlencode(
                {"query": query, "per_page": 30, "orientation": "portrait"})
            headers = {"Authorization": key}
        else:
            url = PROVIDERS[provider]["endpoint"] + "?" + urllib.parse.urlencode(
                {"query": query, "page_size": 20, "urls": "true", "filter": "is_vertical:true"})
            headers = {"Authorization": "Bearer " + key}
        req = urllib.request.Request(url, headers={**headers, "User-Agent": "LocalVideoEditor/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read(2*1024*1024 + 1)
        if len(payload) > 2*1024*1024:
            raise ValueError("Stock response too large.")
        return parse(provider, json.loads(payload.decode("utf-8")))
    return stock_cache.cached_search("creator_" + provider, query, filters, remote)[0]


class ProviderOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, provider):
        super().__init__()
        self.provider = provider
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(self.provider, newurl):
            raise ValueError("Stock media redirected outside the verified provider CDN.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)
