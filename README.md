# Akce Praha 7

Scraper akcí z Prahy 7 a okolí + kalendář (měsíční / týdenní přehled).

```
pip install -r requirements.txt
python scrape.py          # stáhne akce -> web/events.js, web/events.json
start web\index.html      # otevře kalendář
```

## Zdroje

| Zdroj | Metoda |
|---|---|
| Bio Oko | JSON-LD `schema.org/Event` |
| Jatka78 | HTML (`.show__main`) |
| Národní zemědělské muzeum | HTML (`.articleCont`, jen „NZM Praha“) |
| DOX | HTML (`.entry-inner`) |
| Výstaviště (navystavisti.cz) | HTML (`.program-list-item`) |
| praha7.cz/kalendar | HTML (`.tab-content-item`) |
| Studio Hrdinů | HTML `li[data-date]` + schema.org mikrodata |
| Cross Club | HTML (`div.predel` + `div.article`), stránkování `?date=` |
| Holešovická tržnice | HTML (`a.event-card`) |
| Forum Karlín | HTML (`div.event`) |
| Planetárium Praha | JSON API `shop.planetum.cz/web/program/` |
| Národní galerie (Veletržní palác) | GraphQL `admin.www.ngprague.cz/graphql` |
| Národní technické muzeum | aktuality s datem v titulku |
| prague.eu – výstavy (celá Praha) | HTML (`div.tile-switching`) |
| La Fabrika | HTML (`.programRow`), datum a čas z URL |
| Štvanice, Vnitroblock | GoOut JSON API (`/services/entities/v1/schedules?venueIds[]=`) |

Nový zdroj = nová funkce vracející `list[Event]` + přidat ji do `ADAPTERS`
a barvu `--s-<jméno>` / název do `SOURCES` v `web/index.html`.

Další místa z GoOut: najdi ID místa v DevTools (`venueIds[]=`) a přidej ho do `GOOUT_VENUES`.
Chybí: Hobulet (PDF → LLM).

## Automatická aktualizace

GitHub Actions (`.github/workflows/update.yml`) spouští scraper denně ve 4:00 UTC,
po každém push do `main` a ručně přes Actions → Run workflow. Složka `web/` se nasadí
na GitHub Pages: https://brukmoon.github.io/akce-praha7/
