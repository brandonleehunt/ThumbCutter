"""
Finds your most recent long-form YouTube uploads (not Shorts) and builds the
description footer (recent uploads + social links).

Uses YouTube's public Atom feeds, so no API key or login is needed:
  - UULF<channel id> playlist = the channel's "Videos" tab (no Shorts, no lives)
  - fallback: the all-uploads feed, filtering out anything that is a Short
Standard library only.
"""
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
FEED = "https://www.youtube.com/feeds/videos.xml"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def _http_get(url, timeout=15, headers=None):
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def resolve_channel_id(channel_input):
    """Accepts a @handle, a youtube.com/@handle URL, a /channel/UC... URL, or a bare UC... id."""
    s = (channel_input or "").strip()
    if not s:
        raise RuntimeError("No YouTube channel set. Add your channel in Settings.")
    m = re.search(r"(?:/channel/|^)(UC[\w-]{22})(?:[/?#]|$)", s)
    if m:
        return m.group(1)
    hm = re.search(r"@([\w.\-]+)", s)
    handle = hm.group(1) if hm else s.strip("/ ").split("/")[-1]
    html = _http_get(f"https://www.youtube.com/@{handle}",
                     headers={"Cookie": "SOCS=CAI; CONSENT=YES+1"})
    for pat in (r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
                r'"externalId":"(UC[\w-]{22})"',
                r'"channelId":"(UC[\w-]{22})"'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise RuntimeError(f"Couldn't look up the channel for '{channel_input}'. In Settings, paste your "
                       "channel ID instead (starts with UC, found in YouTube Studio > Settings > "
                       "Channel > Advanced settings), which skips the lookup.")


def parse_feed(xml_text):
    """Atom feed -> list of {id, title, url, published, is_short_link}, newest first."""
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", NS):
        vid = (e.findtext("yt:videoId", namespaces=NS) or "").strip()
        if not vid:
            continue
        href = ""
        for link in e.findall("a:link", NS):
            if link.get("rel") == "alternate":
                href = link.get("href") or ""
        out.append({
            "id": vid,
            "title": (e.findtext("a:title", namespaces=NS) or "").strip(),
            "url": f"https://youtu.be/{vid}",
            "published": (e.findtext("a:published", namespaces=NS) or "").strip(),
            "is_short_link": "/shorts/" in href,
        })
    out.sort(key=lambda v: v["published"], reverse=True)
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _is_short(video_id):
    """youtube.com/shorts/<id> answers 200 for a Short and redirects for a normal video."""
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(f"https://www.youtube.com/shorts/{video_id}",
                                     method="HEAD", headers={"User-Agent": UA})
        return opener.open(req, timeout=10).status == 200
    except Exception:
        return False


def latest_long_form(channel_input, count=2, channel_id=None):
    """Returns (videos, channel_id): the newest `count` regular uploads, never Shorts."""
    cid = channel_id or resolve_channel_id(channel_input)
    vids = []
    try:
        vids = [v for v in parse_feed(_http_get(f"{FEED}?playlist_id=UULF{cid[2:]}"))
                if not v["is_short_link"]]
    except Exception:
        vids = []
    if len(vids) < count:
        vids = []
        for v in parse_feed(_http_get(f"{FEED}?channel_id={cid}")):
            if v["is_short_link"] or _is_short(v["id"]):
                continue
            vids.append(v)
            if len(vids) >= count:
                break
    return vids[:count], cid


def parse_socials(text):
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def build_footer(videos, socials_text):
    """Paste-ready footer: recent uploads, then social links."""
    parts = []
    if videos:
        parts.append("Watch next:\n" + "\n".join(f"{v['title']} — {v['url']}" for v in videos))
    socials = parse_socials(socials_text)
    if socials:
        parts.append("Follow along:\n" + "\n".join(socials))
    return "\n\n".join(parts)
