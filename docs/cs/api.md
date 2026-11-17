# HTTP API

[English](../en/api.md)

Zdroj pravdy: `deploy/app/tvserver.py` (routy), `errors.py`, `validators.py`,
`brand.py`, `ratelimit.py` a test `test_api_matrix.py`. Zástupné hodnoty:
`<host>`, `<user>:<password>` (přihlášení do adminu), `<screen>` (název
obrazovky). Server implementuje jen GET, HEAD a POST; PUT, DELETE, PATCH a
OPTIONS vrací `501`.

## Autentizace

- Chráněné cesty používají HTTP Basic s `SIGNAGE_ADMIN_USER` a `SIGNAGE_ADMIN_PASS`.
- Chráněné podle prefixu: `/admin`, `/api/upload`, `/api/delete`, `/api/order`,
  `/api/item`, `/api/devices`, `/api/settings`, `/api/pair/claim`, `/api/screens`,
  `/api/thumb/` a přesně `/`.
- Veřejné: `/tv`, `/tv/<screen>`, `/connect`, `/api/health`,
  `/api/tv/<screen>/playlist`, `/media/<file>`, `/assets/<file>`, `/sw.js`,
  `/robots.txt`, `/qr.png`, `/pair-qr.png`, `POST /api/pair/request`,
  `GET /api/pair/status`, `GET /api/kiosk/release`, `/kiosk/signage-kiosk.apk`.
  Veřejný přehrávač je bezpečný jen díky náhodným názvům obrazovek (`<slug>-<12 hex>`).
- Špatné nebo chybějící údaje: `401` s `WWW-Authenticate: Basic realm="Signage admin"`,
  kód `unauthorized`, spojení se zavře. Příliš mnoho selhání z jedné IP: `429`
  `too_many_attempts`; počítají se jen požadavky s hlavičkou `Authorization`.

## Chyby

JSON chyby (mimo upload) vypadají takto: `{"error": "invalid device", "code": "invalid_device"}`.

- `code` je stabilní strojově čitelný kód (klíč `ERRORS` v `errors.py`); rozhoduj podle něj.
- `error` je text pro člověka. Bez požadavku na jazyk je to stručný technický anglický
  text (jen ASCII). `?lang=cs|en` nebo `Accept-Language` dá text pro koncové
  uživatele česky nebo anglicky; `?lang=` má přednost.
  Odpovědi nesou `Content-Language`. Uživatelská data se nepřekládají.
- Chyby bez JSON těla (neznámá GET cesta, chybějící médium, errors QR) jsou prostý text.

| Kód | HTTP | Kdy |
|---|---|---|
| `unauthorized`, `too_many_attempts` | 401, 429 | chybějící nebo špatné údaje, příliš mnoho neúspěšných přihlášení |
| `too_many_requests` | 429 | limit `POST /api/pair/request` nebo `GET /api/kiosk/release` |
| `pair_capacity_full` | 429 | existuje 200 čekajících párovacích kódů |
| `invalid_json`, `body_not_object`, `invalid_content_length` | 400 | vadné tělo požadavku |
| `request_too_large` | 413 | JSON tělo přes 64 KiB |
| `unknown_endpoint` | 404 | neznámá POST cesta |
| `invalid_device` | 400 / 404 | neplatný název obrazovky (404 u playlistu) |
| `preview_unavailable` | 404 | náhled neexistuje nebo ho nelze vytvořit |
| `invalid_install_id`, `invalid_pair_input` | 400 | `client` neodpovídá `[A-Za-z0-9_-]{16,80}`; kód není `[A-Z2-9]{6}` nebo špatný název |
| `pair_code_expired`, `pair_code_used` | 404, 409 | kód neznámý nebo vypršel, už použitý |
| `state_save_failed` | 500 / 507 | uložení stavu selhalo (507 = plný disk) |
| `no_kiosk_release`, `kiosk_up_to_date` | 404 nebo 409, 409 | žádný platný release (404 u pollu, 409 u `kiosk-update`); obrazovka už má tento `versionCode` nebo vyšší |
| `hours_not_integer`, `hours_out_of_range` | 400 | `hours` musí být 1 až 168 |
| `order_not_list`, `file_not_string`, `seconds_invalid`, `schedule_invalid` | 400 | vstup položky a pořadí (`seconds` do 86400) |
| `label_not_text`, `label_too_long`, `label_invalid_chars` | 400 | popisek: nejvýše 60 znaků, bez `<` `>` a řídicích znaků |
| `brand_*` | 400 | `brand_not_object`, `brand_field_not_text`, `_too_long`, `_bad_chars`, `_not_email`, `brand_url_invalid` |
| `too_many_uploads`, `invalid_idempotency_key`, `upload_in_progress`, `server_busy` | 429, 400, 409, 503 | viz Upload |
| `batch_too_large`, `file_too_large`, `upload_corrupt`, `file_save_failed` | 413, 413, 400, 507 | viz Upload |
| `unsupported_type`, `heic_unsupported`, `video_format_unsupported`, `item_limit`, `empty_file`, `media_invalid`, `storage_quota`, `invalid_file_path`, `media_*` | v seznamu chyb uploadu | `media_*` (`media_codec`, `media_quicktime`, `media_image_huge`, ...) upřesňují `media_invalid` |
| `warn_*`, `fix_repacked*` | `warnings` / `fixes` | nejsou errors: texty varování a automatických úprav |

