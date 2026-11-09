"""10-day daily forecast for Cologne from MET Norway (yr.no) -> out/wetter.json."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

API = "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=50.94&lon=6.96"
YR_URL = ("https://www.yr.no/en/forecast/daily-table/2-2886242/Germany/North%20Rhine-Westphalia/"
          "Cologne%20District/Cologne")
TZ = ZoneInfo("Europe/Berlin")
DAYS = 10

TEXT = {
    "clearsky": "Sonnig", "fair": "Überwiegend sonnig", "partlycloudy": "Teilweise bewölkt",
    "cloudy": "Bewölkt", "fog": "Nebel",
    "lightrain": "Leichter Regen", "rain": "Regen", "heavyrain": "Starker Regen",
    "lightrainshowers": "Leichte Regenschauer", "rainshowers": "Regenschauer",
    "heavyrainshowers": "Starke Regenschauer",
    "lightsleet": "Leichter Schneeregen", "sleet": "Schneeregen", "heavysleet": "Starker Schneeregen",
    "lightsleetshowers": "Leichte Schneeregenschauer", "sleetshowers": "Schneeregenschauer",
    "heavysleetshowers": "Starke Schneeregenschauer",
    "lightsnow": "Leichter Schneefall", "snow": "Schneefall", "heavysnow": "Starker Schneefall",
    "lightsnowshowers": "Leichte Schneeschauer", "snowshowers": "Schneeschauer",
    "heavysnowshowers": "Starke Schneeschauer",
}
SEVERITY = {"sonne": 0, "teils": 1, "wolke": 2, "regen": 3}


def base_code(symbol: str) -> str:
    return symbol.split("_")[0]  # strip _day/_night/_polartwilight


def icon(code: str) -> str:
    if any(k in code for k in ("rain", "sleet", "snow", "showers", "thunder")):
        return "regen"
    if code == "clearsky":
        return "sonne"
    if code in ("fair", "partlycloudy"):
        return "teils"
    return "wolke"  # cloudy, fog, unknown


def german(code: str) -> str:
    if code in TEXT:
        return TEXT[code]
    if "thunder" in code:
        return "Gewitter" if "showers" not in code else "Gewitterschauer"
    return code


def build(data: dict, today: date) -> dict:
    temps: dict[date, list[float]] = defaultdict(list)
    precip: dict[date, float] = defaultdict(float)
    symbols: dict[date, list[str]] = defaultdict(list)
    series = data["properties"]["timeseries"]
    for ts in series:
        t = datetime.fromisoformat(ts["time"].replace("Z", "+00:00")).astimezone(TZ)
        d = t.date()
        det = ts.get("data", {})
        inst = det.get("instant", {}).get("details", {})
        if "air_temperature" in inst:
            temps[d].append(inst["air_temperature"])
        if "next_1_hours" in det:
            nxt, hours = det["next_1_hours"], 1
        elif "next_6_hours" in det:
            nxt, hours = det["next_6_hours"], 6
        else:
            continue
        precip[d] += nxt.get("details", {}).get("precipitation_amount", 0.0) or 0.0
        sym = nxt.get("summary", {}).get("symbol_code")
        if sym and 6 <= t.hour <= 20:  # daytime weather decides the icon
            symbols[d].extend([base_code(sym)] * hours)

    tage = {}
    for i in range(DAYS):
        d = today + timedelta(days=i)
        if not temps.get(d):
            continue
        codes = symbols.get(d) or []
        mm = round(precip.get(d, 0.0), 1)
        if codes:
            cats = Counter(icon(c) for c in codes)
            rain_share = cats.get("regen", 0) / len(codes)
            if mm >= 1.0 or rain_share >= 0.3:
                cat = "regen"
            else:
                cats.pop("regen", None)
                cat = max(cats, key=lambda k: (cats[k], SEVERITY[k])) if cats else "wolke"
            code = Counter(c for c in codes if icon(c) == cat).most_common(1)
            if code:
                w = german(code[0][0])
                if mm >= 8 and w.startswith("Leicht"):
                    w = w.split(" ", 1)[1].capitalize()
            else:  # rain only by amount, symbols showed none
                w = "Leichter Regen" if mm < 3 else "Regen"
        else:
            cat, w = ("regen", "Regen") if mm >= 1.0 else ("wolke", "Bewölkt")
        tage[d.isoformat()] = {"w": w, "i": cat, "max": round(max(temps[d])), "min": round(min(temps[d])),
                               "mm": mm}
    return {"quelle": "MET Norway (yr.no)", "url": YR_URL, "stand": today.isoformat(), "tage": tage}


def fetch(fetcher, today: date) -> tuple[dict | None, str | None]:
    r = fetcher.get_api(API)
    if not r.ok:
        return None, r.error
    try:
        data = json.loads(r.content)
        out = build(data, today)
    except (ValueError, KeyError, TypeError) as exc:
        return None, f"Antwort nicht lesbar: {type(exc).__name__}"
    if not out["tage"]:
        return None, "keine Tage in der Vorhersage"
    return out, None
