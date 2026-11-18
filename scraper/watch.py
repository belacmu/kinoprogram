"""Where films can be watched at home (streaming, free, with ads, rent, buy): helpers for the details step (details.py), whose one
TMDB request per film also gives its services and release dates for every country. Kept in Supabase's watch_options, one row per
country the site covers (also when there's nothing), the country's digital release date in options["digital"] ("YYYY-MM-DD": the
next one if there's one ahead, else the latest; "" if none). TMDB's data comes from JustWatch, which the site credits.

How often a film is checked is the details step's schedule (details.next_fetch).
"""
from datetime import datetime, timezone


DIGITAL = 4  # TMDB's release type for a digital release
KINDS = (("flatrate", "stream"), ("free", "free"), ("ads", "ads"), ("rent", "rent"), ("buy", "buy"))


def options_for(country_data):
    """TMDB's entry for one country -> ({kind: [{name, logo}]}, link). Services in TMDB's order, each once per kind."""
    out = {}
    for src, kind in KINDS:
        seen, items = set(), []
        for p in sorted(country_data.get(src) or [], key=lambda p: p.get("display_priority", 999)):
            name = (p.get("provider_name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                items.append({"name": name, "logo": p.get("logo_path") or ""})
        if items:
            out[kind] = items
    link = country_data.get("link") or ""
    return out, link if link.startswith("https://") else ""


def digital_date(release_dates, country, today):
    """The country's digital release date from TMDB's release_dates: the next one ahead of today, else the latest; "" if none."""
    days = sorted({(d.get("release_date") or "")[:10] for r in release_dates if r.get("iso_3166_1") == country
                   for d in r.get("release_dates") or [] if d.get("type") == DIGITAL and (d.get("release_date") or "")[:10]})
    ahead = [d for d in days if d > today]
    return ahead[0] if ahead else (days[-1] if days else "")


def states_from(rows):
    """watch_options rows -> {film id: {checked (the oldest of its rows), found (services in any country), digital: [dates]}}."""
    out = {}
    for r in rows:
        st = out.setdefault(r["film_id"], {"checked": _when(r["checked_at"]), "found": False, "digital": []})
        st["checked"] = min(st["checked"], _when(r["checked_at"]))
        opts = r.get("options") or {}
        st["found"] |= any(opts.get(k) for _, k in KINDS)
        if opts.get("digital"):
            st["digital"].append(opts["digital"])
    return out


def _when(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