Úplný seznam s texty je `ERRORS` v `deploy/app/errors.py`.

## Endpointy

Chráněné endpointy chtějí basic auth, např. `curl -u <user>:<password> https://<host>/...`.
`device` ve všech JSON endpointech musí odpovídat `[A-Za-z0-9_-]{1,48}`.

### Veřejné

| Metoda a cesta | Popis |
|---|---|
| `GET /tv`, `/tv/<screen>` | HTML přehrávač (bez názvu = `default`), `no-store` |
| `GET /connect` | HTML stránka párování |
| `GET /api/tv/<screen>/playlist` | `{version, device, items[], build, settings}`. `heartbeat=1` zapíše heartbeat; dále `w`, `h`, `why`, `h264`, `quota`, `kiosk` a další diagnostika. Neplatný název: 404 `invalid_device`; správně utvořený, ale neznámý název dostane `version` 0 a žádné položky (nic se nevytvoří, heartbeat se nezapíše) |
| `GET /api/health` | `{ok, state_ok, state_error, devices, items, storage_mb, version, blank_screens, alerts_enabled, autoconvert, max_file_mb, max_items, max_total_mb}` |
| `GET /media/<file>` | médium s podporou `Range` (206/416), `Cache-Control: public, max-age=3600` |
| `GET /assets/<file>`, `/sw.js`, `/robots.txt` | statické soubory, service worker, `Disallow: /` |
| `GET /qr.png` | QR nastavení obrazovky; `device`, `slot` (`1` = web/IG, `2` = e-mail) |
| `GET /pair-qr.png` | QR na `/admin?pair=<code>`; `code` |
| `POST /api/pair/request` | TV pošle `{"client": "<install id>"}`, dostane `{code, expires_in}` (6 znaků `A-Z2-9`, 600 s; stejný `client` dostane stejný kód) |
| `GET /api/pair/status` | `client`; `{"status":"pending","expires_in":N}`, `{"status":"paired","device":"<screen>"}` (kód se spotřebuje) nebo 404 `{"status":"missing"}` |
| `GET /api/kiosk/release` | poll kiosku jednou za minutu (viz Aktualizace kiosku). `device`, `have` (nainstalovaný `versionCode`), `state` (`idle`, `downloading`, `waiting`, `installed`, `declined`, `failed`, `no_permission`), zdraví boxu; neplatné hodnoty se ignorují, nikdy 400. Odpověď `{versionCode, versionName, sha256, size, url, requested}`, `no-store` |
| `GET /kiosk/signage-kiosk.apk` | aktuální APK, podporuje `Range`; bez platného release 404 |

### Chráněné

| Metoda a cesta | Popis |
|---|---|
| `GET /`, `/admin` | HTML admin |
| `GET /api/devices` | `{"devices": {<screen>: {version, count, label, size_mb, online, last_seen, agent, screen, render_err, render_why, h264, storage_mb, kiosk, cached_items, snapshots, stalls, blank, kiosk_code, kiosk_state, kiosk_update, kiosk_*}}, "kiosk_release": {versionCode, versionName} nebo null}`; `online` = heartbeat do 45 s; `kiosk_update` = požadovaný `versionCode` nebo null |
| `GET /api/devices/<screen>/availability` | dostupnost z telemetrie; `hours` 1 až 168 (výchozí 24) |
| `GET /api/thumb/<file>` | JPEG náhled vytvářený líně přes ffmpeg a cachovaný; 404 `preview_unavailable` |
| `GET /api/screens/<screen>/qr.png` | QR s adresou přehrávače obrazovky |
| `POST /api/screens` | `{"label": "..."}` (volitelné); vytvoří obrazovku s náhodným názvem, odpověď `{ok, device, label}` |
| `POST /api/screens/<screen>/kiosk-update` | `{}` vyžádá aktualizaci kiosku (uloží aktuální `versionCode`), `{"cancel": true}` ji zruší; odpověď `{"ok": true, "requested": <versionCode> nebo null}` |
| `POST /api/pair/claim` | `code` a `device` (existující nebo nový), nebo bez `device` nová obrazovka s volitelným `label`; odpověď `{ok, device}` |
| `POST /api/upload?device=<screen>` | multipart upload, viz Upload |
| `POST /api/order` | `device`, `order` (seznam názvů souborů; nevyjmenované položky zůstanou na konci) |
| `POST /api/delete` | `device`, `file`; soubor z disku zmizí, jen když ho nepoužívá jiná obrazovka |
| `POST /api/item` | `device`, `file`, volitelně `seconds`, `sound`, `enabled`, `from`, `until`, `schedule` |
| `POST /api/settings` | `device`, volitelně `qrCorner`, `qr1On`, `qr2On`, `qrUrl`, `qrUrl2`, `brand`, `label`, `transition`, `fit`; odpověď `{ok, settings}` |

