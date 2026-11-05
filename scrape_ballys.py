#!/usr/bin/env python3
"""Scrape https://www.ballysports.com/ (Bally Sports Live, free FAST) -> ballys.m3u + ballys.xml (XMLTV)."""
import html, json, re, sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import requests

BASE = "https://www.ballysports.com"
GROUP = "Fast Ballys"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
S = requests.Session(); S.headers.update(UA)

def get(url):
    r = S.get(url, timeout=30); r.raise_for_status(); return r.text

def slug_to_name(slug):
    return slug.replace("-", " ").title()

def embedded_json(page):
    """Pull JSON blobs out of Next.js payloads (__NEXT_DATA__ and self.__next_f pushes)."""
    out = []
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try: out.append(json.loads(m.group(1)))
        except json.JSONDecodeError: pass
    for chunk in re.findall(r'self\.__next_f\.push\(\[\d+,"(.*?)"\]\)', page, re.S):
        try: text = json.loads(f'"{chunk}"')
        except json.JSONDecodeError: continue
        for blob in re.findall(r'(\{.*\}|\[.*\])', text):
            try: out.append(json.loads(blob))
            except json.JSONDecodeError: pass
    return out

def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)

def parse_ts(v):
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, timezone.utc)
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None

def find_stream(page):
    m = re.search(r'https?:[^"\'\s\\]+?\.m3u8[^"\'\s\\]*', page.replace("\\u0026", "&").replace("\\/", "/"))
    return m.group(0) if m else None

def main():
    home = get(BASE + "/")
    channels = {}  # slug -> dict

    # "All Channels" links
    for slug in dict.fromkeys(re.findall(r'/channels/([a-z0-9\-]+)', home)):
        channels[slug] = {"slug": slug, "name": slug_to_name(slug), "logo": "", "stream": None, "progs": []}

    # Visit each channel page for stream URL, logo, and schedule
    for slug, ch in channels.items():
        url = f"{BASE}/channels/{slug}"
        try: page = get(url)
        except Exception as e:
            print(f"! {slug}: {e}", file=sys.stderr); continue
        ch["url"] = url
        ch["stream"] = find_stream(page)
        m = re.search(r'<meta property="og:title" content="([^"]+)"', page)
        if m: ch["name"] = html.unescape(m.group(1)).split(" - ")[0].split(" | ")[0].strip()
        m = re.search(r'https://stratosphere-assets\.ballysports\.com/main/channel-logos/[^"\')\s]+', page)
        if m: ch["logo"] = m.group(0)
        for blob in embedded_json(page):
            for d in walk(blob):
                title = d.get("title") or d.get("name")
                start = d.get("startTime") or d.get("start") or d.get("startsAt")
                end = d.get("endTime") or d.get("end") or d.get("endsAt")
                s, e = parse_ts(start), parse_ts(end)
                if title and s and e and e > s:
                    ch["progs"].append({"title": str(title), "start": s, "stop": e,
                                        "desc": d.get("description") or "",
                                        "icon": d.get("image") or d.get("thumbnail") or ""})

    # Fallback EPG: pull "On Now"/"Upcoming" cards from the homepage (no times published -> 4h blocks)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for cid, date, slug in re.findall(r'/watch/channel/(\d+)/(\d\d-\d\d-\d{4})/([a-z0-9\-]+)', home):
        pass  # event links; channel mapping is not exposed in the HTML, so left to JSON schedule above

    for ch in channels.values():
        if not ch["progs"]:
            ch["progs"] = [{"title": f'{ch["name"]} Live', "start": now + timedelta(hours=4 * i),
                            "stop": now + timedelta(hours=4 * (i + 1)), "desc": "", "icon": ""}
                           for i in range(6)]
        ch["progs"].sort(key=lambda p: p["start"])

    # --- M3U ---
    lines = ['#EXTM3U x-tvg-url="ballys.xml"']
    for ch in channels.values():
        cid = f'ballys.{ch["slug"]}'
        lines.append(f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{ch["name"]}" tvg-logo="{ch["logo"]}" '
                     f'group-title="{GROUP}",{ch["name"]}')
        lines.append(ch["stream"] or ch.get("url", f'{BASE}/channels/{ch["slug"]}'))
    open("ballys.m3u", "w", encoding="utf-8").write("\n".join(lines) + "\n")

    # --- XMLTV ---
    esc = lambda s: html.escape(str(s), quote=True)
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")
    x = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="ballys-scraper">']
    for ch in channels.values():
        x.append(f'<channel id="ballys.{ch["slug"]}"><display-name>{esc(ch["name"])}</display-name>'
                 + (f'<icon src="{esc(ch["logo"])}"/>' if ch["logo"] else "") + '</channel>')
    for ch in channels.values():
        for p in ch["progs"]:
            x.append(f'<programme start="{fmt(p["start"])}" stop="{fmt(p["stop"])}" channel="ballys.{ch["slug"]}">'
                     f'<title>{esc(p["title"])}</title>'
                     + (f'<desc>{esc(p["desc"])}</desc>' if p["desc"] else "")
                     + f'<category>{GROUP}</category></programme>')
    x.append("</tv>")
    open("ballys.xml", "w", encoding="utf-8").write("\n".join(x))
    print(f"Wrote {len(channels)} channels")

if __name__ == "__main__":
    main()
