import base64
import hashlib
import html as htmllib
import inspect
import io
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import quote, urlparse

import psutil
import requests
from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat
from pypresence import Presence

try:
    from icoextract import IconExtractor  # reads the icon out of an .exe
except ImportError:
    IconExtractor = None

try:
    import pystray  # system tray icon
except Exception:
    pystray = None

try:
    import winreg  # start with Windows
except ImportError:
    winreg = None

import socket
import urllib3.util.connection as _urllib3_connection

_AUTO_FAMILY = _urllib3_connection.allowed_gai_family


def apply_network(mode):
    """auto / ipv4 / ipv6. Some networks stall for 20-40 s on IPv6 before falling back, so it can be forced."""
    _urllib3_connection.allowed_gai_family = {
        "ipv4": lambda: socket.AF_INET, "ipv6": lambda: socket.AF_INET6}.get(mode, _AUTO_FAMILY)


APP_NAME = "ANY-PRESENCE"
WINDOW_SIZE = (860, 580)  # the window has one fixed size
APP_VERSION = "1.2.1" # app version @_@
DEFAULT_APP_ID = "1557320467474292756"  # used unless the person adds their own in Settings
UPDATE_REPO = "ih3l/ANY-PRESENCE-releases"  # public repo used to check for the updates
UPDATE_ASSET = "ANY-PRESENCE.exe"
START_HIDDEN = "--startup" in sys.argv  # set by the "start with Windows" entry

# ---------------------------------------------------------------- engine (shared with the desktop version)
HEADERS = {"User-Agent": f"ANY-PRESENCE/{APP_VERSION} (Discord Rich Presence tool)"}


_BASE = Path(os.environ.get("APPDATA") or Path.home() / ".config")


CONFIG_PATH = _BASE / APP_NAME / "config.json"


OLD_CONFIG_PATH = _BASE / "GamePresence" / "config.json"  # earlier prototype, in case somebody uses and early version off the app before updating @_@
BANNER_DIR = _BASE / APP_NAME / "banners"  # small copies of the Recent banners, so they show at once


DISCORD_CLIENTS = ("discord", "vesktop", "vencord", "legcord", "armcord")  # process names of Discord and its mods


def clean_buttons(raw):
    """Discord allows two buttons, each with a label (up to 32 characters) and an http(s) link."""
    out = []
    for b in (raw if isinstance(raw, list) else [])[:6]:
        if not isinstance(b, dict) or len(out) == 2:
            continue
        label, url = str(b.get("label") or "").strip()[:32], str(b.get("url") or "").strip()
        if label and url.lower().startswith(("http://", "https://")) and len(url) <= 512:
            out.append({"label": label, "url": url})
    return out