Poznámky:

- `/api/item`: `seconds` pod 2 se zvedne na 2, nad 86400 nebo ne-číslo vrátí `400` `seconds_invalid`; `sound` a `enabled` se převedou
  na boolean; `from` a `until` jsou data oříznutá na 10 znaků. Neznámý `file`
  stále vrátí `{ok: true}` a nic nezmění.
- `/api/settings`: `qrUrl` a `qrUrl2` se ořezávají na 500 znaků; `transition` je
  `cut`, jinak `fade`; `fit` je `landscape` (výchozí), `portrait`, `portrait270` nebo
  `fill`; prázdný `label` popisek smaže.
- `/api/devices`: `cached_items` je `<staženo>/<celkem>`, počet položek playlistu (ne megabajtů), které má přehrávač v cache; hlásí ho přehrávač, prázdné když není známo. Dříve se pole jmenovalo `mem_mb`.
- Každá změna dat zvýší `version` obrazovky, podle čehož ji přehrávač pozná.

## Upload

`POST /api/upload?device=<screen>`, `multipart/form-data`. Každá část s názvem
souboru je jedno médium. `device` vždy předávej: bez něj upload míří na obrazovku `default` a vytvoří ji, pokud neexistuje, což málokdy chceš (název jde uhodnout).

```bash
curl -u <user>:<password> -H "Idempotency-Key: 9f1c2a-photo-001" \
  -F "file=@photo.jpg" -F "file=@clip.mp4" \
  "https://<host>/api/upload?device=<screen>"
```

- Přípony: `.jpg .jpeg .png .webp .gif` (obrázek, výchozích 10 s) a
  `.mp4 .m4v .webm .mov` (video, výchozích 30 s, bez zvuku). `.mov` s H.264 se
  bezeztrátově přebalí do MP4; HEIC se odmítne (`heic_unsupported`). Obsah se
  ověřuje (`media_invalid`); video nad bezpečné rozlišení jen varuje.
- Nová položka je `enabled: true`, `sound: false`, bez rozvrhu.
- Limity: `SIGNAGE_MAX_FILE_MB` (100), `SIGNAGE_MAX_ITEMS` na obrazovku (50),
  `SIGNAGE_MAX_TOTAL_MB` (2000); délka požadavku se předem porovná s
  `SIGNAGE_MAX_TOTAL_MB` (`batch_too_large`, 413).
- Nejvýše 3 uploady naráz; další dostanou `503` `server_busy` s `Retry-After: 5`.
  Rate limit na IP: `429` `too_many_uploads`.
- `Idempotency-Key` (volitelný, `[A-Za-z0-9_.:-]{1,128}`, platí v rámci
  obrazovky): úspěšná odpověď se drží `SIGNAGE_IDEM_TTL` s (600) a opakování se
  stejným klíčem ji vrátí s `Idempotent-Replay: true` bez dalšího ukládání.
  Dokud běží: `409` `upload_in_progress`. Neúspěšné pokusy se neukládají.
- Úspěch: `{"ok": true, "saved": ["photo.jpg"], "errors": [], "warnings": [], "fixes": []}`. Položky jsou `<file>: <text>` v jazyce z `?lang=`.
- S chybami: `ok: false`, `errors`, `error_codes` (paralelní kódy) a `code`
  (první). Dávka se zpracovává soubor po souboru, takže HTTP 200 může nést
  `saved` i `errors`. Když se kvůli zátěži nebo místu nic neuložilo: `503` nebo `507`.
