"""Costa del Sol (Fuengirola / Marbella, near Calahonda) sources.

CarteleraCines.es (used with its owners' permission for this small personal project) aggregates the
Spanish ticketing providers and publishes sessions through a read-only JSON-RPC endpoint (MCP). One
call per cinema returns up to 14 days of sessions: time, version (`VOSE` = original language with
Spanish subtitles, empty = the default version) and a ticket link. Called twice a day, a handful of
requests each time.
"""
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import film, get  # noqa: E402

ENDPOINT = "https://www.carteleracines.es/mcp_server.php"
SITE = "https://www.carteleracines.es"
MAX_DAYS = 14          # the longest range the endpoint accepts
PAUSE = 1.0            # seconds between requests
# CarteleraCines name -> how we show it. Red Dog (Marbella) has no sessions there yet; it is picked up
# automatically if that changes.
CINEMAS = {
    "Multicines Alfil Fuengirola": "Alfil (Fuengirola)",
    "mk2 Cinesur Miramar": "mk2 Miramar (Fuengirola)",
    "Kinépolis La Cañada": "Kinépolis La Cañada (Marbella)",
    "Red Dog Cinemas": "Red Dog (Puerto Banús)",
}
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
           "User-Agent": "kinoprogram (small personal project, twice a day; +https://github.com/belacmu/kinoprogram)"}
ORIGINAL = {"VOSE", "VO", "V.O.", "VOS"}


def call(tool, arguments):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}}).encode()
    reply = json.loads(get(ENDPOINT, body, HEADERS, timeout=60))
    if "error" in reply:
        raise RuntimeError(reply["error"].get("message", "request failed"))
    data = json.loads(reply["result"]["content"][0]["text"])
    if not data.get("ok"):
        raise RuntimeError(str(data)[:200])
    return data["data"]


def fetch_all(now):
    start, end = now.strftime("%Y-%m-%d"), (now + timedelta(days=MAX_DAYS - 1)).strftime("%Y-%m-%d")
    films = {}
    for i, (source_name, name) in enumerate(CINEMAS.items()):
        if i:
            time.sleep(PAUSE)
        try:
            data = call("get_cartelera_cine", {"cine": source_name, "fecha_inicio": start, "fecha_fin": end})
        except Exception as e:  # one cinema failing must not empty the region
            print(f"  ! {name}: {e}", file=sys.stderr)
            continue
        count = 0
        for p in data.get("peliculas") or []:
            f = films.get(p["slug"])
            if not f:
                release = (p.get("fecha_estreno") or "")[:10]
                poster = p.get("cartel") or ""
                f = films[p["slug"]] = film(
                    title=p["titulo"], year=release[:4], runtime=p.get("duracion") or 0,
                    genres=[g.strip() for g in (p.get("generos_texto") or "").split(",") if g.strip()],
                    director=p.get("directores_texto") or "",
                    poster=(SITE + poster) if poster.startswith("/") else poster,
                    links=[{"label": "CarteleraCines", "url": f"{SITE}/#{p['slug']}"}],
                    premiere=release, premiereConfirmed=bool(release),
                )
            for c in p["cines"].values():
                for s in c["sesiones"]:
                    version = s.get("version") or ""
                    f["shows"].append({
                        "t": s["fecha_hora"][:16].replace(" ", "T"), "cinema": name, "screen": "",
                        "tags": ["Original language"] if version.upper() in ORIGINAL else [],
                        "note": "Spanish subtitles" if version.upper() in ORIGINAL else "", "ticket": s.get("urlticket") or "",
                        "status": "", "dub": False, "en": version.upper() in ORIGINAL,  # `en` = original language here
                    })
                    count += 1
        print(f"  {name}: {count} sessions")
    return list(films.values())


LANG_NAMES = {"en": "English", "es": "Spanish", "no": "Norwegian", "nb": "Norwegian", "sv": "Swedish", "da": "Danish",
              "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese", "ja": "Japanese", "ko": "Korean",
              "zh": "Chinese", "hi": "Hindi", "ru": "Russian", "nl": "Dutch", "pl": "Polish", "tr": "Turkish"}


def mark_dubbed(films):
    """Say what language each showing is in. A showing without the original-version tag is dubbed into Spanish,
    unless the film is Spanish-language. The film's original language comes from TMDB (ext.lang); when it is
    unknown the showing isn't called dubbed and its audio language is left blank.
    Sets on each show: `dub`, `lang` (audio language code, "" if unknown) and the visible tag."""
    for f in films:
        lang = (f.get("ext") or {}).get("lang") or ""
        name = LANG_NAMES.get(lang, "")
        for s in f["shows"]:
            s["tags"] = [t for t in s["tags"] if t != "Original language"]
            s["dub"] = bool(lang) and lang != "es" and not s["en"]
            s["lang"] = "es" if s["dub"] else lang
            if s["en"]:  # original version, Spanish subtitles
                s["tags"].append("English audio" if lang == "en" else f"{name} audio" if name else "Original language")
