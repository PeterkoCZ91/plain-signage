# Architektura

[English](../en/architecture.md)

## Součásti

- Server, `deploy/app/tvserver.py`: `ThreadingHTTPServer`, žádný webový framework. Handler, soubor stavu, upload, párování, náhledy, telemetrie, odesílání alertů. Pomocné moduly (`errors.py`, `validators.py`, `brand.py`, `ratelimit.py`, `mediacheck.py`, `mediautil.py`, `statefmt.py`, `alerts.py`) obsahují čisté funkce; `multipart.py` streamuje upload na disk; `video_convert.py` a `fix_media.py` přebalují video přes ffmpeg. Jediné volitelné závislosti jsou `segno` (QR) a `ffmpeg`.
- Přehrávač, admin, párovací stránka a service worker jsou v `deploy/app/tv/`: `player.html`, `admin.html` (česky a anglicky), `connect.html`, `sw.js`.
- Kiosk (`kiosk-app/`) a Wi-Fi pomocník (`wifi-app/`): viz [kiosk.md](kiosk.md).
- Skripty nasazení v `deploy/`: `deploy.sh`, `backup.sh`, `restore.sh`, `smoke-test.sh`, `convert-video.sh`, `tools/publish-kiosk.sh`, `tools/soak.py`.
- Jazyky: admin a párovací stránka mají české a anglické slovníky (`?lang=`, pak `localStorage` `signageLang`, pak prohlížeč, výchozí čeština). Kiosk používá `res/values` (angličtina) a `res/values-cs`. Chyby serveru pocházejí z jedné tabulky (`ERRORS`) se stabilním `code`; bez požadavku na jazyk se vrací stručný anglický text.
- Testy a CI: testy serveru (`deploy/app/test_*.py`, jednotkové a HTTP integrační), testy klienta (`node --test`, 72 testů přehrávače, service workeru, i18n a rozvrhu v `node:vm`), E2E v Chromiu s Playwrightem (`deploy/app/e2e/`, součást `pytest`), testy kiosku v čisté Javě v `kiosk-app/test/` a cyklus záloha-obnova. CI (`.github/workflows/ci.yml`) pouští ruff, pytest, shellcheck, test zálohy a obnovy, `node --test`, E2E v Chromiu a smoke test přes `docker compose` (admin musí odpovědět 401). Příkazy: [install.md](install.md#bez-dockeru).

```
admin, chytrá TV, Android kiosk ──HTTPS──┐   reverzní proxy (nginx)
starý panel ─────────────────────HTTP────┤        │
                                          ▼
                          kontejner: tvserver.py (port 8090)
                          /admin  /tv/<screen>  /connect  /api/...
                          volume /data: média, tv-state.json, telemetrie, release kiosku
```

## Stav a obrazovky

Obrazovka je záznam pod `devices` v souboru stavu (`SIGNAGE_STATE`, JSON se
`schema_version`) s položkami `version`, `items` a `settings`. Položky mají `id`,
`file`, `type`, `seconds`, `enabled`, `from`/`until`, volitelný `schedule` a
`sound`; nastavení obsahuje přechod, režim vyplnění, volby QR, `brand` a `label`.
Název obrazovky je součástí URL a odpovídá `^[A-Za-z0-9_-]{1,48}$`. Nové
obrazovky dostanou `<slug>-<12 hex>`, náhodná část má 48 bitů z `secrets`.
Každá změna zvýší `version`, který přehrávač pozná v playlistu.

Stav se zapisuje atomicky (dočasný soubor, `fsync`, `os.replace`) pod zámkem.
Poškozený soubor se zkopíruje stranou a zápisy se zablokují, takže `/api/health`
vrací `ok: false`. Jen v paměti jsou přítomnost, rezervace uploadů, cache QR,
rate limitery, idempotentní odpovědi a zdraví boxu. Rozvrhy položek vyhodnocuje
přehrávač podle hodin zařízení, ne server ([api.md](api.md#rozvrh-položky)).

## Tok dat, párování a monitoring

- Upload: multipart, kontrola typu a velikosti, rezervace kvóty, streamování do `.signage-upload-*`, volitelné přebalení ffmpegem, přejmenování na `<16 hex>_<name>`, uložení.
- Přehrávač čte playlist každých 15 s s `?heartbeat=1`; nový `SIGNAGE_BUILD` ho přiměje se obnovit při přechodu mezi položkami a starší build se nikdy nepovažuje za novější.
- Párování: TV otevře `/connect` a dostane šestiznakový kód platný deset minut; QR kód vede do adminu s předvyplněným kódem. Jakmile ho admin převezme, TV při dalším pollu přejde na `/tv/<screen>` a adresu si zapamatuje.
- Monitoring: heartbeat posílá jen přehrávač, otevřený admin se nepočítá. Obrazovka je online, když se ozvala za posledních 45 s. Telemetrie jde do `SIGNAGE_TELEMETRY` nejvýše jednou za minutu (drží se 14 dní, strop 8 MiB); admin ukazuje posledních 24 hodin po hodinových blocích. Dokazuje to, že přehrávač běží, ne stav panelu. Smyčka alertů (60 s) posílá Telegram zprávy o offline, prázdných obrazovkách a nízké RAM.
- Offline: service worker cachuje shell, celé soubory médií (s podporou `Range`) a poslední úplný playlist a při výpadku sítě hraje z nich. První načtení a nedokončené stahování internet potřebují. Workbox je přibalený, nic se nenačítá z CDN.
- Aktualizace kiosku a zdraví boxu jedou na jednom pollu, `GET /api/kiosk/release`: [api.md](api.md#aktualizace-kiosku).

## Bezpečnostní model

- Veřejné záměrně: přehrávač, playlist, `/media/`, `/api/health`, párování, `/qr.png`, poll a APK kiosku. Adresa obrazovky je prakticky heslo k jejímu obsahu, proto neuhodnutelné názvy a náhodné prefixy uploadů.
- Basic auth (porovnání v konstantním čase, 401 zavře spojení) pro admin a zapisovací API.
- Rate limity na IP s `Retry-After`; `SIGNAGE_TRUST_PROXY=1` nastavuj jen za proxy.
- Limity: JSON tělo 64 KiB, textové pole formuláře 1 MB, stropy velikosti uploadu a celkového úložiště (413, 507), nejvýše 3 souběžné uploady (503), timeout socketu. `brand` a `label` nepřijmou `<` ani `>`; `media_path` nikdy nesleduje symlink ven ze `SIGNAGE_MEDIA`.
- `admin.html` nese Content-Security-Policy jako tag `<meta>` (`default-src 'self'`; inline skripty a styly jsou povolené, `eval` ne). Není to HTTP hlavička a netýká se přehrávače ani `/connect`. Stránky adminu navíc posílají `X-Robots-Tag: noindex`.
- Kiosk: nešifrovaný provoz a zálohy vypnuté, navigace omezená na nastavený https host.
- Kontejner běží s `no-new-privileges`, limitem procesů a rotací logů.
- Bez nezávislého bezpečnostního posudku.

## Známá omezení

- Jedno heslo admina, žádné role, žádné oddělení klientů; jediné oddělení obrazovek je jejich název.
- Kiosk nemá PIN: dvojí stisk BACK ho opustí a na 10 minut pozastaví watchdog (záměrně, pro techniky). Na Androidu 10 watchdog potřebuje jednorázově přes ADB udělené `SYSTEM_ALERT_WINDOW`. Kiosk má `targetSdk` 29.
- Kontejner běží jako root; zálohy zůstávají na stejném stroji, dokud je nezkopíruješ.
- Neautomatizované: skutečné TV a panely, kiosk na skutečném boxu (autostart po výpadku proudu), nasazení (kontrola ručně a smoke testem). Java testy kiosku se v CI nespouštějí. Zátěž se měří ručně přes `deploy/tools/soak.py`.
- Výchozí jazyk je angličtina: chyby API bez požadavku na jazyk, přehrávač na TV a Telegram alerty jsou anglicky; česky je lze získat přes `?lang=cs` / `Accept-Language` (chyby API), jazyk přehrávače a `SIGNAGE_ALERT_LANG=cs` (alerty). Alerty mají textové předpony (`[CRITICAL]`, `[WARNING]`, `[OK]`), ne emoji.