- Chyby celého požadavku mají stejný tvar: `400` `upload_corrupt`, `413`
  `file_too_large` / `batch_too_large`, `507` `file_save_failed`, `429`, `409`,
  `503`. U předčasných odpovědí server přečte omezenou část těla, než spojení
  zavře, aby klient viděl odpověď, ne reset.
- Neplatné `?device=` dá `400` `invalid_device` a nic nevytvoří.

## Rozvrh položky

`schedule` v `POST /api/item` omezuje, kdy se položka přehrává, a v playlistu se
vrací jako `items[].schedule`.

```json
{"device": "<screen>", "file": "photo.jpg",
 "schedule": {"days": [0, 1, 2, 3, 4], "from": "08:00", "to": "16:00"}}
```

- `days`: unikátní celá čísla 0 (pondělí) až 6 (neděle).
- `from`, `to`: `HH:MM` (24 h), jen společně a různé. `from` > `to` přechází
  přes půlnoc a patří ke dni, ve kterém začíná; `to` je výlučné.
- Jiné klíče jsou chyba (`schedule_invalid`, 400).
- `null`, `{}` a `{"days": []}` bez časů rozvrh smažou; chybějící pole ho nechá beze změny.
- Vyhodnocuje ho přehrávač podle hodin zařízení, ne server.

## Brand

`brand` v `POST /api/settings` je objekt s klíči `name` (max 60), `email` (120),
`phone` (40), `city` (80), `url` (500), `qrEmail` (120), `pwaTitle` (30);
neznámé klíče se ignorují.

- Hodnoty jsou text bez `<`, `>` a řídicích znaků. Prázdný řetězec vrátí pole k
  výchozí hodnotě; `"brand": null` smaže celý brand. Nové hodnoty se slučují s
  uloženými.
- `email`, `qrEmail`: e-mail, volitelně s `mailto:`. `url`: `http(s)://`,
  `mailto:` nebo holý e-mail.
- Pořadí priorit: brand obrazovky, pak `SIGNAGE_BRAND_*`, pak vestavěné výchozí
  hodnoty. Výsledný brand a QR karty se vrací v `settings` playlistu (`brand`,
  `qrCards`, `qrCorner`, `qr1On`, `qr2On`, `qrUrl`, `qrUrl2`, `transition`, `fit`).

## Aktualizace kiosku

Release leží v `SIGNAGE_KIOSK_DIR` (výchozí `kiosk/` vedle souboru stavu,
`/data/kiosk` v kontejneru): `signage-kiosk.apk` a `kiosk.json`
= `{"versionCode": int > 0, "versionName": "1-32 znaků", "sha256": "<64 malých hex>", "size": int}`.
Release je platný, jen když JSON projde validací a `size` odpovídá APK; jinak se
server chová, jako by žádný nebyl. Publikuje se přes `deploy/tools/publish-kiosk.sh`.

Admin pošle `POST /api/screens/<screen>/kiosk-update`, což uloží
`kiosk_update = versionCode`. Při dalším pollu box dostane `requested: true`,
stáhne APK, ověří `sha256` a instalaci nechá potvrdit na TV. Požadavek se smaže,
když box ohlásí `have >= requested`, nebo `state` `declined`, `failed` či
`no_permission` (tyto tři se počítají až od druhého pollu po požadavku). Poll pro neznámou
obrazovku nic nezapíše.

### Zdraví boxu

Kiosk přidává tyto parametry do téhož pollu (starší klient je může vynechat). Server je ověří,
neplatné nebo chybějící bere jako neznámé (`null`), drží je jen v paměti a nikdy
neodpoví 400. SSID se nikdy neposílá.

| Dotaz | Význam | Platné hodnoty |
|---|---|---|
| `ram`, `ram_total` | volná a celková RAM v MB | celé číslo 0-65536 |
| `ram_low` | systém hlásí nedostatek paměti | `1` / `0` |
| `up` | uptime boxu v s | celé číslo 0-10^9 |
| `rssi` | síla Wi-Fi v dBm; jen při aktivní Wi-Fi | celé číslo -127-0 |
| `net` | aktivní síť | `wifi`, `eth`, `other` |
| `crash` | pády rendereru WebView od startu procesu | celé číslo 0-10^6 |

Poll s alespoň jednou platnou hodnotou je úplný snímek (chybějící se stane
`null`); poll bez nich (starší kiosk) snímek nemění. `GET /api/devices` ukazuje
`kiosk_ram`, `kiosk_ram_low`, `kiosk_ram_total`, `kiosk_up`, `kiosk_rssi`,
`kiosk_net`, `kiosk_crash`, `kiosk_seen` (epoch posledního hlášení),
`kiosk_boots` (restarty poznané z poklesu `up`, jen v paměti) a
`kiosk_last_boot` (epoch zapnutí).

