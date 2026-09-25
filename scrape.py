"""Stáhne akce z webů v Praze 7 a okolí a uloží je do web/events.js (+ events.json).

Spuštění:  python scrape.py
Pak otevři web/index.html v prohlížeči.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (akce-praha7 bot; osobni pouziti)"
OUT_DIR = Path(__file__).parent / "web"
TODAY = date.today()
PRAGUE = ZoneInfo("Europe/Prague")


@dataclass
class Event:
    title: str
    start: str               # ISO datum nebo datum+čas
    end: str | None
    venue: str
    url: str
    source: str
    category: str | None = None
    price: str | None = None


# ---------------------------------------------------------------- helpers

session = requests.Session()
session.headers["User-Agent"] = UA


def get(url: str) -> BeautifulSoup:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    if r.encoding and r.encoding.lower() == "iso-8859-1":  # server neposlal charset
        r.encoding = "utf-8"
    time.sleep(1)  # slušná pauza mezi requesty
    return BeautifulSoup(r.text, "html.parser")


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


MONTHS = {  # genitiv, jak se píše v datech ("1. května 2026")
    "ledna": 1, "února": 2, "března": 3, "dubna": 4, "května": 5, "června": 6,
    "července": 7, "srpna": 8, "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
}


def infer_year(d: int, m: int) -> date:
    """Datum bez roku: pokud je víc než ~2 měsíce v minulosti, patří do dalšího roku."""
    dt = date(TODAY.year, m, d)
    if (TODAY - dt).days > 60:
        dt = date(TODAY.year + 1, m, d)
    return dt


def parse_cz_range(text: str) -> tuple[date, date | None] | None:
    """'19. 6. – 8. 11. 2026', '25. 9. 2026', '1. května 2026 - 31. října 2026', '23. 9.'"""
    t = text
    for name, num in MONTHS.items():
        t = re.sub(rf"(\d{{1,2}})\.\s*{name}", rf"\1. {num}.", t)
    parts = re.findall(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d\d)?", t)
    if not parts:
        return None
    last_year = next((int(y) for _, _, y in reversed(parts) if y), None)

    def mk(d, m, y):
        if y:
            return date(int(y), int(m), int(d))
        if last_year:
            return date(last_year, int(m), int(d))
        return infer_year(int(d), int(m))

    start = mk(*parts[0])
    end = mk(*parts[1]) if len(parts) > 1 else None
    if end and end < start:  # "20. 12. – 5. 1. 2027"
        start = start.replace(year=end.year - 1)
    return start, end


def with_time(d: date, text: str | None) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return f"{d.isoformat()}T{int(m[1]):02d}:{m[2]}" if m else d.isoformat()


# ---------------------------------------------------------------- adaptéry
# Každý adaptér vrací list[Event]. Když web změní HTML, spadne jen ten jeden.

def biooko() -> list[Event]:
    """Bio Oko má akce jako schema.org/Event v JSON-LD – nejspolehlivější varianta."""
    s = get("https://www.biooko.net/")
    out = []
    for tag in s.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        for e in d if isinstance(d, list) else [d]:
            if e.get("@type") != "Event":
                continue
            offer = e.get("offers") or {}
            out.append(Event(
                title=clean(e["name"]),
                start=e["startDate"][:16],
                end=(e.get("endDate") or "")[:16] or None,
                venue="Bio Oko",
                url=e.get("url", "https://www.biooko.net/"),
                source="biooko",
                category="Kino",
                price=f"{offer['price']} Kč" if offer.get("price") else None,
            ))
    return out


def jatka78() -> list[Event]:
    s = get("https://www.jatka78.cz/cs/program")
    out = []
    for it in s.select(".show__main"):
        a = it.select_one(".show__title a")
        d = parse_cz_range(it.select_one(".show__date").get_text())
        if not a or not d:
            continue
        where = clean(it.select_one(".show__where").get_text()) if it.select_one(".show__where") else ""
        tm = it.select_one(".show__time")
        out.append(Event(
            title=clean(a.get_text()),
            start=with_time(d[0], tm.get_text() if tm else None),
            end=None,
            venue=f"Jatka78 – {where}" if where else "Jatka78",
            url=urljoin("https://www.jatka78.cz/", a["href"]),
            source="jatka78",
            category="Divadlo / cirkus",
        ))
    return out


def nzm() -> list[Event]:
    """Národní zemědělské muzeum – bere jen pobočku Praha (ostatní jsou mimo Prahu)."""
    s = get("https://www.nzm.cz/aktualne-v-muzeu/akce")
    out = []
    for it in s.select(".articleCont"):
        labels = [clean(x.get_text()) for x in it.select(".labels__item")]
        if not labels or labels[0] != "NZM Praha":
            continue
        a = it.select_one(".articleHdr__link")
        t = it.select_one("time[datetime]")
        if not a or not t:
            continue
        out.append(Event(
            title=clean(a.get("title") or a.get_text()),
            start=t["datetime"],
            end=None,
            venue="Národní zemědělské muzeum",
            url=urljoin("https://www.nzm.cz/", a["href"]),
            source="nzm",
            category=labels[1] if len(labels) > 1 else None,
        ))
    return out


def dox() -> list[Event]:
    s = get("https://www.dox.cz/program")
    out = []
    for it in s.select(".entry-inner"):
        a = it.select_one(".entry-title a")
        term = it.select_one(".entry-meta-term")
        d = parse_cz_range(term.get_text()) if term else None
        if not a or not d:
            continue
        cat = it.select_one(".entry-meta-category")
        out.append(Event(
            title=clean(a.get_text()),
            start=with_time(d[0], term.get_text() if not d[1] else None),
            end=d[1].isoformat() if d[1] else None,
            venue="DOX",
            url=a["href"],
            source="dox",
            category=clean(cat.get_text()) if cat else None,
        ))
    return out


def vystaviste() -> list[Event]:
    s = get("https://navystavisti.cz/program/")
    out = []
    for it in s.select(".program-list-item"):
        title = it.select_one(".title")
        dt = it.select_one(".date")
        d = parse_cz_range(dt.get_text()) if dt else None
        if not title or not d:
            continue
        out.append(Event(
            title=clean(title.get_text()),
            start=with_time(d[0], dt.get_text()),
            end=d[1].isoformat() if d[1] else None,
            venue="Výstaviště Praha",
            url=urljoin("https://navystavisti.cz/", it.select_one("a")["href"]),
            source="vystaviste",
        ))
    return out


def praha7() -> list[Event]:
    s = get("https://www.praha7.cz/kalendar/")
    out = []
    for it in s.select(".tab-content-item"):
        title = it.select_one(".event-title")
        spans = it.select(".event-date-interval span")
        if not title or not spans:
            continue
        d = parse_cz_range(" – ".join(x.get_text() for x in spans))
        info = it.select_one(".inline-item-data")
        info_txt = clean(info.get_text()) if info else ""
        link = it.select_one("a[href*='event/?id=']")
        out.append(Event(
            title=clean(title.get_text()),
            start=with_time(d[0], info_txt),
            end=d[1].isoformat() if d[1] and d[1] != d[0] else None,
            venue=re.sub(r"^od \d{1,2}:\d{2},\s*", "", info_txt) or "Praha 7",
            url=urljoin("https://www.praha7.cz/kalendar/", link["href"]) if link else "https://www.praha7.cz/kalendar/",
            source="praha7",
            category=", ".join(clean(x.get_text()) for x in it.select(".category-list li")) or None,
        ))
    return out


def studiohrdinu() -> list[Event]:
    """Studio Hrdinů má u každé akce data-date + schema.org mikrodata."""
    s = get("https://studiohrdinu.cz/cs/program/")
    out = []
    for li in s.select("#program li[data-date]"):
        name = li.select_one("h2[itemprop=name]")
        a = li.select_one("a[itemprop=url]")
        if not name:
            continue
        out.append(Event(
            title=clean(name.get_text()),
            start=li["data-date"][:16].replace(" ", "T"),
            end=None,
            venue="Studio Hrdinů",
            url=urljoin("https://studiohrdinu.cz/", a["href"]) if a else "https://studiohrdinu.cz/cs/program/",
            source="studiohrdinu",
            category="Divadlo",
        ))
    return out


def crossclub() -> list[Event]:
    """Program je po dnech (div.predel = hlavička dne, div.article = akce), stránkuje se ?date=."""
    out = []
    for offset in (0, 14, 28):
        day = date.fromordinal(TODAY.toordinal() + offset)
        s = get(f"https://www.crossclub.cz/cs/program/?date={day.isoformat()}")
        current = None
        for el in s.select("div.predel, div.article"):
            if "predel" in el["class"]:
                d = parse_cz_range(el.get_text())
                current = d[0] if d else None
                continue
            a = el.select_one("h2 a")
            if not current or not a:
                continue
            cat = el.select_one(".category")
            out.append(Event(
                title=clean(a.get_text()),
                start=current.isoformat(),
                end=None,
                venue="Cross Club",
                url=a["href"],
                source="crossclub",
                category=clean(cat.get_text()).split(" - ")[0].rstrip(":") if cat else None,
            ))
    return out


def trznice() -> list[Event]:
    """Holešovická tržnice (Webflow). Data typu '10.9.2026 – 25.10.26'."""
    s = get("https://www.holesovickatrznice.cz/program")
    out = []
    for card in s.select("a.event-card"):
        txt = clean(card.get_text(" "))
        txt = re.sub(r"(\d{1,2}\.\d{1,2}\.)(\d\d)\b", r"\g<1>20\2", txt)  # 26 -> 2026
        d = parse_cz_range(txt)
        title = card.select_one("h2, h3, h4, .event-title, .heading")
        if not d:
            continue
        out.append(Event(
            title=clean(title.get_text()) if title else txt[:80],
            start=d[0].isoformat(),
            end=d[1].isoformat() if d[1] and d[1] != d[0] else None,
            venue="Holešovická tržnice",
            url=urljoin("https://www.holesovickatrznice.cz/", card["href"]),
            source="trznice",
        ))
    return out


def forumkarlin() -> list[Event]:
    s = get("https://www.forumkarlin.cz/program/")
    out = []
    for ev in s.select("div.event"):
        if "cancel" in ev["class"]:
            continue
        a = ev.select_one("h3 a")
        dt = ev.select_one(".date")
        if not a or not dt:
            continue
        for x in dt.select(".den"):
            x.decompose()
        d = parse_cz_range(dt.get_text())
        if not d:
            continue
        out.append(Event(
            title=clean(a.get_text()),
            start=d[0].isoformat(),
            end=None,
            venue="Forum Karlín",
            url=a["href"],
            source="forumkarlin",
            category="Koncert",
        ))
    return out


def planetarium() -> list[Event]:
    """Planetum má JSON API pro e-shop; bereme jen Planetárium Praha (Stromovka)."""
    to = date.fromordinal(TODAY.toordinal() + 90)
    r = session.get("https://shop.planetum.cz/web/program/",
                    params={"from": TODAY.isoformat(), "to": to.isoformat(), "full": 1, "lang": "cs"},
                    timeout=30)
    r.raise_for_status()
    out = []
    for it in r.json()["list"]:
        if it.get("location_name") != "Planetárium Praha":
            continue
        out.append(Event(
            title=clean(it["name"]),
            start=it["from"][:16].replace(" ", "T"),
            end=it["to"][:16].replace(" ", "T") if it.get("to") else None,
            venue="Planetárium Praha",
            url=it.get("external_url") or "https://www.planetum.cz/program",
            source="planetarium",
            category=it.get("show_type_name"),
        ))
    return out


NG_QUERY = """query ($types: [String]!, $page: Int, $limit: Int) {
  events(types: $types, page: $page, limit: $limit) {
    has_more_pages
    data { id name categories { name } seo_url_slug { cs } buildings { name }
           dates { start_at { timestamp } end_at { timestamp } } }
  }
}"""


def ngprague() -> list[Event]:
    """Národní galerie – veřejné GraphQL API webu; jen Veletržní palác (Praha 7)."""
    def ts(x):
        return datetime.fromtimestamp(int(x["timestamp"]), PRAGUE) if x else None

    out = []
    for types in (["event"], ["exhibition"]):
        page = 1
        while page <= 10:
            r = session.post("https://admin.www.ngprague.cz/graphql", timeout=30, json={
                "query": NG_QUERY, "variables": {"types": types, "page": page, "limit": 50}})
            r.raise_for_status()
            res = r.json()["data"]["events"]
            for e in res["data"]:
                if not any(b["name"] == "Veletržní palác" for b in e["buildings"]):
                    continue
                cat = ", ".join(c["name"] for c in e["categories"]) or ("Výstava" if types == ["exhibition"] else None)
                for d in e["dates"]:
                    s, en = ts(d["start_at"]), ts(d["end_at"])
                    if not s:
                        continue
                    multi = en and en.date() != s.date()
                    out.append(Event(
                        title=clean(e["name"]),
                        start=s.date().isoformat() if multi or (s.hour, s.minute) == (0, 0) else s.strftime("%Y-%m-%dT%H:%M"),
                        end=en.date().isoformat() if multi else (en.strftime("%Y-%m-%dT%H:%M") if en else None),
                        venue="Národní galerie – Veletržní palác",
                        url=f"https://www.ngprague.cz/udalost/{e['id']}/{e['seo_url_slug']['cs']}",
                        source="ngprague",
                        category=cat,
                    ))
            if not res["has_more_pages"]:
                break
            page += 1
            time.sleep(1)
    return out


def ntm() -> list[Event]:
    """NTM nemá kalendář – akce jsou aktuality s datem v titulku ('3. 10. 2026 – Parní vůz…')."""
    s = get("https://www.ntm.cz/pro-navstevniky/aktuality")
    out = []
    for a in s.select("a.articleHdr__link"):
        title = clean(a.get("title") or a.get_text())
        m = re.match(r"^(\d{1,2})\.\s*(?:a|až|-|–)\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d\d)\s*[–:-]\s*(.+)", title)
        if m:  # "9. a 10. 10. 2026 – …", "26. až 28. 9. 2026 – …"
            d1, d2, mo, y, name = m.groups()
            start = date(int(y), int(mo), int(d1))
            end = date(int(y), int(mo), int(d2))
        else:
            m = re.match(r"^(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d\d)\s*[–:-]\s*(.+)", title)
            if not m:
                continue  # zprávy bez data a „Od 1. 9. …“ (uzavírky, výstavy) přeskočíme
            d1, mo, y, name = m.groups()
            start, end = date(int(y), int(mo), int(d1)), None
        out.append(Event(
            title=clean(name),
            start=start.isoformat(),
            end=end.isoformat() if end else None,
            venue="Národní technické muzeum",
            url=urljoin("https://www.ntm.cz/", a["href"]),
            source="ntm",
        ))
    return out


def pragueeu() -> list[Event]:
    """Prague City Tourism – výstavy v celé Praze (stránkování ?pg=)."""
    out = []
    for pg in range(1, 6):
        s = get(f"https://prague.eu/en/akce-kategorie/exhibitions/?pg={pg}")
        tiles = s.select("div.tile-switching")
        for t in tiles:
            a = t.select_one("h2 a")
            p = t.select_one(".tile-switching__main p")
            d = parse_cz_range(p.get_text()) if p else None
            if not a or not d:
                continue
            venue = t.select_one(".tile-switching__afterHeading")
            cat = t.select_one(".tile-switching__beforeHeading")
            out.append(Event(
                title=clean(a.get_text()),
                start=d[0].isoformat(),
                end=d[1].isoformat() if d[1] and d[1] != d[0] else None,
                venue=clean(venue.get_text()) if venue else "Praha",
                url=a["href"],
                source="pragueeu",
                category=clean(cat.get_text()) if cat else "Výstava",
            ))
        if len(tiles) < 20 or not s.find("a", href=re.compile(rf"pg={pg + 1}")):
            break
    return out


def lafabrika() -> list[Event]:
    """Datum a čas jsou přímo v URL akce: /cs/program/pluto-2026-10-02-19-30-00."""
    s = get("https://www.lafabrika.cz/cs/program")
    out = []
    for row in s.select(".programRow"):
        a = row.find("a", href=re.compile(r"-(20\d\d-\d\d-\d\d)-(\d\d)-(\d\d)-\d\d(-\d+)?$"))
        title = row.select_one(".programRow-content-title")
        if not a or not title:
            continue
        m = re.search(r"-(20\d\d-\d\d-\d\d)-(\d\d)-(\d\d)-\d\d(-\d+)?$", a["href"])
        sub = row.select_one(".programRow-content-subtitle")
        stage = row.select_one(".programRow-status-venue")
        out.append(Event(
            title=clean(title.get_text()),
            start=f"{m[1]}T{m[2]}:{m[3]}",
            end=None,
            venue=f"La Fabrika – {clean(stage.get_text())}" if stage else "La Fabrika",
            url=urljoin("https://www.lafabrika.cz/", a["href"]),
            source="lafabrika",
            category=clean(sub.get_text()) if sub else "Divadlo",
        ))
    return out


# GoOut: pro místa bez vlastního programu. Interní JSON API (to samé volá jejich web),
# robots.txt ho nezakazuje. ID místa najdeš v DevTools (venueIds[]=…) na stránce místa.
GOOUT_VENUES = {
    8720: ("Ostrov Štvanice", "stvanice"),
    25641: ("Vnitroblock", "vnitroblock"),
    17148: ("Vnitroblock", "vnitroblock"),
    25834: ("Vnitroblock", "vnitroblock"),
    29931: ("Vnitroblock", "vnitroblock"),
    30611: ("Vnitroblock", "vnitroblock"),
}


GOOUT_CATEGORIES = {
    "concerts": "Koncert", "festivals": "Festival", "theatre": "Divadlo", "exhibitions": "Výstava",
    "movies": "Kino", "parties": "Party", "in_city": "Přednáška / akce ve městě", "for_children": "Pro děti",
    "sport": "Sport", "gastronomy": "Gastro",
}


def goout() -> list[Event]:
    out = []
    for venue_id, (venue, source) in GOOUT_VENUES.items():
        r = session.get("https://goout.net/services/entities/v1/schedules", timeout=30, params={
            "languages[]": "cs", "venueIds[]": venue_id, "limit": 100, "include": "events"})
        r.raise_for_status()
        d = r.json()
        events = {e["id"]: e for e in d.get("included", {}).get("events", [])}
        for sc in d["schedules"]:
            at = sc["attributes"]
            ev = events.get(sc["relationships"]["event"]["id"])
            if not ev or at.get("state") != "approved":
                continue
            loc = ev["locales"].get("cs") or next(iter(ev["locales"].values()))
            s, e = at["startAt"], at.get("endAt")
            multi = e and e[:10] != s[:10]
            out.append(Event(
                title=clean(loc["name"]),
                start=s[:16] if at.get("hasTime") and not multi else s[:10],
                end=(e[:10] if multi else e[:16] if at.get("hasTimeEnd") else None) if e else None,
                venue=venue,
                url=(sc.get("locales", {}).get("cs") or {}).get("siteUrl") or sc.get("url"),
                source=source,
                category=GOOUT_CATEGORIES.get(ev["attributes"].get("mainCategory"), ev["attributes"].get("mainCategory")),
                price=f"{at['pricing']} Kč" if at.get("pricing") else None,
            ))
        time.sleep(1)
    return out


ADAPTERS = [biooko, jatka78, nzm, dox, vystaviste, praha7,
            studiohrdinu, crossclub, trznice, forumkarlin,
            planetarium, ngprague, ntm, pragueeu, lafabrika, goout]


# ---------------------------------------------------------------- dedup + výstup

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z0-9]", "", s.encode("ascii", "ignore").decode())


def dedupe(events: list[Event]) -> list[Event]:
    seen, out = set(), []
    for e in events:
        key = (norm(e.title), e.start[:10], norm(e.venue)[:6])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def main() -> int:
    all_events: list[Event] = []
    for fn in ADAPTERS:
        try:
            evs = fn()
            print(f"{fn.__name__:12} {len(evs):4} akcí")
            all_events += evs
        except Exception as ex:  # jeden rozbitý zdroj nesmí shodit ostatní
            print(f"{fn.__name__:12} CHYBA: {ex}", file=sys.stderr)

    events = sorted(dedupe(all_events), key=lambda e: e.start)
    if not events:  # nepublikovat prázdný kalendář, když selže všechno
        print("Žádné akce – nic neukládám.", file=sys.stderr)
        return 1
    data = {"generated": datetime.now(PRAGUE).isoformat(timespec="minutes"),
            "events": [asdict(e) for e in events]}
    OUT_DIR.mkdir(exist_ok=True)
    js = json.dumps(data, ensure_ascii=False, indent=1)
    (OUT_DIR / "events.json").write_text(js, encoding="utf-8")
    # events.js, aby index.html šel otevřít i přímo ze souboru (file:// neumí fetch)
    (OUT_DIR / "events.js").write_text(f"window.EVENTS_DATA = {js};\n", encoding="utf-8")
    print(f"Celkem {len(events)} akcí -> {OUT_DIR / 'events.js'}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