def clean_env():
    """The environment for programs this app starts: without PyInstaller's own variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_MEI", "_PYI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def default_config():
    return {
        "app_id": "",
        "show_timer": True,
        "auto_stop": True,
        "nsfw": False,
        "close_action": "quit",  # "quit" or "background"
        "auto_detect": True,  # start the presence automatically when a saved app is running
        "theme": "rose",
        "custom_theme": None,  # {"name": ..., "colors": [background, surface, accent]} made in Settings
        "check_updates": True,
        "update_later": "",  # version the person chose "Later" for: its window doesn't open by itself again
        "network": "auto",  # auto / ipv4 / ipv6
        "imgbb_key": "",  # free API key from api.imgbb.com: icons are hosted there
        "icon_cache": {},  # source -> hosted URL, so the same image is only uploaded once
        "recent": [],  # games the presence was started for (newest first)
        "playtime": {},  # program name -> seconds played. Kept even if the game leaves Recent
        "apps": [],  # saved apps (see save_current_app)
        "sources": {},  # filled in below once SOURCES exists
    }


THEME_IDS = ("rose", "violet", "graphite", "paper", "blush", "custom")


def clean_custom_theme(ct):
    """The custom theme if it is well formed ({"name", "colors": three #RRGGBB}), else None."""
    if not isinstance(ct, dict) or not isinstance(ct.get("colors"), list) or len(ct["colors"]) != 3:
        return None
    colors = [str(c).strip().upper() for c in ct["colors"]]
    if not all(re.fullmatch(r"#[0-9A-F]{6}", c) for c in colors):
        return None
    return {"name": str(ct.get("name") or "").strip()[:16] or "Custom", "colors": colors}


def load_config():
    cfg = default_config()
    cfg["sources"] = {k: k not in DEFAULT_OFF for k in SOURCES}
    for path in (CONFIG_PATH, OLD_CONFIG_PATH):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            try:  # unreadable file: keep a copy instead of silently overwriting it with an empty one
                path.replace(path.with_name(path.stem + ".broken.json"))
            except OSError:
                pass
            continue
        for key in ("app_id", "show_timer", "auto_stop", "nsfw", "close_action", "auto_detect", "theme", "network", "check_updates", "update_later"):
            if key in data:
                cfg[key] = data[key]
        if cfg["close_action"] not in ("quit", "background"):
            cfg["close_action"] = "quit"
        if cfg["network"] not in ("auto", "ipv4", "ipv6"):
            cfg["network"] = "auto"
        if isinstance(data.get("imgbb_key"), str):
            cfg["imgbb_key"] = data["imgbb_key"].strip()
        if isinstance(data.get("icon_cache"), dict):
            cfg["icon_cache"] = {k: v for k, v in data["icon_cache"].items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(data.get("recent"), list):
            cfg["recent"] = [r for r in data["recent"] if isinstance(r, dict) and isinstance(r.get("key"), str)
                             and isinstance(r.get("title"), str) and r.get("proc")]
        if isinstance(data.get("playtime"), dict):
            cfg["playtime"] = {k.lower(): float(v) for k, v in data["playtime"].items()
                               if isinstance(k, str) and isinstance(v, (int, float)) and v > 0}
        for r in cfg["recent"]:  # games from before the playtime list existed
            proc, played = str(r.get("proc") or "").lower(), r.get("played") or 0
            if proc and isinstance(played, (int, float)) and played > cfg["playtime"].get(proc, 0):
                cfg["playtime"][proc] = float(played)
        ct = clean_custom_theme(data.get("custom_theme"))
        if ct:
            cfg["custom_theme"] = ct
        if cfg["theme"] not in THEME_IDS or (cfg["theme"] == "custom" and not cfg["custom_theme"]):
            cfg["theme"] = "rose"  # a theme that was removed, or a custom one that is missing C_C
        apps = data.get("apps", [])
        if isinstance(apps, list):
            cfg["apps"] = [a for a in apps if isinstance(a, dict)
                           and isinstance(a.get("proc"), str) and isinstance(a.get("title"), str)]
        for key, value in data.get("sources", {}).items():
            if key in cfg["sources"]:
                cfg["sources"][key] = bool(value)
        break
    return cfg


_SAVE_LOCK = threading.Lock()


def save_config(cfg):
    with _SAVE_LOCK:  # several threads save (searches, uploads, the monitor): one at a time
        for attempt in range(2):
            try:
                CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp = CONFIG_PATH.with_suffix(".tmp")
                tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                tmp.replace(CONFIG_PATH)
                return
            except Exception:  # e.g. another thread changed the data mid-write: try once more
                time.sleep(0.05)


@dataclass
class Result:
    title: str
    icon: str | None  # URL sent to Discord
    source: str
    thumb: str | None = None  # URL used for the small preview in the list
    url: str | None = None  # the game's page (for the presence buttons)
    launch: str | None = None  # how the app can start it ("steam:<id>")
    banner: str | None = None  # wide picture for the Recent tab
    ref: dict | None = None  # which store page this is ({"s": "steam", "id": ...}), for the game info window


def http_get(url, **kw):
    headers = {**HEADERS, **kw.pop("headers", {})}
    timeout = kw.pop("timeout", 8)
    r = requests.get(url, headers=headers, timeout=timeout, **kw)
    r.raise_for_status()
    return r


def banner_file(url):
    return BANNER_DIR / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".jpg")


def banner_data(path):
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


def fetch_banner(url):
    """Downloads a banner and keeps a small copy on disk.""" # this use almost no memory, if you have 500 games it could add up to like 20-40MB maybe, I don't know, not good at maths
    r = http_get(url, timeout=15)
    if len(r.content) > 12_000_000:
        raise ValueError("too big")
    img = Image.open(io.BytesIO(r.content))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (24, 18, 22))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img.thumbnail((640, 640))
    path = banner_file(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    img.save(tmp, "JPEG", quality=82, optimize=True)
    tmp.replace(path)
    return path


def clip_text(text, limit=128):
    """Discord rejects texts longer than 128 characters (window titles can be long)."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


STOPWORDS = {"the", "of", "a", "an", "and", "or", "to", "in", "on", "for"}


def normalize_query(term: str) -> str:
    """Drop punctuation/symbols that trip up store searches (colons, dashes, (tm) ...)."""
    s = re.sub(r"['\u2019`]", "", term)
    s = re.sub(r"[^\w\s]|_", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def squash(text: str) -> str:
    """Lower case without spaces or symbols: "SiNiSistar 2" and "SiNiSistar2" come out the same.""" # peak R-18 game imo but not for everyone for sure
    return re.sub(r"[\W_]+", "", (text or "").lower())


def significant_tokens(term: str):
    words = normalize_query(term).lower().split()
    tokens = [t for t in words if len(t) >= 3 and t not in STOPWORDS]
    whole = squash(term)
    if len(words) > 1 and len(whole) >= 5 and whole not in tokens:  # the query written as one word
        tokens.append(whole)
    return tokens


def title_matches(title: str, tokens) -> bool:
    """True if a word of the title starts with a query word, or the title contains a long query word
    (so "SiNiSistar2" finds "SiNiSistar 2" and the other way round)."""
    if not tokens:
        return True
    words = normalize_query(title).lower().split()
    if any(w.startswith(t) for w in words for t in tokens):
        return True
    flat = squash(title)
    return any(len(t) >= 5 and t in flat for t in tokens)


def closeness(title: str, term: str) -> int:
    """0 = same title, 1 = one starts with the other, 2 = one contains the other, 3 = anything else."""
    a, b = squash(title), squash(term)
    if not a or not b:
        return 3
    if a == b:
        return 0
    if a.startswith(b) or b.startswith(a):
        return 1
    return 2 if (b in a or (len(a) >= 4 and a in b)) else 3


def query_variants(term: str):
    """The cleaned full query first, then shorter versions (used if nothing is found)."""
    base = normalize_query(term)
    if not base:
        return []
    words = base.split()
    head = normalize_query(re.split(r"\s[-\u2013\u2014~]\s|:\s", term, maxsplit=1)[0])  # main title before ": subtitle"
    spaced = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", base)  # "Game1" -> "Game 1"
    joined = re.sub(r"(?<=[^\W\d_])\s+(?=\d{1,2}\b)", "", base)  # "Game 1" -> "Game1"
    sw = spaced.split()
    first_word = sw[0] if len(sw) > 1 and len(sw[0]) >= 4 and sw[0].lower() not in STOPWORDS else ""  # just the name, no number
    candidates = [base, spaced, joined, head] + [" ".join(words[:n]) for n in (6, 4, 2) if len(words) > n] + [first_word]
    unique = [normalize_query(q) for q in candidates if q]
    unique = [q for q in dict.fromkeys(unique) if q]
    unique.sort(key=lambda q: -len(q.split()))  # stable: most specific first
    return unique[:7]


def run_source(fn, term, nsfw):
    """Search one source; if the full query finds nothing, retry with shorter queries.
    Returns (results, hidden, query_used_if_it_was_shortened)."""
    last_error, answered = None, False
    for i, query in enumerate(query_variants(term) or [term]):
        if i:
            time.sleep(0.25)  # a pause between tries, so a store doesn't take them for a flood
        try:
            results, hidden = fn(query, nsfw)
        except Exception as e:  # one failed try doesn't end the search: the next version may work 0_0
            last_error = e
            continue
        answered = True
        if results or hidden:
            results = sorted(results, key=lambda r: closeness(r.title, term))  # best matches first
            return results, hidden, (query if i else None)
    if last_error is not None and not answered:
        raise last_error
    return [], 0, None


def why(e):
    """A few words for why a store couldn't be reached."""
    code = getattr(getattr(e, "response", None), "status_code", None)
    return f"HTTP {code}" if code else (str(e) if isinstance(e, RuntimeError) else type(e).__name__)


STEAM_SEARCH = "https://store.steampowered.com/api/storesearch/"


STEAM_DETAILS = "https://store.steampowered.com/api/appdetails"


STEAM_POSTER = "https://cdn.cloudflare.steamstatic.com/steam/apps/{}/library_600x900.jpg"


def url_exists(url):
    try:
        headers = dict(HEADERS, Referer="https://www.dlsite.com/") if "dlsite" in url else HEADERS
        return requests.head(url, headers=headers, timeout=5, allow_redirects=True).status_code == 200
    except Exception:
        return False


STEAM_IMG = "https://cdn.cloudflare.steamstatic.com/steam/apps/{}/header.jpg"


STEAM_ADULT_DESCRIPTORS = {3, 4}  # adult-only sexual content / frequent nudity C_C


def steam_is_adult(appid) -> bool:
    """True if Steam flags the app as adult - or if we can't tell (fail closed)."""
    try:
        d = http_get(STEAM_DETAILS, params={"appids": appid, "filters": "content_descriptors"}).json()
        entry = d.get(str(appid), {})
        if not entry.get("success"):
            return True
        data = entry.get("data")
        ids = data.get("content_descriptors", {}).get("ids", []) if isinstance(data, dict) else []
        return bool(set(ids) & STEAM_ADULT_DESCRIPTORS)
    except Exception:
        return True


STEAM_FALLBACKS = (STEAM_POSTER, STEAM_IMG,
                   "https://cdn.cloudflare.steamstatic.com/steam/apps/{}/capsule_616x353.jpg",
                   "https://cdn.cloudflare.steamstatic.com/steam/apps/{}/capsule_231x87.jpg")


def steam_icon(item):
    """The best picture that exists for this game: poster, header, then the capsule banners."""
    for pattern in STEAM_FALLBACKS:
        url = pattern.format(item["id"])
        if url_exists(url):
            return url
    return None


def steam_banner_options(appid):
    """Wide pictures Steam has for a game: hero, header, capsule and the store screenshots."""
    base = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/"
    fixed = [base + n for n in ("library_hero.jpg", "header.jpg", "capsule_616x353.jpg")]
    with ThreadPoolExecutor(max_workers=3) as ex:
        urls = [u for u, ok in zip(fixed, ex.map(url_exists, fixed)) if ok]
    try:
        data = http_get("https://store.steampowered.com/api/appdetails",
                        params={"appids": appid, "filters": "screenshots"}).json()
        shots = (data.get(str(appid)) or {}).get("data", {}).get("screenshots", [])
        urls += [x["path_full"] for x in shots[:6] if x.get("path_full")]
    except Exception:
        pass
    return urls


def dlsite_banner_options(icon):
    """The product's other pictures (_img_smp1, _img_smp2, ...) next to its main one."""
    if not icon or "_img_main" not in icon:
        return [icon] if icon else []
    cands = [icon.replace("_img_main", f"_img_smp{n}") for n in range(1, 7)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        found = [u for u, ok in zip(cands, ex.map(url_exists, cands)) if ok]
    return found + [icon]


def _closest(items, title, key=lambda x: x, fallback=False):
    """The item whose title is closest to the wanted one (None if nothing is close, unless fallback)."""
    tokens = significant_tokens(title)
    ranked = sorted(items, key=lambda it: closeness(key(it), title))
    for it in ranked:
        if closeness(key(it), title) < 3 or title_matches(key(it), tokens):
            return it
    return ranked[0] if (fallback and ranked) else None


def _steam_urls(title, launch):
    appid = launch[6:] if str(launch or "").startswith("steam:") and launch[6:].isdigit() else None
    for query in ([] if appid else query_variants(title)):  # only the store search, none of the slow extras
        items = http_get(STEAM_SEARCH, params={"term": query, "cc": "us", "l": "en"}).json().get("items", [])[:6]
        hit = _closest(items, title, key=lambda i: i.get("name", ""))
        if hit:
            appid = str(hit["id"])
            break
    return steam_banner_options(appid) if appid else []


def _gog_urls(title, nsfw):
    res, _, _ = run_source(search_gog, title, nsfw)
    hit = _closest(res, title, key=lambda r: r.title)
    return [hit.banner or hit.thumb] if hit and (hit.banner or hit.thumb) else []


def _dlsite_hit(title, nsfw):
    """The DLsite product closest to a title: (product id, title, picture, site), or None."""
    sites = ["home", "soft"] + (["maniax", "pro"] if nsfw else [])

    def fetch(args):
        site, query = args
        try:
            r = http_get(f"https://www.dlsite.com/{site}/fsr/ajax/=/language/jp/keyword/{quote(query, safe='')}/per_page/30/page/1",
                         headers={"Accept-Language": "ja,en;q=0.8"}, cookies={"adultchecked": "1"} if nsfw else None)
            try:
                markup = r.json().get("search_result", "")
            except ValueError:
                markup = r.text
            return [(*it, site) for it in parse_dlsite(markup)]
        except Exception:
            return []

    for query in query_variants(title):
        with ThreadPoolExecutor(max_workers=len(sites)) as ex:
            items = [it for found in ex.map(fetch, [(site, query) for site in sites]) for it in found if it[2]]
        hit = _closest(items, title, key=lambda it: it[1], fallback=True)  # DLsite's own ranking decides when titles differ
        if hit:
            return hit
    return None


def _dlsite_urls(title, nsfw):
    hit = _dlsite_hit(title, nsfw)
    if not hit:
        return []
    plain = re.sub(r"_\d+x\d+(?=\.\w+$)", "", hit[2]).replace("/resize/", "/modpub/")
    return dlsite_banner_options(re.sub(r"\.\w+$", ".jpg", plain))


def banner_sources(title, launch, nsfw):
    """What to look up for a game's banners: (label, function) pairs, one per store."""
    return [("Steam", lambda: _steam_urls(title, launch)), ("GOG", lambda: _gog_urls(title, nsfw)),
            ("DLsite", lambda: _dlsite_urls(title, nsfw))]


def search_steam(term, nsfw):
    items = http_get(STEAM_SEARCH, params={"term": term, "cc": "us", "l": "en"}).json().get("items", [])[:6]
    flags = {}
    if not nsfw and items:
        with ThreadPoolExecutor(max_workers=len(items)) as ex:
            for item, adult in zip(items, ex.map(lambda i: steam_is_adult(i["id"]), items)):
                flags[item["id"]] = adult
    shown = [i for i in items if not flags.get(i["id"])]
    icons = {}
    if shown:  # the portrait cover crops to a square best; older games may only have a header or a capsule
        with ThreadPoolExecutor(max_workers=len(shown)) as ex:
            for item, icon in zip(shown, ex.map(steam_icon, shown)):
                icons[item["id"]] = icon
    out = []
    for item in shown:
        out.append(Result(item["name"], icons.get(item["id"]) or item.get("tiny_image"), "Steam", item.get("tiny_image"),
                          f"https://store.steampowered.com/app/{item['id']}/", f"steam:{item['id']}",
                          f"https://cdn.cloudflare.steamstatic.com/steam/apps/{item['id']}/header.jpg",
                          {"s": "steam", "id": str(item["id"])}))
    return out, len(items) - len(shown)


GOG_CATALOG = "https://catalog.gog.com/v1/catalog"


def search_gog(term, nsfw):
    params = {
        "query": f"like:{term}",
        "limit": 10,
        "order": "desc:score",
        "productType": "in:game,pack",
        "countryCode": "US",
        "locale": "en-US",
        "currencyCode": "USD",
    }
    products = http_get(GOG_CATALOG, params=params).json().get("products", [])
    wanted = significant_tokens(term)
    out = []
    for p in products:
        title = p.get("title", "?")
        if not title_matches(title, wanted):  # GOG's fuzzy search also returns unrelated games 0n0
            continue
        cover = p.get("coverHorizontal") or p.get("coverVertical")
        wide = p.get("coverHorizontal") or p.get("coverVertical")
        tall = p.get("coverVertical") or p.get("coverHorizontal")
        link = p.get("storeLink") or (f"https://www.gog.com/game/{p['slug']}" if p.get("slug") else None)
        ref = {"s": "gog", "id": str(p["id"]), "name": title} if p.get("id") else None
        out.append(Result(title, tall, "GOG", wide, link, None, p.get("coverHorizontal"), ref))
        if len(out) == 6:
            break
    return out, 0


VNDB_API = "https://api.vndb.org/kana/vn"


def search_vndb(term, nsfw):
    body = {
        "filters": ["search", "=", term],
        "fields": "id, title, image.url, image.thumbnail, image.sexual, image.violence",
        "results": 6,
    }
    r = requests.post(VNDB_API, json=body, headers=HEADERS, timeout=8)
    r.raise_for_status()
    out, hidden = [], 0
    for vn in r.json().get("results", []):
        img = vn.get("image") or {}
        if not nsfw and (img.get("sexual", 0) > 0.5 or img.get("violence", 0) > 0.5):
            hidden += 1
            continue
        out.append(Result(vn.get("title", "?"), img.get("url"), "VNDB", img.get("thumbnail") or img.get("url"),
                          f"https://vndb.org/{vn['id']}" if vn.get("id") else None, None, None,
                          {"s": "vndb", "id": vn["id"]} if vn.get("id") else None))
    return out, hidden


def parse_dlsite(markup: str):
    items = []
    for block in markup.split("search_result_img_box_inner")[1:]:
        pid = re.search(r"product_id/([A-Z]{2}\d+)", block)
        title = re.search(r'title="([^"]+)"', block) or re.search(r'alt="([^"]+)"', block)
        if not (pid and title):
            continue
        img = re.search(r'(?:https?:)?//img\.dlsite\.jp/[^"\']+', block)
        url = img.group(0) if img else None
        if url and url.startswith("//"):
            url = "https:" + url
        items.append((pid.group(1), htmllib.unescape(title.group(1)), url))
    return items


def dlsite_variants(url):
    """DLsite search shows resized .webp thumbnails (240x240), which the image service refuses to crop UnU.
    The same picture exists full size under /modpub/ without the size suffix."""
    if not url:
        return []
    plain = re.sub(r"_\d+x\d+(?=\.\w+$)", "", url)
    full = plain.replace("/resize/", "/modpub/")
    stem = re.sub(r"\.\w+$", "", full)
    stem_small = re.sub(r"\.\w+$", "", url)
    out = []
    for candidate in (stem + ".jpg", full, stem_small + ".jpg", url):
        if candidate not in out:
            out.append(candidate)
    return out


def best_dlsite_image(url):
    variants = dlsite_variants(url)
    for candidate in variants[:-1]:
        if url_exists(candidate):
            return candidate
    return url


def dlsite_banner(url):
    """A second picture of the product (same size as the main one, but not the cover itself), if it has one."""
    if url and "_img_main" in url:
        other = url.replace("_img_main", "_img_smp1")
        if url_exists(other):
            return other
    return url


def search_dlsite(term, nsfw):
    sites = ["home", "soft"] + (["maniax", "pro"] if nsfw else [])
    adult_sites = {"maniax", "pro"}

    def fetch(site):
        url = f"https://www.dlsite.com/{site}/fsr/ajax/=/language/jp/keyword/{quote(term, safe='')}/per_page/30/page/1"
        try:
            r = http_get(
                url,
                headers={"Accept-Language": "ja,en;q=0.8"},
                cookies={"adultchecked": "1"} if nsfw else None,
            )
            try:
                markup = r.json().get("search_result", "")
            except ValueError:
                markup = r.text
            return site, [(*it, site) for it in parse_dlsite(markup)[:4]], False
        except Exception:
            return site, [], True

    out, seen, failed = [], set(), 0
    with ThreadPoolExecutor(max_workers=len(sites)) as ex:
        for site, items, error in ex.map(fetch, sites):
            failed += error
            for pid, title, img, _site in items:
                if pid in seen:
                    continue
                seen.add(pid)
                label = "DLsite (adult)" if site in adult_sites else "DLsite"
                out.append(Result(title, img, label, img, f"https://www.dlsite.com/{site}/work/=/product_id/{pid}.html",
                                  None, None, {"s": "dlsite", "id": pid, "site": site}))
    if failed == len(sites):
        raise RuntimeError("DLsite unreachable")
    if out:  # swap the tiny 240px thumbnail for a full-size version the image service can handle
        with ThreadPoolExecutor(max_workers=min(8, len(out))) as ex:
            best = list(ex.map(lambda r: best_dlsite_image(r.icon), out))
        for r, url in zip(out, best):
            r.icon = url
        with ThreadPoolExecutor(max_workers=min(8, len(out))) as ex:
            for r, banner in zip(out, ex.map(lambda r: dlsite_banner(r.icon), out)):
                r.banner = banner
    return out, 0


WIKI_API = "https://en.wikipedia.org/w/api.php"


def search_wikipedia(term, nsfw):
    params = {
        "action": "query", "format": "json", "generator": "search", "gsrsearch": term,
        "gsrlimit": 6, "gsrnamespace": 0, "prop": "pageimages", "piprop": "thumbnail", "pithumbsize": 512,
    }
    pages = http_get(WIKI_API, params=params).json().get("query", {}).get("pages", {})
    out = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        thumb = (page.get("thumbnail") or {}).get("source")
        if thumb:
            out.append(Result(page["title"], thumb, "Wikipedia", thumb))
    return out, 0


LUTRIS_API = "https://lutris.net/api/games"


def absolute(url, base="https://lutris.net"):
    if not url:
        return None
    return url if url.startswith("http") else (("https:" + url) if url.startswith("//") else base + url)


def search_lutris(term, nsfw):
    """Lutris' open game catalogue: PC games from every era, including many old and niche ones."""
    data = http_get(LUTRIS_API, params={"search": term, "page_size": 12}).json()
    wanted = significant_tokens(term)
    out, seen = [], set()
    for g in data.get("results", []):
        name = (g.get("name") or "").strip()
        if not name or name.lower() in seen or not title_matches(name, wanted):
            continue
        cover = absolute(g.get("coverart") or g.get("banner_url") or g.get("banner") or g.get("icon_url") or g.get("icon"))
        if not cover:
            continue
        seen.add(name.lower())
        banner = absolute(g.get("banner_url") or g.get("banner"))
        out.append(Result(name, cover, "Lutris", banner or cover, None, None, banner or None))
        if len(out) == 6:
            break
    return out, 0


ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_ADULT = ("adult", "hentai", "erotic", "porn", "nsfw", "xxx", "18+")


def search_archive(term, nsfw):
    """Internet Archive's software library: abandonware, old shareware and games that exist nowhere else."""
    words = normalize_query(term).split()
    if not words:
        return [], 0
    params = {"q": "title:(" + " AND ".join(words) + ") AND mediatype:software",
              "fl[]": ["identifier", "title", "subject"], "sort[]": "downloads desc", "rows": 12, "output": "json"}
    docs = http_get(ARCHIVE_SEARCH, params=params).json().get("response", {}).get("docs", [])
    out, hidden = [], 0
    for d in docs:
        ident = d.get("identifier")
        if not ident:
            continue
        title = d.get("title") or ident
        if isinstance(title, list):
            title = title[0] if title else ident
        title = str(title)
        subject = d.get("subject") or ""
        text = (title + " " + (" ".join(subject) if isinstance(subject, list) else str(subject))).lower()
        if not nsfw and any(w in text for w in ARCHIVE_ADULT):
            hidden += 1
            continue
        thumb = f"https://archive.org/download/{ident}/__ia_thumb.jpg"
        out.append(Result(str(title), thumb, "Internet Archive", thumb))
        if len(out) == 6:
            break
    return out, hidden


DEFAULT_OFF = set()  # sources that start switched off


SOURCES = {
    "steam": ("Steam", search_steam),
    "gog": ("GOG", search_gog),
    "vndb": ("VNDB", search_vndb),
    "dlsite": ("DLsite (beta)", search_dlsite),
    "wikipedia": ("Wikipedia", search_wikipedia),
    "lutris": ("Lutris", search_lutris),
    "archive": ("Internet Archive (abandonware)", search_archive),
}

# ---- this is to hide them when clicking "Choose" so it can actually show and prioritize the game(s) you're running at the time 
# ---- you can still pick these as your presence with the "browse" button tho @_@
IGNORE = {
    "system", "registry", "explorer.exe", "svchost.exe", "discord.exe",
    "chrome.exe", "firefox.exe", "msedge.exe", "brave.exe", "opera.exe",
    "python.exe", "pythonw.exe", "code.exe", "steam.exe", "steamwebhelper.exe",
    "epicgameslauncher.exe", "searchhost.exe", "taskmgr.exe", "cmd.exe",
    "powershell.exe", "windowsterminal.exe", "spotify.exe", "slack.exe",
}


# ---- game info window (Steam, GOG, DLsite, VNDB)
INFO_SOURCES = ("steam", "gog", "dlsite", "vndb")
INFO_HOSTS = ("store.steampowered.com", "www.gog.com", "www.dlsite.com", "vndb.org", "www.youtube.com", "youtu.be")
GOG_PRODUCT = "https://api.gog.com/products/{}"


def clean_ref(raw):
    """A store page reference ({"s": store, "id": ...}) if it is well formed, else None."""
    if not isinstance(raw, dict) or raw.get("s") not in INFO_SOURCES:
        return None
    rid = str(raw.get("id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", rid):
        return None
    ref = {"s": raw["s"], "id": rid}
    site = str(raw.get("site") or "")
    if re.fullmatch(r"[a-z]{2,12}", site):
        ref["site"] = site
    name = str(raw.get("name") or "").strip()[:120]
    if name:
        ref["name"] = name
    return ref


def ref_from_entry(e):
    """Which store page a recent game belongs to: saved with it, or worked out from its Steam id or button links.""" # recent game now referred to as "My games" in the app
    ref = clean_ref(e.get("ref"))
    if ref:
        return ref
    m = re.fullmatch(r"steam:(\d+)", str(e.get("launch") or ""))
    if m:
        return {"s": "steam", "id": m.group(1)}
    for b in e.get("buttons") or []:
        url = str((b or {}).get("url") or "")
        if m := re.search(r"store\.steampowered\.com/app/(\d+)", url):
            return {"s": "steam", "id": m.group(1)}
        if m := re.search(r"gog\.com/(?:[a-z]{2}/)?game/([\w-]+)", url):
            return {"s": "gog", "id": m.group(1)}
        if m := re.search(r"vndb\.org/(v\d+)", url):
            return {"s": "vndb", "id": m.group(1)}
        if m := re.search(r"dlsite\.com/([a-z]+)/work/=/product_id/([A-Z]{2}\d+)", url):
            return {"s": "dlsite", "id": m.group(2), "site": m.group(1)}
    return None


def find_ref(title, nsfw):
    """Looks a game up by its title when nothing says which store page it is: Steam, then GOG, then DLsite."""
    try:
        for query in query_variants(title):
            items = http_get(STEAM_SEARCH, params={"term": query, "cc": "us", "l": "en"}).json().get("items", [])[:6]
            hit = _closest(items, title, key=lambda i: i.get("name", ""))
            if hit:
                return {"s": "steam", "id": str(hit["id"])}
    except Exception:
        pass
    try:
        res, _, _ = run_source(search_gog, title, nsfw)
        hit = _closest(res, title, key=lambda r: r.title)
        if hit and hit.ref:
            return hit.ref
    except Exception:
        pass
    try:
        hit = _dlsite_hit(title, nsfw)
        if hit and _closest([hit], title, key=lambda it: it[1]):  # a DLsite guess has to be a real match here
            return {"s": "dlsite", "id": hit[0], "site": hit[3]}
    except Exception:
        pass
    return None


def plain_text(markup, limit=3000):
    """Page markup as readable text: paragraphs kept, pictures and tags dropped."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", markup or "")
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>|</tr>", "\n", t)
    t = re.sub(r"(?i)<li[^>]*>", "• ", t)
    t = htmllib.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t\r\f\v ]+", " ", t)
    t = re.sub(r" ?\n ?", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t[:limit].rstrip() + ("…" if len(t) > limit else "")


def first_paragraph(text, limit=320):
    para = (text or "").split("\n\n")[0].replace("\n", " ").strip()
    return para if len(para) <= limit else para[:limit].rsplit(" ", 1)[0] + "…"


def rows(*pairs):
    """Detail rows, leaving out the ones without a value."""
    return [[k, v] for k, v in pairs if v]


def join(items, limit=6):
    return ", ".join(str(i) for i in (items or [])[:limit] if i)


def info_steam(ref, nsfw, title):
    appid = ref["id"]
    page = f"https://store.steampowered.com/app/{appid}/"
    raw = http_get(STEAM_DETAILS, params={"appids": appid, "cc": "us", "l": "english"}).json()
    entry = raw.get(str(appid)) or {}
    d = entry.get("data")
    if not entry.get("success") or not isinstance(d, dict):
        return None
    adult = bool(set((d.get("content_descriptors") or {}).get("ids") or []) & STEAM_ADULT_DESCRIPTORS)
    media = []
    if nsfw or not adult:
        for m in (d.get("movies") or [])[:6]:
            mp4 = (m.get("mp4") or {}).get("max") or (m.get("webm") or {}).get("max")  # older entries still have a plain file
            media.append({"type": "video", "name": m.get("name") or "Trailer", "thumb": m.get("thumbnail"),
                          "src": mp4, "hls": m.get("hls_h264"), "page": page})
        for sh in (d.get("screenshots") or [])[:20]:
            if sh.get("path_full"):
                media.append({"type": "image", "thumb": sh.get("path_thumbnail") or sh["path_full"], "url": sh["path_full"]})
    platforms = d.get("platforms") or {}
    meta = d.get("metacritic") or {}
    return {
        "source": "Steam", "title": d.get("name") or title, "url": page, "cover": d.get("header_image"),
        "tagline": plain_text(d.get("short_description"), 400), "about": plain_text(d.get("about_the_game"), 3500),
        "reviews": [],
        "scores": [{"name": "Metacritic", "value": str(meta["score"]), "url": None}] if meta.get("score") else [],
        "details": rows(("Genres", join([g.get("description") for g in d.get("genres") or []])),
                        ("Developer", join(d.get("developers"))), ("Publisher", join(d.get("publishers"))),
                        ("Release date", (d.get("release_date") or {}).get("date")),
                        ("Platforms", join([n for k, n in (("windows", "Windows"), ("mac", "macOS"), ("linux", "Linux")) if platforms.get(k)]))),
        "tags": [c.get("description") for c in (d.get("categories") or [])[:8] if c.get("description")],
        "media": media, "adult": adult, "hidden": bool(adult and not nsfw and (d.get("movies") or d.get("screenshots"))),
    }



# ---- info box
def gog_shot(template, formatter):
    return str(template).replace("{formatter}", formatter)


def info_gog(ref, nsfw, title):
    name = ref.get("name") or title
    prods = http_get(GOG_CATALOG, params={"query": f"like:{name}", "limit": 10, "order": "desc:score", "productType": "in:game,pack",
                                          "countryCode": "US", "locale": "en-US", "currencyCode": "USD"}).json().get("products", [])
    p = next((x for x in prods if str(x.get("id")) == ref["id"] or x.get("slug") == ref["id"]), None) \
        or _closest(prods, name, key=lambda x: x.get("title", ""))
    gid = str(p["id"]) if p and p.get("id") else (ref["id"] if ref["id"].isdigit() else None)
    extra = {}
    if gid:
        try:
            extra = http_get(GOG_PRODUCT.format(gid), params={"expand": "description,screenshots,videos", "locale": "en-US"}).json()
        except Exception:
            extra = {}
    if not p and not extra:
        return None
    p = p or {}
    desc = extra.get("description") or {}
    lead, full = plain_text(desc.get("lead"), 1200), plain_text(desc.get("full"), 3500)
    media = []
    for v in (extra.get("videos") or [])[:4]:
        url = v.get("video_url") or ""
        if url.startswith("https://") and v.get("thumbnail_url"):
            media.append({"type": "video", "name": "Trailer", "thumb": v["thumbnail_url"], "src": None, "hls": None, "page": url})
    shots = [t for t in (p.get("screenshots") or []) if "{formatter}" in str(t)]
    if not shots:
        shots = [s.get("formatter_template_url") for s in (extra.get("screenshots") or []) if "{formatter}" in str(s.get("formatter_template_url"))]
    for t in shots[:20]:
        media.append({"type": "image", "thumb": gog_shot(t, "product_card_v2_mobile_slider_639"), "url": gog_shot(t, "ggvgl_2x")})
    rating = None
    try:
        r = float(p.get("reviewsRating") or 0)
        r = r / 10 if r > 5 else r
        if r > 0:
            rating = {"label": "GOG", "text": f"{r:.1f} / 5", "pct": round(r * 20), "count": int(p.get("reviewsCount") or 0)}
    except (TypeError, ValueError):
        pass
    released = str(p.get("releaseDate") or (extra.get("release_date") or "")[:10]).replace(".", "-")
    oses = [n for k, n in (("windows", "Windows"), ("osx", "macOS"), ("linux", "Linux")) if k in (p.get("operatingSystems") or [])]
    link = p.get("storeLink") or (f"https://www.gog.com/game/{p['slug']}" if p.get("slug") else None) \
        or ((extra.get("links") or {}).get("product_card"))
    return {
        "source": "GOG", "title": p.get("title") or extra.get("title") or title, "url": link,
        "cover": p.get("coverHorizontal") or p.get("coverVertical"),
        "tagline": first_paragraph(lead), "about": (lead + "\n\n" + full).strip() if full and full != lead else (lead or full),
        "reviews": [rating] if rating else [], "scores": [],
        "details": rows(("Genres", join([g.get("name") for g in p.get("genres") or []])),
                        ("Developer", join(p.get("developers"))), ("Publisher", join(p.get("publishers"))),
                        ("Release date", released), ("Platforms", join(oses))),
        "tags": [f.get("name") for f in (p.get("features") or [])[:8] if f.get("name")],
        "media": media, "adult": False, "hidden": False,
    }


def info_dlsite(ref, nsfw, title):
    site, pid = ref.get("site") or "maniax", ref["id"]
    adult = site in ("maniax", "pro")
    page = f"https://www.dlsite.com/{site}/work/=/product_id/{pid}.html"
    cookies = {"adultchecked": "1"} if adult else None
    html = http_get(page, params={"locale": "en_US"}, cookies=cookies, headers={"Accept-Language": "en,ja;q=0.8"}, timeout=12).text

    def meta(prop):
        m = re.search(r'<meta[^>]+property="%s"[^>]+content="([^"]*)"' % prop, html) or \
            re.search(r'<meta[^>]+content="([^"]*)"[^>]+property="%s"' % prop, html)
        return htmllib.unescape(m.group(1)).strip() if m else ""

    name = plain_text((re.search(r'id="work_name"[^>]*>(.*?)</h1>', html, re.S) or [None, ""])[1], 300) or \
        re.sub(r"\s*\[[^\]]*\]\s*$", "", meta("og:title")) or title
    maker = plain_text((re.search(r'class="maker_name"[^>]*>\s*<a[^>]*>(.*?)</a>', html, re.S) or [None, ""])[1], 120)
    outline = []
    block = re.search(r'id="work_outline".*?</table>', html, re.S)
    for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", block.group(0) if block else "", re.S):
        k, v = plain_text(th, 40), plain_text(re.sub(r"</a>\s*<a", "</a>, <a", td), 160).replace("\n", ", ")
        if k and v and k.lower() not in ("rating", "tags", "bonus"):
            outline.append([k, v])
    desc = ""
    for pat in (r'itemprop="description"[^>]*>', r'class="work_parts_container"[^>]*>'):
        m = re.search(pat, html)
        if m:
            desc = plain_text(html[m.end():m.end() + 9000], 3000)
            break
    tagline = meta("og:description")
    cover = meta("og:image")
    cover = ("https:" + cover) if cover.startswith("//") else cover
    reviews, sales = [], None
    try:
        info = http_get("https://www.dlsite.com/%s/product/info/ajax" % site, params={"product_id": pid, "cdn_cache_min": 1},
                        cookies=cookies).json()
        rec = info.get(pid) if isinstance(info, dict) else None
        if isinstance(rec, dict):
            avg, n = float(rec.get("rate_average_2dp") or rec.get("rate_average") or 0), int(rec.get("rate_count") or 0)
            if avg > 0 and n:
                reviews.append({"label": "DLsite", "text": f"{avg:.1f} / 5", "pct": round(avg * 20), "count": n})
            sales = int(rec.get("dl_count") or 0) or None
    except Exception:
        pass
    shots = []
    if nsfw or not adult:
        for u in re.findall(r'(?:data-src|src)="((?:https?:)?//img\.dlsite\.jp/[^"]*_img_smp\d*(?:_\d+x\d+)?\.\w+)"', html):
            u = ("https:" + u) if u.startswith("//") else u
            u = re.sub(r"_\d+x\d+(?=\.\w+$)", "", u).replace("/resize/", "/modpub/")
            if u not in shots:
                shots.append(u)
        if not shots and cover:
            shots = [cover]
    return {
        "source": "DLsite", "title": name, "url": page, "cover": cover or None,
        "tagline": tagline[:400], "about": desc if len(desc) > len(tagline) else tagline, "reviews": reviews, "scores": [],
        "details": rows(("Circle", maker), ("Sales", f"{sales:,}" if sales else "")) + outline[:8], "tags": [],
        "media": [{"type": "image", "thumb": u, "url": u} for u in shots[:20]], "adult": adult,
        "hidden": bool(adult and not nsfw),
    }


def info_vndb(ref, nsfw, title):
    body = {"filters": ["id", "=", ref["id"]],
            "fields": "title, alttitle, description, released, rating, votecount, length_minutes, languages, platforms, "
                      "image.url, image.sexual, image.violence, screenshots.url, screenshots.thumbnail, screenshots.sexual, "
                      "screenshots.violence, developers.name"}
    r = requests.post(VNDB_API, json=body, headers=HEADERS, timeout=10)
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return None
    vn = results[0]
    text = re.sub(r"\[spoiler\].*?\[/spoiler\]", "", vn.get("description") or "", flags=re.S | re.I)
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text, flags=re.S | re.I)
    text = re.sub(r"\[/?[a-z]+\]", "", text, flags=re.I).strip()
    img = vn.get("image") or {}
    shots = [x for x in vn.get("screenshots") or [] if nsfw or (x.get("sexual", 0) <= 0.5 and x.get("violence", 0) <= 0.5)]
    hidden = len(shots) < len(vn.get("screenshots") or [])
    rating = None
    if vn.get("rating") and vn.get("votecount"):
        rating = {"label": "VNDB", "text": f"{vn['rating'] / 10:.1f} / 10", "pct": round(vn["rating"]), "count": int(vn["votecount"])}
    mins = vn.get("length_minutes")
    return {
        "source": "VNDB", "title": vn.get("title") or title, "url": f"https://vndb.org/{ref['id']}",
        "cover": img.get("url") if (nsfw or img.get("sexual", 0) <= 0.5) else None,
        "tagline": first_paragraph(plain_text(text, 1200)), "about": plain_text(text, 3500),
        "reviews": [rating] if rating else [], "scores": [],
        "details": rows(("Developer", join([d.get("name") for d in vn.get("developers") or []])),
                        ("Release date", vn.get("released")), ("Length", f"about {round(mins / 60)} hours" if mins else ""),
                        ("Languages", join([str(x).upper() for x in vn.get("languages") or []], 8))),
        "tags": [], "media": [{"type": "image", "thumb": x.get("thumbnail") or x["url"], "url": x["url"]} for x in shots[:20]],
        "adult": False, "hidden": hidden,
    }


INFO_LOADERS = {"steam": info_steam, "gog": info_gog, "dlsite": info_dlsite, "vndb": info_vndb}


def parse_version(tag):
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def parse_release_notes(body):
    """Release notes may contain 'Priority: recommended|required' and 'Why: ...' lines; the rest is the change list."""
    priority, why, lines = "normal", "", []
    for line in (body or "").splitlines():
        m = re.match(r"\s*(priority|why)\s*:\s*(.+)", line, re.I)
        if not m:
            lines.append(line)
        elif m.group(1).lower() == "priority":
            value = m.group(2).strip().lower()
            priority = value if value in ("recommended", "required") else "normal"
        else:
            why = m.group(2).strip()
    return priority, why, "\n".join(lines).strip()


def clean_name(stem: str) -> str:
    s = re.sub(r"[-_.]+", " ", stem)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"\b(win ?64|win ?32|x64|x86|shipping|launcher|client)\b", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def window_titles_by_pid():
    """pid -> title of its visible top-level window. Windows only (None elsewhere)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwm = ctypes.windll.dwmapi
    titles = {}
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            if user32.GetWindowLongW(hwnd, -20) & 0x80:  # tool window (tooltips, helpers…)
                return True
            cloaked = wintypes.DWORD()
            dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:  # hidden UWP windows
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            titles.setdefault(pid.value, buf.value)
        except Exception:
            pass
        return True

    user32.EnumWindows(EnumProc(callback), 0)
    return titles


def list_programs(show_all=False):
    """Running programs as [{'name', 'exe', 'title'}].
    By default only programs that have a visible window (like Task Manager's "Apps")."""
    try:
        titles = window_titles_by_pid()
    except Exception:
        titles = None
    me = os.getpid()
    win_dir = os.environ.get("SystemRoot", "").lower()
    found = {}
    for p in psutil.process_iter(["pid", "name", "exe"]):
        pid, name, exe = p.info["pid"], p.info["name"], p.info["exe"] or ""
        if pid == me or not name or name.lower() in IGNORE:
            continue
        if exe and win_dir and exe.lower().startswith(win_dir):
            continue
        title = (titles or {}).get(pid, "")
        if titles is not None and not show_all and not title:
            continue
        entry = found.setdefault(name, {"name": name, "exe": exe, "title": ""})
        if exe and not entry["exe"]:
            entry["exe"] = exe
        if title and not entry["title"]:
            entry["title"] = title
    return sorted(found.values(), key=lambda e: (e["title"] or e["name"]).lower())


def extract_icon(exe_path, size=40):
    """The program's own icon as a PIL image, or None if it can't be read."""
    if IconExtractor is None or not exe_path:
        return None
    try:
        data = IconExtractor(exe_path).get_icon()
        data.seek(0)
        img = Image.open(data).convert("RGBA")
        return img.resize((size, size), getattr(Image, "Resampling", Image).LANCZOS)
    except Exception:
        return None


PROXY = "https://wsrv.nl/"


ICON_SIZE = 512


def float_box(size, state):
    """(x0, y0, side) of the square crop in the source image; state = {x, y, zoom, rot}."""
    W, H = size
    s = max(1.0, min(W, H) / max(1.0, state["zoom"]))
    cx = min(max(state["x"] * W, s / 2), W - s / 2)
    cy = min(max(state["y"] * H, s / 2), H - s / 2)
    return cx - s / 2, cy - s / 2, s


def crop_square(size, state):
    """Same square as float_box, as whole pixels inside the image."""
    W, H = size
    x0, y0, s = float_box(size, state)
    s = max(1, min(int(round(s)), W, H))
    return max(0, min(int(round(x0)), W - s)), max(0, min(int(round(y0)), H - s)), s


def rotate_box(box, size, rot):
    """The same square, located in the image after rotating the image clockwise by rot degrees."""
    x0, y0, s = box
    W, H = size
    if rot == 90:
        return H - y0 - s, x0, s
    if rot == 180:
        return W - x0 - s, H - y0 - s, s
    if rot == 270:
        return y0, W - x0 - s, s
    return box


def render_crop(img, box, rot, size):
    """What the icon looks like: crop the square, scale it, rotate it clockwise by rot."""
    x0, y0, s = box
    view = img.resize((size, size), getattr(Image, "Resampling", Image).BICUBIC, box=(x0, y0, x0 + s, y0 + s))
    return view.rotate(-rot) if rot else view


def proxy_url(src, box, ro):
    x0, y0, s = box
    params = (f"url={quote(src, safe='')}&cx={int(x0)}&cy={int(y0)}&cw={int(s)}&ch={int(s)}&precrop"
              f"&w={ICON_SIZE}&h={ICON_SIZE}&fit=cover")
    if ro:
        params += f"&ro={ro}"
    return f"{PROXY}?{params}"


def url_candidates(src, size, state, preferred=None):
    """[(order, direction, url)]. The proxy docs don't say whether it rotates before or after cropping,
    or which way 'ro' turns, so for rotated icons we try the combinations (see verify_icon_url)."""
    box, rot = crop_square(size, state), state["rot"] % 360
    if rot == 0:
        return [("A", 1, proxy_url(src, box, 0))]
    out = []
    for order in ("A", "B"):  # A: crop, then rotate   B: rotate, then crop
        use_box = box if order == "A" else rotate_box(box, size, rot)
        for direction in (1, -1):  # 1: ro turns clockwise   -1: counter-clockwise
            out.append((order, direction, proxy_url(src, use_box, rot if direction == 1 else (360 - rot) % 360)))
    if preferred:
        out.sort(key=lambda c: (c[0], c[1]) != tuple(preferred))
    return out


def flatten(img):
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    return bg.convert("RGB")


def image_distance(a, b, size=32):
    """How different two pictures look (0 = identical, ~60+ = unrelated), compared at a small size."""
    diff = ImageChops.difference(flatten(a).resize((size, size)), flatten(b).resize((size, size)))
    return sum(ImageStat.Stat(diff).mean) / 3


class ProxyRefused(RuntimeError):
    """The image service answered with an HTTP error for every attempt (e.g. the source site blocks it)."""


IMGBB_API = "https://api.imgbb.com/1/upload"
NEED_KEY = "Your own images need an ImgBB API key. Add it in Settings."
CENTER = {"x": 0.5, "y": 0.5, "zoom": 1.0, "rot": 0}
# sites Discord loads reliably on its own; anything else is re-hosted when an ImgBB key is set
SAFE_HOSTS = ("steamstatic.com", "gog-statics.com", "vndb.org", "wikimedia.org", "dlsite.jp",
              "archive.org", "lutris.net", "ibb.co", "wsrv.nl", "catbox.moe")


def load_source(src):
    """The picture behind a URL or a data: URI, as an RGBA image."""
    if src.startswith("data:"):
        raw = base64.b64decode(src.partition(",")[2])
    else:
        raw = http_get(src, timeout=15).content
    return ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGBA")


def to_png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def imgbb_upload(png, key):
    """Upload to ImgBB; returns the direct image URL."""
    try:
        r = requests.post(IMGBB_API, data={"key": key, "image": base64.b64encode(png).decode()},
                          headers=HEADERS, timeout=40)
    except requests.exceptions.RequestException:
        raise RuntimeError("Couldn't reach ImgBB. Check your connection and try again.")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if not r.ok or not body.get("success"):
        msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None
        if r.status_code in (400, 403) and msg and "key" in msg.lower():
            raise RuntimeError("ImgBB rejected the API key. Check it in Settings.")
        raise RuntimeError(f"ImgBB upload failed: {msg or r.status_code}")
    return body["data"]["url"]


def verify_icon_url(src, img, state, preferred=None, fetch=None):
    """Build the proxy URL for this edit and check - by really fetching it - that it matches the preview.
    Returns (url, (order, direction))."""
    fetch = fetch or (lambda url: http_get(url, timeout=20))
    local = render_crop(img, crop_square(img.size, state), state["rot"] % 360, 64)
    why = "no usable image came back"
    best = None  # (distance, url, variant): the closest match so far
    tried = http_errors = 0
    for order, direction, url in url_candidates(src, img.size, state, preferred):
        tried += 1
        try:
            remote = Image.open(io.BytesIO(fetch(url).content))
            remote.load()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            raise RuntimeError("Couldn't reach the image service (wsrv.nl). Check your connection and try again.")
        except Exception as e:
            why = str(e)
            if isinstance(e, requests.exceptions.HTTPError):
                http_errors += 1
            continue
        if state["rot"] % 360 == 0:
            return url, (order, direction)
        score = image_distance(local, remote)
        if score <= 6:  # a clear match: stop here
            return url, (order, direction)
        if best is None or score < best[0]:
            best = (score, url, (order, direction))
        why = "it came back rotated or cropped differently than the preview"
    if best and best[0] <= 14:  # not perfect (e.g. heavy compression) but clearly the closest
        return best[1], best[2]
    if best is None and tried and http_errors == tried:
        raise ProxyRefused(why)
    raise RuntimeError(f"Couldn't reproduce this edit through the image service ({why}). Try again without rotating.")


# ---------------------------------------------------------------- backend for the interface
def resource_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def png_data_uri(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()



# ---------------------------------------------------------------- start with Windows, tray icon, single instance
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command():
    if getattr(sys, "frozen", False):  # packaged .exe
        return f'"{sys.executable}" --startup'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pyw if pyw.exists() else Path(sys.executable)
    return f'"{interpreter}" "{Path(__file__).resolve()}" --startup'


def get_startup_value():
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, APP_NAME)[0]
    except OSError:
        return None


def set_startup(enabled: bool):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def make_app_icon(size=64):
    """The tray icon: tray.png shipped next to the program (a plain tile if it's missing)."""
    try:
        return Image.open(resource_path("tray.png")).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle([2, 2, size - 3, size - 3], radius=size // 4, fill=(244, 154, 194, 255))
        return img


def trim_memory():
    """Hand idle memory back to Windows (this process and the web view's helper processes)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        me = psutil.Process()
        for proc in [me] + me.children(recursive=True):
            handle = k32.OpenProcess(0x0100 | 0x0400, False, proc.pid)  # SET_QUOTA | QUERY_INFORMATION
            if handle:
                psapi.EmptyWorkingSet(handle)
                k32.CloseHandle(handle)
    except Exception:
        pass


class SingleInstance:
    """Only one ANY-PRESENCE at a time (Windows). A second launch just asks the running one to show its window."""
    MUTEX = "Local\\ANY-PRESENCE-mutex"
    EVENT = "Local\\ANY-PRESENCE-show"

    def __init__(self):
        self.is_primary = True
        self._event = None
        self._k32 = None
        if os.name != "nt":
            return
        try:
            import ctypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            for fn in (k32.CreateMutexW, k32.CreateEventW, k32.OpenEventW):
                fn.restype = ctypes.c_void_p
            k32.SetEvent.argtypes = [ctypes.c_void_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k32.WaitForSingleObject.restype = ctypes.c_uint32
            self._k32 = k32
            self._mutex = k32.CreateMutexW(None, False, self.MUTEX)
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                self.is_primary = False
                handle = k32.OpenEventW(0x0002, False, self.EVENT)  # EVENT_MODIFY_STATE
                if handle:
                    k32.SetEvent(handle)
                    k32.CloseHandle(handle)
            else:
                self._event = k32.CreateEventW(None, False, False, self.EVENT)  # auto-reset
        except Exception:
            self.is_primary = True  # if anything goes wrong, just run normally

    def listen(self, callback):
        """Call callback() (from a background thread) whenever a second launch happens."""
        if not self._event or not self._k32:
            return

        def loop():
            while True:
                if self._k32.WaitForSingleObject(self._event, 1000) == 0:  # WAIT_OBJECT_0
                    callback()

        threading.Thread(target=loop, daemon=True).start()


class Api:
    """Every public method here can be called from the page as `pywebview.api.<name>(...)`."""

    def __init__(self):
        self._cfg = load_config()
        apply_network(self._cfg["network"])
        self._window = None
        self._rpc = None
        self._running = False
        self._starting = False
        self._started_at = None
        self._game_proc = None
        self._seen_running = False
        self._proxy_variant = None
        self._search_id = 0
        self._tray = None
        self._current_title = ""
        self._auto_entry = None  # saved app currently driving the presence via auto-detect
        self._auto_dismissed = set()  # saved apps stopped by hand (until they close)
        self._auto_retry_at = 0.0
        self._waiting_for = None  # saved app detected while Discord isn't open yet
        self._last_payload = None  # what the presence currently shows (to restore it after Discord restarts)
        self._lost = False  # Discord closed while the presence was active
        self._stack = []  # presences a newer saved app took over (restored, newest first, when it closes)
        self._auto_known = set()  # saved apps seen running on the last check
        self._banner_cache = {}  # banner candidates already looked up
        self._banner_running = set()
        self._monitor_id = 0
        self._names_cache = None
        self._pending = None  # a game started from Recent while Discord was closed: its presence starts when Discord opens
        self._pt_last = 0.0  # playtime tracking
        self._pt_open = None  # keys of the recent games seen running at the last check
        self._pt_saved = 0.0
        self._info_cache = {}  # game info already looked up: key -> (time, info)
        self._bn_lock = threading.Lock()  # banner copies kept on disk
        self._bn_todo = []
        self._bn_tried = {}
        self._bn_worker = False

    # ---- plumbing
    def _push(self, event, payload=None):
        """Tell the page something happened (presence stopped by itself, ...)."""
        if self._window is not None:
            try:
                self._window.evaluate_js(f"window.onBackend && window.onBackend({json.dumps(event)}, {json.dumps(payload)})")
            except Exception:
                pass

    def _app_id(self):
        own = str(self._cfg.get("app_id") or "").strip()
        return own if own.isdigit() else DEFAULT_APP_ID

    def state(self):
        cfg = self._cfg
        return {
            "app_id": cfg["app_id"], "nsfw": cfg["nsfw"], "show_timer": cfg["show_timer"],
            "auto_stop": cfg["auto_stop"], "imgbb_key": cfg["imgbb_key"], "version": APP_VERSION, "check_updates": cfg["check_updates"], "update_later": str(cfg.get("update_later") or ""), "network": cfg["network"], "theme": cfg.get("theme", "rose"), "custom_theme": cfg.get("custom_theme"),
            "sources": cfg["sources"], "source_labels": {k: v[0] for k, v in SOURCES.items()},
            "running": self._running, "started_at": self._started_at,
            "auto_detect": cfg["auto_detect"], "close_action": cfg["close_action"],
            "startup": get_startup_value() is not None, "startup_available": winreg is not None,
            "tray_available": pystray is not None, "apps": cfg["apps"],
            "recent": cfg["recent"],
        }

    def set_setting(self, key, value):
        if key == "startup":
            try:
                set_startup(bool(value))
            except Exception as e:
                return {"error": f"Couldn't change the startup setting: {e}"}
            return {"ok": True}
        if key == "close_action":
            self._cfg[key] = "background" if value == "background" and pystray is not None else "quit"
        elif key == "network":
            self._cfg[key] = value if value in ("auto", "ipv4", "ipv6") else "auto"
            apply_network(self._cfg[key])
        elif key == "imgbb_key":
            self._cfg[key] = str(value).strip()
        elif key == "theme":
            self._cfg[key] = value if value in THEME_IDS and (value != "custom" or self._cfg.get("custom_theme")) else "rose"
        elif key == "update_later":
            self._cfg[key] = str(value or "").strip()[:40]
        elif key in ("app_id", "nsfw", "show_timer", "auto_stop", "auto_detect", "check_updates"):
            self._cfg[key] = str(value).strip() if key in ("app_id", "theme") else bool(value)
        elif key.startswith("source:") and key[7:] in self._cfg["sources"]:
            self._cfg["sources"][key[7:]] = bool(value)
        save_config(self._cfg)
        return True

    def set_custom_theme(self, name, colors):
        ct = clean_custom_theme({"name": name, "colors": colors})
        if not ct:
            return {"error": "Those colours can't be used."}
        self._cfg["custom_theme"] = ct
        self._cfg["theme"] = "custom"
        save_config(self._cfg)
        return {"ok": True, "custom_theme": ct}

    # ---- updates 88w88
    def check_update(self, manual=True):
        if "OWNER" in UPDATE_REPO:
            return {"error": "Updates aren't set up yet."} if manual else {}
        try:
            r = http_get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
                         headers={"Accept": "application/vnd.github+json"}, timeout=10).json()
        except Exception:
            return {"error": "Couldn't check for updates."} if manual else {}
        tag = str(r.get("tag_name") or "")
        if parse_version(tag) <= parse_version(APP_VERSION):
            return {"current": True, "version": APP_VERSION}
        found = {a.get("name"): a for a in r.get("assets", [])}
        base = f"https://github.com/{UPDATE_REPO}/releases/download/"
        exe = found.get(UPDATE_ASSET) or {}
        exe_url = exe.get("browser_download_url")
        digest = str(exe.get("digest") or "")  # GitHub calculates this for every uploaded file
        sha = digest[7:].lower() if digest.startswith("sha256:") else None
        sha_url = (found.get(UPDATE_ASSET + ".sha256") or {}).get("browser_download_url")
        if not (exe_url and exe_url.startswith(base) and (sha or (sha_url and sha_url.startswith(base)))):
            return {"error": "This release is missing its files."} if manual else {}
        priority, why, notes = parse_release_notes(r.get("body"))
        self._update = {"version": tag.lstrip("vV"), "url": exe_url, "sha": sha, "sha_url": sha_url}
        return {"available": True, "version": self._update["version"], "priority": priority, "why": why, "notes": notes}

    def install_update(self):
        """Download the new .exe, check its checksum, then let a small script swap it in and restart the app."""
        info = getattr(self, "_update", None)
        if not info:
            return {"error": "Check for updates first."}
        if not getattr(sys, "frozen", False):
            return {"error": "Updating works in the built .exe."}
        exe = Path(sys.executable)
        new = exe.with_name(exe.stem + ".new.exe")
        try:
            expected = info["sha"] or http_get(info["sha_url"], timeout=15).text.split()[0].strip().lower()
            digest = hashlib.sha256()
            with requests.get(info["url"], headers=HEADERS, timeout=30, stream=True) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                done, last = 0, -1
                with open(new, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        pct = int(done * 100 / total) if total else -1
                        if pct != last:
                            last = pct
                            self._push("update_progress", {"stage": "download", "pct": pct})
            self._push("update_progress", {"stage": "verify", "pct": 100})
            if digest.hexdigest() != expected:
                new.unlink(missing_ok=True)
                return {"error": "The download didn't match its checksum, so it was discarded."}
            import subprocess, tempfile
            bat = Path(tempfile.gettempdir()) / "any_presence_update.bat"
            esc = lambda path: str(path).replace("%", "%%")  # a % in a folder name must survive the script
            bat.write_text(  # UTF-8 with the console switched to it, so folders with accents (José, Ñandú...) work
                "@echo off\r\nchcp 65001 >nul\r\nset n=0\r\n:wait\r\nping 127.0.0.1 -n 2 >nul\r\n"
                f'move /y "{esc(new)}" "{esc(exe)}" >nul 2>&1\r\n'
                "if errorlevel 1 (\r\n set /a n+=1\r\n if %n% lss 30 goto wait\r\n exit /b 1\r\n)\r\n"
                f'ping 127.0.0.1 -n 4 >nul\r\nstart "" "{esc(exe)}"\r\ndel "%~f0"\r\n', encoding="utf-8")
            self._push("update_progress", {"stage": "restart", "pct": 100})
            env = clean_env()
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x08000000 | 0x00000200,
                             close_fds=True, env=env, startupinfo=si,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            try:
                new.unlink(missing_ok=True)
            except Exception:
                pass
            return {"error": f"Update failed: {e}"}
        threading.Timer(1.5, self._quit_app).start()
        return {"ok": True}

    def _update_check_later(self):
        time.sleep(8)
        if self._cfg["check_updates"]:
            info = self.check_update(manual=False)
            if info.get("available"):
                self._push("update_available", info)

    # ---- links
    def open_link(self, url):
        host = urlparse(str(url)).hostname if str(url).startswith("https://") else None
        if url in ("https://discord.com/developers/applications", "https://api.imgbb.com/") or host in INFO_HOSTS:
            import webbrowser
            webbrowser.open(url)

    # ---- window
    def minimize(self):
        if self._window:
            self._window.minimize()

    def close(self):
        if self._cfg["close_action"] == "background" and pystray is not None and self._window:
            self._hide_to_tray()
        else:
            self._quit_app()

    def _hide_to_tray(self):
        if pystray is None:
            if self._window:
                self._window.minimize()
            return
        try:
            self._start_tray()
            self._window.hide()
            threading.Timer(2, trim_memory).start()
        except Exception:
            self._stop_tray()
            if self._window:
                self._window.minimize()

    def _show_window(self):
        self._stop_tray()
        if self._window:
            try:
                self._window.show()
                self._window.restore()
            except Exception:
                pass

    def _start_tray(self):
        if self._tray is not None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Open ANY-PRESENCE", lambda icon, item: self._show_window(), default=True),
            pystray.MenuItem(
                lambda item: f"Presence: {self._current_title}" if self._running and self._current_title else "Presence: off",
                None, enabled=False),
            pystray.MenuItem("Stop presence", lambda icon, item: self._tray_stop(), enabled=lambda item: self._running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, item: self._quit_app()),
        )
        self._tray = pystray.Icon(APP_NAME, make_app_icon(), APP_NAME, menu)
        self._tray.run_detached()

    def _tray_stop(self):
        self.stop()
        self._push("stopped", "Presence stopped.")

    def _stop_tray(self):
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
            self._tray = None

    def _refresh_tray(self):
        if self._tray is not None:
            try:
                self._tray.update_menu()
            except Exception:
                pass

    def _quit_app(self):
        save_config(self._cfg)
        self.stop(quiet=True, by_user=False)
        self._stop_tray()
        if self._window:
            self._window.destroy()

    # ---- icon hosting (ImgBB)
    def _host_png(self, png, cache_key=None):
        key = self._cfg.get("imgbb_key", "")
        if not key:
            raise RuntimeError(NEED_KEY)
        cache = self._cfg["icon_cache"]
        ck = cache_key or "h:" + hashlib.sha256(png).hexdigest()
        if ck in cache:
            return cache[ck]
        url = imgbb_upload(png, key)
        cache[ck] = url
        while len(cache) > 400:
            cache.pop(next(iter(cache)))
        save_config(self._cfg)
        return url

    @staticmethod
    def _needs_hosting(url):
        host = urlparse(url).netloc.lower().split(":")[0]
        if url.lower().split("?")[0].endswith(".gif"):  # keep animated pictures as they are
            return False
        return not any(host == h or host.endswith("." + h) for h in SAFE_HOSTS)

    def _prepare(self, p):
        """The payload with its icon ready for Discord: own images and program icons get hosted."""
        p = dict(p)
        icon, exe = p.get("icon"), p.pop("exe", None)
        src, crop = p.pop("icon_src", None), p.pop("icon_crop", None)
        has_key = bool(self._cfg.get("imgbb_key"))
        if icon and icon.startswith("data:"):  # an image the person added
            img = load_source(icon)
            p["icon"] = self._host_png(to_png(render_crop(img, crop_square(img.size, CENTER), 0, ICON_SIZE)))
        elif icon and has_key and src and isinstance(crop, dict) and not urlparse(icon).netloc.endswith("ibb.co"):
            # a crop made without hosting (or before the key was added): draw it again from the original and host it
            ck = "c:" + src + "|" + json.dumps(crop, sort_keys=True)
            try:
                if ck in self._cfg["icon_cache"]:
                    p["icon"] = self._cfg["icon_cache"][ck]
                else:
                    img = load_source(src)
                    view = {"x": float(crop["x"]), "y": float(crop["y"]), "zoom": float(crop["zoom"]), "rot": int(crop["rot"]) % 360}
                    p["icon"] = self._host_png(to_png(render_crop(img, crop_square(img.size, view), view["rot"], ICON_SIZE)), ck)
            except Exception:
                pass  # keeps the link it already has
        elif icon and has_key and self._needs_hosting(icon):
            ck = "u:" + icon
            try:
                if ck in self._cfg["icon_cache"]:
                    p["icon"] = self._cfg["icon_cache"][ck]
                else:
                    img = load_source(icon)
                    p["icon"] = self._host_png(to_png(render_crop(img, crop_square(img.size, CENTER), 0, ICON_SIZE)), ck)
            except Exception:
                pass  # can't be re-hosted: Discord gets the original link
        elif not icon and exe and has_key:  # no picture chosen: use the program's own icon
            try:
                img = extract_icon(exe, 256)
                if img:
                    p["icon"] = self._host_png(to_png(img))
            except Exception:
                pass
        return p

    # ---- recent games
    def _record_recent(self, p, orig, touch=True, add=False):
        proc = str(orig.get("proc") or "").strip()
        if not proc:  # only games with a program chosen: that's how the list knows they're running
            return False
        launch = orig.get("launch") or (str(orig.get("exe") or "") or None)
        title = str(p.get("title") or "").strip()
        icon = p.get("icon")
        if icon and str(icon).startswith("data:"):
            icon = None
        key = (proc or str(launch or "") or title).lower()
        old_ref = next((r.get("ref") for r in self._cfg["recent"] if r["key"] == key), None)
        ref = clean_ref(orig.get("ref")) or clean_ref(p.get("ref")) or old_ref
        entry = {"key": key, "title": title, "icon": icon, "extra": p.get("extra") or None, "ref": ref,
                 "buttons": clean_buttons(p.get("buttons")), "banner": str(p.get("banner") or "") or None, "proc": proc or None,
                 "exe": str(orig.get("exe") or "") or None, "launch": launch, "last": int(time.time())}
        old = next((r for r in self._cfg["recent"] if r["key"] == key), None)
        # total seconds the program has been seen running: remembered even if the game was removed before
        entry["played"] = max((old or {}).get("played", 0), self._cfg["playtime"].get(proc.lower(), 0))
        if old and old.get("banner_custom"):  # a banner the person chose stays
            entry["banner"], entry["banner_custom"] = old.get("banner"), True
        if add:  # added by hand, never played: goes right after the game to continue, which stays on top C_C
            if old:
                return False
            entry["last"] = 0
            self._cfg["recent"].insert(min(1, len(self._cfg["recent"])), entry)
        elif touch:
            rest = [r for r in self._cfg["recent"] if r["key"] != key]
            self._cfg["recent"] = [entry] + rest
        else:  # an edit: refresh the entry where it is, only if it's already there
            for i, old in enumerate(self._cfg["recent"]):
                if old["key"] == key:
                    entry["last"] = old.get("last", entry["last"])
                    entry["launch"] = entry["launch"] or old.get("launch")
                    entry["exe"] = entry["exe"] or old.get("exe")
                    self._cfg["recent"][i] = entry
                    break
            else:
                return False
        save_config(self._cfg)
        self._push("recent", self._cfg["recent"])
        return True

    def add_recent(self, payload):
        """Puts the game being edited into My Games without starting it."""
        title = str(payload.get("title") or "").strip()
        if not title:
            return {"error": "Pick a result or a program first."}
        if not str(payload.get("proc") or "").strip():
            return {"error": "Choose the running program first."}
        p = {k: payload.get(k) for k in ("title", "icon", "extra", "buttons", "banner", "ref")}
        added = self._record_recent(p, payload, add=True)
        return {"added": added, "recent": self._cfg["recent"]}

    @staticmethod
    def _banner_urls(recent):
        urls = []
        for e in recent:
            b = e.get("banner")
            if not b:
                m = re.fullmatch(r"steam:(\d+)", str(e.get("launch") or ""))
                b = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{m.group(1)}/header.jpg" if m else None
            if isinstance(b, str) and b.startswith("https://") and b not in urls:
                urls.append(b)
        return urls

    def banner_images(self, urls):
        """The banners already kept on disk, as pictures the page can show at once. The rest are fetched in the background."""
        out, missing = {}, []
        for u in [u for u in (urls or []) if isinstance(u, str) and u.startswith("https://")][:100]:
            path = banner_file(u)
            try:
                out[u] = banner_data(path)
            except OSError:
                missing.append(u)
        if missing:
            self._queue_banners(missing, front=True)  # the page wants these now
        return out

    def _queue_banners(self, urls, front=False):
        with self._bn_lock:
            for u in reversed(urls) if front else urls:
                if u in self._bn_todo:
                    if front:  # already waiting: move it up
                        self._bn_todo.remove(u)
                        self._bn_todo.insert(0, u)
                    continue
                if time.time() - self._bn_tried.get(u, 0) < 600:  # failed a moment ago 0_0
                    continue
                self._bn_tried[u] = time.time()
                self._bn_todo.insert(0, u) if front else self._bn_todo.append(u)
            if self._bn_todo and not self._bn_worker:
                self._bn_worker = True
                threading.Thread(target=self._banner_loop, daemon=True).start()

    def _banner_loop(self):
        while True:
            with self._bn_lock:
                if not self._bn_todo:
                    self._bn_worker = False
                    return
                u = self._bn_todo.pop(0)
            try:
                self._push("banner_ready", {"url": u, "data": banner_data(fetch_banner(u))})
            except Exception:
                pass

    def _warm_banners(self):
        """At start-up: keep a copy of every Recent banner, and drop copies nobody uses any more."""
        urls = self._banner_urls(self._cfg["recent"])
        try:
            keep = {banner_file(u).name for u in urls}
            for f in BANNER_DIR.glob("*"):
                if f.name not in keep:
                    f.unlink(missing_ok=True)
        except OSError:
            pass
        self._queue_banners([u for u in urls if not banner_file(u).exists()])

    def game_info(self, key):
        """Everything a store knows about a game in My Games, for the info window."""
        e = next((r for r in self._cfg["recent"] if r["key"] == key), None)
        if not e:
            return {"error": "That game isn't in the list any more."}
        try:
            ref = ref_from_entry(e)
            if not ref:
                ref = find_ref(e["title"], self._cfg["nsfw"])
                if ref:  # remembered, so the next click is quick
                    e["ref"] = ref
                    save_config(self._cfg)
            return self._load_info(ref, e["title"])
        except requests.exceptions.RequestException:
            return {"error": "Couldn't reach the store. Check your connection and try again."}
        except Exception as ex:
            return {"error": f"Couldn't load the info: {ex}"}

    def game_info_ref(self, ref, title):
        """The same info window for a search result (a game that isn't in My Games)."""
        title = str(title or "").strip()[:200]
        try:
            return self._load_info(clean_ref(ref) or find_ref(title, self._cfg["nsfw"]), title)
        except requests.exceptions.RequestException:
            return {"error": "Couldn't reach the store. Check your connection and try again."}
        except Exception as ex:
            return {"error": f"Couldn't load the info: {ex}"}

    def _load_info(self, ref, title):
        if not ref:
            return {"none": True, "title": title}
        nsfw = self._cfg["nsfw"]
        ck = (ref["s"], ref["id"], ref.get("site"), nsfw)
        hit = self._info_cache.get(ck)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        info = INFO_LOADERS[ref["s"]](ref, nsfw, title)
        if not info:
            return {"none": True, "title": title}
        partial = info.pop("partial", False)
        info["ref"] = ref
        if not partial:
            self._info_cache[ck] = (time.time(), info)
        return info

    def banner_options(self, key):
        """Banner candidates for a recent game. Each store's pictures are pushed as soon as they are found."""
        e = next((r for r in self._cfg["recent"] if r["key"] == key), None)
        if not e:
            return {"options": [], "done": True}
        ck = f"{key}|{e['title']}|{self._cfg['nsfw']}"
        cached = self._banner_cache.get(ck)
        if cached is not None:
            return {"options": cached, "done": True}
        if ck not in self._banner_running:  # a lookup for this game is already on its way
            self._banner_running.add(ck)
            threading.Thread(target=self._banner_worker, args=(key, ck, e["title"], e.get("launch"), self._cfg["nsfw"]),
                             daemon=True).start()
        return {"options": [], "done": False}

    def _banner_worker(self, key, ck, title, launch, nsfw):
        got, seen, lock = [], set(), threading.Lock()

        def run(job):
            label, fn = job
            try:
                urls = fn()
            except Exception:
                urls = []
            with lock:
                fresh = [{"url": u, "source": label} for u in urls if u and u not in seen]
                seen.update(o["url"] for o in fresh)
                got.extend(fresh)
            self._push("banner_options", {"key": key, "options": fresh, "done": False})

        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(run, banner_sources(title, launch, nsfw)))
        self._banner_cache[ck] = got
        self._banner_running.discard(ck)
        while len(self._banner_cache) > 30:
            self._banner_cache.pop(next(iter(self._banner_cache)))
        self._push("banner_options", {"key": key, "options": [], "done": True})

    def set_banner(self, key, banner):
        """A banner picked or uploaded for a recent game (None goes back to the automatic one)."""
        banner = banner or None
        if banner is not None and not (isinstance(banner, str) and len(banner) < 600000
                                       and banner.startswith(("https://", "data:image/"))):
            return {"error": "That picture can't be used."}
        for e in self._cfg["recent"]:
            if e["key"] == key:
                e["banner"], e["banner_custom"] = banner, bool(banner)
                proc = (e.get("proc") or "").lower()
                for a in self._cfg["apps"]:
                    if proc and a["proc"].lower() == proc:
                        a["banner"] = banner
        save_config(self._cfg)
        return {"recent": self._cfg["recent"]}

    def remove_recent(self, key):
        for r in self._cfg["recent"]:  # the hours stay: they come back if the game is played again
            if r["key"] == key and r.get("proc") and r.get("played"):
                self._cfg["playtime"][r["proc"].lower()] = max(r["played"], self._cfg["playtime"].get(r["proc"].lower(), 0))
        self._cfg["recent"] = [r for r in self._cfg["recent"] if r["key"] != key]
        save_config(self._cfg)
        return self._cfg["recent"]

    @staticmethod
    def _launch(spec):
        """Starts a game from a recent entry: a Steam game by its id, or a program by its path."""
        spec = str(spec or "")
        try:
            if spec.startswith("steam:") and spec[6:].isdigit():
                os.startfile(f"steam://rungameid/{spec[6:]}")
                return None
            path = Path(spec)
            if path.suffix.lower() != ".exe" or not path.is_file():
                return "Can't find that program any more."
            import subprocess
            subprocess.Popen([str(path)], cwd=str(path.parent), env=clean_env(), close_fds=True)
            return None
        except Exception as e:
            return f"Couldn't start it: {e}"

    @staticmethod
    def _game_started(proc):
        """When the program really started (so the timer shows how long it has been open), or None."""
        if not proc:
            return None
        times = []
        try:
            for p in psutil.process_iter(["name"]):
                if (p.info["name"] or "").lower() == proc.lower():
                    try:
                        times.append(p.create_time())
                    except Exception:
                        pass
        except Exception:
            return None
        return int(min(times)) if times else None

    def _promote(self, proc):
        """Puts the game with this program at the top of Recent: that's the one to continue."""
        p = str(proc or "").lower()
        recent = self._cfg["recent"]
        i = next((n for n, r in enumerate(recent) if p and (r.get("proc") or "").lower() == p), None)
        if i in (None, 0):
            return
        recent[i]["last"] = int(time.time())
        recent.insert(0, recent.pop(i))
        save_config(self._cfg)
        self._push("recent", recent)

    def _track_playtime(self):
        """Adds the time since the last check to every recent game whose program is running."""
        now, last = time.time(), self._pt_last
        self._pt_last = now
        if not self._cfg["recent"] or not last:
            return
        dt = min(now - last, 30)  # a sleeping computer doesn't count as playing
        names = self._running_names()
        changed = False
        open_now = set()
        for r in self._cfg["recent"]:
            if r.get("proc") and r["proc"].lower() in names:
                r["played"] = r.get("played", 0) + dt
                self._cfg["playtime"][r["proc"].lower()] = r["played"]
                open_now.add(r["key"])
                changed = True
        closed = (self._pt_open or set()) - open_now
        self._pt_open = open_now
        if closed:  # the game closed last becomes the one to continue
            now_i = int(now)
            gone = [r for r in self._cfg["recent"] if r["key"] in closed]
            for r in gone:
                r["last"] = now_i
            self._cfg["recent"] = gone + [r for r in self._cfg["recent"] if r["key"] not in closed]
            save_config(self._cfg)
            self._pt_saved = now
            self._push("recent", self._cfg["recent"])
            shown = (self._auto_entry or {}).get("proc") or self._game_proc
            if self._running and shown and shown.lower() in names:  # a game that's still open and on Discord stays on top
                self._promote(shown)
            return
        if changed and now - self._pt_saved > 60:
            self._pt_saved = now
            save_config(self._cfg)

    def _running_names(self, fresh=False):
        """Lower-case names of the running programs. Several checks need them every few seconds, so one scan is shared."""
        now = time.time()
        if not fresh and self._names_cache and now - self._names_cache[0] < 2:
            return self._names_cache[1]
        try:
            names = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
        except Exception:
            names = set()
        self._names_cache = (now, names)
        return names

    def running_recent(self):
        """Keys of the recent games whose program is running right now."""
        names = self._running_names()
        return [r["key"] for r in self._cfg["recent"] if r.get("proc") and r["proc"].lower() in names]

    def play_recent(self, key, launch=True):
        e = next((r for r in self._cfg["recent"] if r["key"] == key), None)
        if not e:
            return {"error": "That game isn't in the list any more."}
        if launch and e.get("launch"):
            if e.get("proc") and e["proc"].lower() in self._running_names(fresh=True):
                return {"error": "It's already running."}
            err = self._launch(e["launch"])
            if err:
                return {"error": err}
        # playing it counts even if Discord isn't open
        self._record_recent({"title": e["title"], "icon": e.get("icon"), "extra": e.get("extra"),
                             "buttons": e.get("buttons"), "banner": e.get("banner")},
                            {"proc": e.get("proc"), "exe": e.get("exe"), "launch": e.get("launch"), "ref": e.get("ref")})
        carried, prev = [], None
        if self._running:  # what was showing goes back on the pile, to return to when this game closes
            carried = list(self._stack)
            prev = {"entry": self._auto_entry, "payload": self._last_payload, "game_proc": self._game_proc,
                    "started_at": self._started_at}
        self.set_program(e.get("proc"))
        if self._running:
            self.stop(by_user=False)
        payload = {"title": e["title"], "icon": e.get("icon"), "extra": e.get("extra"),
                   "buttons": e.get("buttons"), "banner": e.get("banner"), "exe": e.get("exe"), "proc": e.get("proc"),
                   "launch": e.get("launch"), "ref": e.get("ref")}
        r = self.start(payload, early_record=False)  # already recorded above
        if r.get("ok"):
            r["title"] = e["title"]
            if prev and prev["payload"] and (prev["game_proc"] or "").lower() != (e.get("proc") or "").lower():
                carried.append(prev)
            self._stack = carried
        elif r.get("error") not in ("no_title",) and not self._discord_open():
            # Discord isn't open: the presence starts by itself as soon as it is
            self._pending = {"payload": payload, "proc": e.get("proc"), "since": time.time(), "retry_at": 0.0}
            r = {"waiting": True, "title": e["title"]}
        r["recent"] = self._cfg["recent"]  # the page shows the new order without waiting for anything else
        return r

    def _discord_open(self):
        return any(n.startswith(DISCORD_CLIENTS) for n in self._running_names())

    def _check_pending(self):
        """Starts a presence that was waiting for Discord, once Discord is open."""
        p = self._pending
        if not p:
            return
        if self._running or self._starting:  # something else already has the presence
            self._pending = None
            return
        now = time.time()
        names = self._running_names()
        game_gone = p["proc"] and p["proc"].lower() not in names and now - p["since"] > 120
        if game_gone or now - p["since"] > 6 * 3600:  # the game closed (or it was forgotten...): nothing to show
            self._pending = None
            self._push("info", "The game closed before Discord opened. Nothing to show.")
            return
        if now < p["retry_at"] or not any(n.startswith(DISCORD_CLIENTS) for n in names):
            return
        self.set_program(p["proc"])  # so the presence ends with the game, whatever was picked in the meantime
        r = self.start(dict(p["payload"]), early_record=False)
        if r.get("ok"):
            self._pending = None
            self._push("pending_started", self._view(p["payload"], r["started_at"], p["payload"]))
        elif r.get("error") in ("no_title",):
            self._pending = None
        else:
            p["retry_at"] = time.time() + 8  # Discord is still loading: try again shortly
            self._pending = p

    def get_recent(self):
        return self._cfg["recent"]

    # ---- saved apps
    def save_app(self, entry):
        proc = str(entry.get("proc") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not proc or not title:
            return {"error": "Nothing to save."}
        icon = entry.get("icon") or None
        if icon:
            try:
                icon = self._prepare({"icon": icon, "icon_src": entry.get("icon_src"), "icon_crop": entry.get("icon_crop")})["icon"]
            except Exception as e:
                return {"error": str(e)}
        new = {"proc": proc, "exe": str(entry.get("exe") or ""), "title": title, "icon": icon,
               "extra": entry.get("extra") or None, "buttons": clean_buttons(entry.get("buttons")), "banner": str(entry.get("banner") or "") or None,
               "ref": clean_ref(entry.get("ref")), "enabled": True}
        if entry.get("icon_src") and isinstance(entry.get("icon_crop"), dict):  # keep the crop so it can be adjusted later
            new["icon_src"] = entry["icon_src"]
            new["icon_crop"] = entry["icon_crop"]
        apps, verb = self._cfg["apps"], "Saved"
        for i, old in enumerate(apps):
            if old["proc"].lower() == proc.lower():  # same program -> update it
                new["enabled"] = old.get("enabled", True)
                apps[i] = new
                verb = "Updated"
                break
        else:
            apps.append(new)
        self._record_recent(new, {"proc": proc, "exe": new["exe"], "ref": new.get("ref")}, touch=False)
        save_config(self._cfg)
        live = (self._auto_entry or {}).get("proc", "").lower() == proc.lower() or (self._game_proc or "").lower() == proc.lower()
        if self._running and live:  # this app's presence is on right now: show the change at once
            try:
                if self._auto_entry is not None:
                    self._auto_entry = new
                self._send(self._prepare({"title": new["title"], "icon": new["icon"], "extra": new["extra"],
                                          "buttons": new["buttons"], "banner": new.get("banner")}))
            except Exception:
                pass
        return {"verb": verb, "apps": apps}

    def toggle_app(self, proc, enabled):
        for a in self._cfg["apps"]:
            if a["proc"].lower() == str(proc).lower():
                a["enabled"] = bool(enabled)
        save_config(self._cfg)
        return True

    def delete_app(self, proc):
        self._cfg["apps"] = [a for a in self._cfg["apps"] if a["proc"].lower() != str(proc).lower()]
        save_config(self._cfg)
        return self._cfg["apps"]

    # ---- auto-detect: saved apps turn the presence on by themselves
    def _start_background(self):
        threading.Thread(target=self._auto_loop, daemon=True).start()
        threading.Thread(target=self._update_check_later, daemon=True).start()
        threading.Thread(target=self._warm_banners, daemon=True).start()

    def _auto_loop(self):
        time.sleep(3)
        ticks = 0
        while True:
            try:
                self._auto_check()
            except Exception:
                pass
            try:
                self._track_playtime()
            except Exception:
                pass
            try:
                self._check_pending()
            except Exception:
                pass
            ticks += 1
            if ticks == 2 or (self._tray is not None and ticks % 12 == 0):  # after start-up, and now and then while in the tray
                trim_memory()
            time.sleep(5)

    def _auto_check(self):
        apps = [a for a in self._cfg["apps"] if a.get("enabled", True)]
        if not apps and self._auto_entry is None:
            self._auto_known = set()
            return
        running = self._running_names()
        self._auto_dismissed &= running  # a program stopped by hand is re-armed once it has closed
        saved = {a["proc"].lower(): a for a in apps if a["proc"].lower() in running}
        new = [p for p in saved if p not in self._auto_known and p not in self._auto_dismissed]
        self._auto_known = set(saved)

        if self._auto_entry is not None:
            if self._running and self._auto_entry["proc"].lower() not in running:
                self._auto_end(running)  # an auto-started presence ends when its program closes
            elif new and self._cfg["auto_detect"] and self._running:
                self._auto_takeover(saved[new[0]])  # a newer saved app takes over
            return

        if not self._cfg["auto_detect"] or self._starting:
            return
        if time.time() < self._auto_retry_at:
            return
        if self._running:  # a presence started by hand: only an app that just opened takes over
            if new:
                self._auto_takeover(saved[new[0]])
            return
        for proc, entry in saved.items():
            if proc not in self._auto_dismissed:
                self._auto_start(entry)
                return

    @staticmethod
    def _view(src, started_at, entry=None):
        """What the page shows for a presence it didn't start itself."""
        e = entry or src
        return {"title": src.get("title"), "icon": src.get("icon"), "extra": src.get("extra"),
                "buttons": src.get("buttons") or [], "banner": src.get("banner"), "proc": (entry or {}).get("proc"), "exe": e.get("exe"),
                "ref": src.get("ref") or e.get("ref"),
                "started_at": started_at}

    def _fall_back(self, running):
        """Shows the most recent earlier presence whose program is still running. False if there is none."""
        while self._stack:  # newest first
            frame = self._stack.pop()
            if frame["entry"] is not None:
                valid = frame["entry"]["proc"].lower() in running
            else:
                valid = bool(frame["payload"]) and (not frame["game_proc"] or frame["game_proc"].lower() in running)
            if not valid:
                continue
            try:
                self._auto_entry = frame["entry"]
                self._game_proc = frame["game_proc"]
                self._seen_running = bool(frame["game_proc"])
                self._started_at = frame["started_at"]
                self._send(frame["payload"])
                shown = frame["entry"]
                if shown is None and frame["game_proc"]:  # the page also needs to know which program this is
                    gp = frame["game_proc"].lower()
                    rec = next((r for r in self._cfg["recent"] if (r.get("proc") or "").lower() == gp), None)
                    shown = {"proc": frame["game_proc"], "exe": (rec or {}).get("exe")}
                self._push("resumed", self._view(frame["payload"], self._started_at, shown))
                self._promote((shown or {}).get("proc"))
                return True
            except Exception:
                continue
        return False

    def _auto_end(self, running):
        entry = self._auto_entry
        if self._fall_back(running):
            return
        self.stop(by_user=False)
        self._push("stopped", f"{entry['title']} closed. Presence stopped.")

    def _auto_takeover(self, entry):
        prev = {"entry": self._auto_entry, "payload": self._last_payload, "game_proc": self._game_proc,
                "started_at": self._started_at}
        try:
            payload = self._prepare({"title": entry["title"], "icon": entry.get("icon"), "extra": entry.get("extra"),
                                     "buttons": entry.get("buttons"), "banner": entry.get("banner"), "exe": entry.get("exe"),
                                     "icon_src": entry.get("icon_src"), "icon_crop": entry.get("icon_crop")})
            self._started_at = self._game_started(entry.get("proc")) or int(time.time())
            self._send(payload)
        except Exception:
            self._started_at = prev["started_at"]
            self._last_payload = prev["payload"]
            self._auto_known.discard(entry["proc"].lower())
            self._auto_retry_at = time.time() + 30
            return
        self._stack.append(prev)
        self._auto_entry = entry
        self._game_proc = None
        self._push("auto_started", self._view(entry, self._started_at, entry))
        self._promote(entry["proc"])

    def _auto_start(self, entry):
        self._auto_entry = entry
        r = self.start({"title": entry["title"], "icon": entry.get("icon"), "extra": entry.get("extra"),
                        "buttons": entry.get("buttons"), "banner": entry.get("banner"), "exe": entry.get("exe"),
                        "proc": entry.get("proc"), "icon_src": entry.get("icon_src"), "icon_crop": entry.get("icon_crop"),
                        "ref": entry.get("ref")}, early_record=False)
        if r.get("error"):  # most likely Discord isn't open yet: keep trying quietly
            self._auto_entry = None
            self._auto_retry_at = time.time() + 8
            if self._waiting_for != entry["proc"]:
                self._waiting_for = entry["proc"]
                self._record_recent(entry, {"proc": entry["proc"], "exe": entry.get("exe"), "ref": entry.get("ref")})  # once per detection
                self._push("info", f"{entry['title']} detected. Waiting for Discord…")
            return
        self._waiting_for = None
        self._push("auto_started", self._view(entry, r["started_at"], entry))

    # ---- search
    def search(self, term):
        term = (term or "").strip()
        enabled = [k for k in SOURCES if self._cfg["sources"].get(k)]
        if not term:
            return {"results": [], "hidden": 0, "failed": [], "relaxed": []}
        if not enabled:
            return {"error": "Turn on at least one search source in Settings."}
        self._search_id += 1
        results, hidden, failed, relaxed = [], 0, [], []
        ex = ThreadPoolExecutor(max_workers=len(enabled))
        futures = [(k, ex.submit(run_source, SOURCES[k][1], term, self._cfg["nsfw"])) for k in enabled]
        for key, fut in futures:
            try:
                res, h, used = fut.result()
                results.extend(asdict(r) for r in res)
                hidden += h
                if used and res:
                    relaxed.append(f"{SOURCES[key][0]}: \"{used}\"")
            except Exception as e:
                failed.append(f"{SOURCES[key][0]} ({why(e)})")
        ex.shutdown()
        return {"results": results, "hidden": hidden, "failed": failed, "relaxed": relaxed}

    # ---- running programs
    def programs(self, show_all=False):
        try:
            return list_programs(bool(show_all))
        except Exception:
            return []

    def program_icon(self, exe):
        img = extract_icon(exe, 64)
        return png_data_uri(img) if img else None

    def browse(self):
        if self._window is None:
            return None
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        paths = self._window.create_file_dialog(kind, file_types=("Programs (*.exe)", "All files (*.*)"))
        if not paths:
            return None
        path = paths[0] if isinstance(paths, (list, tuple)) else paths
        return {"name": Path(path).name, "exe": str(path), "title": ""}

    def set_program(self, name):
        self._game_proc = name or None
        self._seen_running = False
        return clean_name(Path(name).stem) if name else ""

    # ---- icon editing
    def apply_icon(self, src, view):
        """Turn the crop the page shows into a URL Discord can load."""
        if src.startswith("data:") and not self._cfg.get("imgbb_key"):
            return {"error": NEED_KEY}
        try:
            img = load_source(src)
        except Exception as e:
            return {"error": f"Couldn't load the image: {e}"}
        view = {"x": float(view["x"]), "y": float(view["y"]), "zoom": float(view["zoom"]), "rot": int(view["rot"]) % 360}
        if self._cfg.get("imgbb_key"):
            try:
                return {"url": self._host_png(to_png(render_crop(img, crop_square(img.size, view), view["rot"], ICON_SIZE)))}
            except Exception as e:
                return {"error": str(e)}
        try:
            url, variant = verify_icon_url(src, img, view, self._proxy_variant)
            self._proxy_variant = variant
            return {"url": url}
        except ProxyRefused:
            return {"error": "The image service can't read this picture. Add an ImgBB API key in Settings to host it."}
        except Exception as e:
            return {"error": str(e)}
    # ---- Discord presence
    def start(self, payload, early_record=True):
        app_id = self._app_id()
        if not (payload.get("title") or "").strip():
            return {"error": "no_title"}
        if self._running or self._starting:
            return {"error": "already_running"}
        self._pending = None  # a new start replaces any presence that was waiting for Discord
        orig = payload
        try:
            payload = self._prepare(payload)
        except Exception as e:
            return {"error": str(e)}
        if early_record:  # a game counts as played even if Discord isn't open and the presence can't start yet C_C
            self._record_recent(payload, orig)
        self._starting = True
        try:
            self._rpc = Presence(app_id)
            self._rpc.connect()
            self._started_at = self._game_started(orig.get("proc")) or int(time.time())
            self._send(payload)
        except Exception as e:
            self._rpc = None
            self._starting = False
            return {"error": f"Couldn't start: {e}"}
        self._starting = False
        self._running = True
        self._lost = False
        self._refresh_tray()  # the tray menu was built before this: enable "Stop presence"
        self._monitor_id += 1
        threading.Thread(target=self._monitor, args=(self._monitor_id,), daemon=True).start()
        if not early_record:
            self._record_recent(payload, orig)
        return {"ok": True, "started_at": self._started_at}

    def update(self, payload):
        if not self._running:
            return {"error": "not_running"}
        try:
            sent = self._prepare(payload)
            self._send(sent)
            self._record_recent(sent, payload, touch=False)
            return {"ok": True}
        except Exception as e:
            return {"error": f"Update failed: {e}"}

    def stop(self, quiet=False, by_user=True):
        if by_user:
            self._pending = None  # stopping also cancels a presence that was waiting for Discord
        if by_user and self._auto_entry is not None:  # stopped by hand: don't restart until the program closes
            self._auto_dismissed.add(self._auto_entry["proc"].lower())
        self._auto_entry = None
        self._stack = []
        was = self._running
        self._running = False
        self._started_at = None
        if self._rpc:
            try:
                self._rpc.clear()
                self._rpc.close()
            except Exception:
                pass
            self._rpc = None
        self._current_title = ""
        self._refresh_tray()
        return {"ok": True, "was_running": was}

    def _send(self, p):
        self._last_payload = p
        title = clip_text(p["title"])
        self._current_title = title
        self._refresh_tray()
        extra = clip_text(p["extra"]) if p.get("extra") else None
        params = inspect.signature(self._rpc.update).parameters
        has_name = "name" in params
        kw = {
            "name": title if has_name else None,  # newer pypresence/Discord: custom activity name
            "details": None if has_name else title,
            "state": extra,
            "large_image": p.get("icon") or None,
            "large_text": title,
            "start": self._started_at if self._cfg["show_timer"] else None,
            "status_display_type": None if has_name else 2,
            "buttons": clean_buttons(p.get("buttons")) or None,
        }
        self._rpc.update(**{k: v for k, v in kw.items() if v is not None and k in params})

    def _monitor(self, my_id):
        """While the presence is on: stops it when the chosen program closes, and brings it back if Discord restarts."""
        while self._running and my_id == self._monitor_id:
            time.sleep(5)
            if not self._running or my_id != self._monitor_id:  # stopped, or a newer presence has its own monitor
                return
            names = self._running_names()
            self._check_discord(any(n.startswith(DISCORD_CLIENTS) for n in names))
            if not (self._running and self._cfg["auto_stop"] and self._game_proc):
                continue
            if self._game_proc.lower() in names:
                self._seen_running = True
            elif self._seen_running:
                if self._fall_back(names):  # another game is still open: show that one
                    continue
                self.stop(by_user=False)
                self._push("stopped", "Program closed. Presence stopped.")
                return

    def _check_discord(self, discord_up):
        if not discord_up:
            if not self._lost:
                self._lost = True
                self._push("info", "Discord closed. Presence will return when it opens again.")
            return
        if not self._lost:
            return
        try:  # Discord is back: connect again and show the same thing as before
            old, self._rpc = self._rpc, None
            if old:
                try:
                    old.close()
                except Exception:
                    pass
            rpc = Presence(self._app_id())
            rpc.connect()
            self._rpc = rpc
            if self._last_payload:
                self._send(self._last_payload)
            self._lost = False
            self._push("info", "Discord is back. Presence restored.")
        except Exception:
            self._rpc = None  # its connection isn't ready yet; try again on the next check


def main():
    import webview

    # a leaner web view: one renderer, no background services it doesn't need
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                          "--renderer-process-limit=1 --disable-background-networking --disable-extensions "
                          "--disable-sync --disable-component-update --disable-features=Translate "
                          "--js-flags=--max-old-space-size=64")

    single = SingleInstance()
    if not single.is_primary:
        return
    api = Api()
    hidden = START_HIDDEN and api._cfg["close_action"] == "background" and pystray is not None
    window = webview.create_window(
        APP_NAME, str(resource_path("index.html")), js_api=api,
        width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], resizable=False, frameless=True, easy_drag=False,
        background_color="#140C11", hidden=hidden, minimized=START_HIDDEN and not hidden,
    )
    api._window = window
    single.listen(api._show_window)

    def ready():
        if get_startup_value() and getattr(sys, "frozen", False) and get_startup_value() != startup_command():
            try:  # the packaged .exe was moved: keep the startup entry pointing at it
                set_startup(True)
            except OSError:
                pass
        if hidden:
            api._start_tray()
        api._start_background()

    webview.start(ready)


if __name__ == "__main__":
    main()