Telegram alert: 3 po sobě jdoucí polly (`RAM_ALERT_POLLS`) s `ram` pod 80 MB
(`RAM_ALERT_MB`) vyvolají „trvale nízká volná RAM“, zruší se od 120 MB
(`RAM_OK_MB`). Jen pro online obrazovku.

## Rate limity

Klouzavé okno na IP klienta; při překročení `429` s `Retry-After`,
`Connection: close` a JSON `code`. Paměť omezuje `SIGNAGE_RL_MAX_KEYS`.

| Co | Proměnné (limit / okno v s) | Výchozí | Kód |
|---|---|---|---|
| neúspěšná přihlášení | `SIGNAGE_RL_LOGIN_MAX` / `_WINDOW` | 10 / 300 | `too_many_attempts` |
| `POST /api/pair/request` | `SIGNAGE_RL_PAIR_MAX` / `_WINDOW` | 30 / 300 | `too_many_requests` |
| `POST /api/upload` | `SIGNAGE_RL_UPLOAD_MAX` / `_WINDOW` | 30 / 60 | `too_many_uploads` |
| `GET /api/kiosk/release` | `SIGNAGE_RL_KIOSK_MAX` / `_WINDOW` | 120 / 60 | `too_many_requests` |

Za reverzní proxy nastav `SIGNAGE_TRUST_PROXY=1`: IP se pak bere z posledního
záznamu `X-Forwarded-For`. Bez proxy ji nenastavuj, jinak si hlavičku může
podvrhnout kdokoli.

## Proměnné prostředí

| Proměnná | Výchozí | Význam |
|---|---|---|
| `SIGNAGE_ADMIN_USER`, `SIGNAGE_ADMIN_PASS` | povinné | basic auth; bez nich server skončí s kódem 2 |
| `SIGNAGE_BUILD` | `dev` | verze v `/api/health` a playlistu (`build`) |
| `SIGNAGE_MEDIA`, `SIGNAGE_STATE` | `tv-media`, `tv-state.json` vedle aplikace | adresář s médii, soubor stavu |
| `SIGNAGE_STATE_BACKUP_RETENTION` | 5 | počet uchovaných kopií poškozeného souboru stavu |
| `SIGNAGE_PUBLIC_URL` | z `Host` / `X-Forwarded-Proto` | veřejná základní URL pro QR kódy |
| `SIGNAGE_CORS_ORIGIN` | prázdné (vypnuto) | jediný povolený webový origin |
| `SIGNAGE_MAX_FILE_MB`, `SIGNAGE_MAX_ITEMS`, `SIGNAGE_MAX_TOTAL_MB` | 100, 50, 2000 | limity uploadu |
| `SIGNAGE_TRUST_PROXY` | vypnuto | `1` = IP z `X-Forwarded-For` |
| `SIGNAGE_BIND` | `0.0.0.0` | adresa, na které server poslouchá |
| `SIGNAGE_RL_LOGIN_*`, `_PAIR_*`, `_UPLOAD_*`, `_KIOSK_*` | viz Rate limity | limity |
| `SIGNAGE_RL_MAX_KEYS` | 4096 | strop sledovaných IP |
| `SIGNAGE_IDEM_TTL`, `SIGNAGE_IDEM_MAX` | 600, 256 | doba (s) a počet uložených idempotentních odpovědí |
| `SIGNAGE_STALE_TMP_SECONDS` | 3600 | stáří osiřelých dočasných souborů mazaných při startu |
| `SIGNAGE_KIOSK_DIR` | `kiosk/` vedle stavu | adresář s release kiosku |
| `SIGNAGE_TG_TOKEN`, `SIGNAGE_TG_CHAT` | prázdné | Telegram alerty, zapnuté jen když jsou nastavené obě |
| `SIGNAGE_ALERT_OFFLINE_MIN` | 10 | minut offline před alertem |
| `SIGNAGE_TELEMETRY`, `_RETENTION_DAYS`, `_MAX_MB` | `telemetry.jsonl` vedle stavu, 14, 8 | soubor telemetrie, uchování, strop velikosti |
| `SIGNAGE_BRAND_*`, `SIGNAGE_BRAND_FILE`, `SIGNAGE_ASSETS_DIR` | neutrální ukázkové hodnoty | výchozí brand, viz [install.md](install.md#konfigurace) |

Neplatná nebo nekladná číselná hodnota se tiše nahradí výchozí. Port je první
argument příkazové řádky (výchozí 8090).
