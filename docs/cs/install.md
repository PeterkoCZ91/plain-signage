# Instalace

[English](../en/install.md)

Zástupné hodnoty: `<host>` (veřejná adresa služby), `<ssh-host>` (alias z
`~/.ssh/config`), `<screen>` (název obrazovky, např. `lobby-1a2b3c4d5e6f`),
`<user>` a `<password>` (přihlášení do adminu). Skutečné hodnoty patří do
soukromého souboru mimo git.

## Docker Compose

Potřebuješ Docker s pluginem Compose.

```bash
git clone https://github.com/PeterkoCZ91/plain-signage signage && cd signage
cp .env.example .env            # pak uprav .env: nastav SIGNAGE_ADMIN_USER a SIGNAGE_ADMIN_PASS
docker compose up -d --build
curl -s http://localhost:8080/api/health
```

Otevři `http://localhost:8080/admin` (basic auth údaji z `.env`), založ
obrazovku tlačítkem **+ Nová obrazovka** a otevři `http://localhost:8080/tv/<screen>`.
`docker-compose.yml` předává kontejneru tyto proměnné z `.env`:
`SIGNAGE_ADMIN_USER` a `SIGNAGE_ADMIN_PASS` (obě povinné) a výchozí brand
`SIGNAGE_BRAND_NAME`, `_EMAIL`, `_PHONE`, `_CITY`, `_URL`, `_QR_EMAIL`,
`_PWA_TITLE` (výchozí jsou zástupné hodnoty). `SIGNAGE_PORT` (port na hostiteli,
výchozí 8080) používá jen Compose. Přes `env_file: .env` se do kontejneru dostane
i každá další proměnná z `.env`, takže `SIGNAGE_TG_TOKEN`, `SIGNAGE_PUBLIC_URL`,
`SIGNAGE_MAX_FILE_MB` a zbytek z [Konfigurace](#konfigurace) fungují bez úpravy
compose souboru; explicitní položky mají přednost před `env_file`. Pokud je port
8080 obsazený, nastav v `.env` `SIGNAGE_PORT=<port>` a ve všech adresách použij
ten port. Data jsou ve volume `signage-data` v `/data`. `docker compose down`
volume zachová; s `-v` ho smaže.

## Bez Dockeru

Požadavky:

- `python3`. CI a image používají 3.12; starší verze nejsou testované.
  Volitelně `segno` (QR kódy; bez něj QR endpointy obrazovky a párování vracejí
  503 a `/qr.png` vrátí vestavěný `default-qr.png`) a `ffmpeg` (náhledy, přebalení,
  převod; bez něj `/api/health` hlásí `"autoconvert": false`).
- Pro testy: `pytest` (`pip install pytest`) a Node (CI používá 20).
- Pro `backup.sh`, `restore.sh` a `test-backup-restore.sh`: `bash`, `flock`,
  `tar`, `df`.
- `PYTHONUTF8=1` zajistí, že Python čte hesla admina se znaky mimo ASCII správně
  bez ohledu na locale systému.

Všechno drž v jednom datovém adresáři (`tv-state.json` a `tv-media/`); tuto
strukturu očekávají `backup.sh` a `restore.sh`. Spouštěj z kořene repozitáře:

```bash
SIGNAGE_ADMIN_USER=<user> SIGNAGE_ADMIN_PASS=<password> \
SIGNAGE_MEDIA=./data/tv-media SIGNAGE_STATE=./data/tv-state.json \
PYTHONUTF8=1 python3 deploy/app/tvserver.py 8090
```

Port je volitelný první argument (výchozí 8090; jiný port zadej jako číslo).
Server poslouchá na všech rozhraních (změní to `SIGNAGE_BIND`) a úvodní hláška
vypíše adresy admina a TV s LAN adresou stroje. Bez obou proměnných admina se
server nespustí. Rychlé kontroly:

```bash
curl -s http://localhost:8090/api/health                                   # {"ok": true, ...}
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8090/admin       # 401
curl -s -o /dev/null -w '%{http_code}\n' -u '<user>:<password>' http://localhost:8090/admin   # 200
```

Záloha a obnova bez Dockeru (podrobnosti obnovy:
[troubleshooting.md](troubleshooting.md#obnova-ze-zálohy-dat)):

```bash
SRC=./data DEST=./backups ./deploy/backup.sh
```

Testy (úplný seznam kontrol včetně ruff, shellcheck, Java testů kiosku a E2E je
v README). Spouštěj všechny z kořene repozitáře, každý zvlášť. Subshell zabrání
tomu, aby `pytest` změnil tvůj adresář, a `node --test` spuštěný z `deploy/app`
nenajde žádné soubory (a ohlásí 0 testů); 0 testů není úspěch. E2E testy
potřebují `playwright` s Chromiem a bez něj se přeskočí; na zápisu `node --test`
s hvězdičkou záleží, varianta s adresářem na Node 22 selže:

```bash
(cd deploy/app && python3 -m pytest -q)
node --test deploy/app/tv/test/*.test.mjs
bash deploy/test-backup-restore.sh
```

Docker bez Compose:

```bash
docker build -t signage:latest deploy/app
mkdir -p ./signage-data
docker run -d --name signage --restart unless-stopped \
  -p 127.0.0.1:8090:8090 \
  -e SIGNAGE_ADMIN_USER=<user> -e SIGNAGE_ADMIN_PASS=<password> \
  -e PYTHONUTF8=1 \
  -v "$PWD/signage-data:/data" \
  --log-opt max-size=10m --log-opt max-file=3 \
  signage:latest
```

Image je `python:3.12-slim` se `segno` a `ffmpeg`. `Dockerfile` kopíruje moduly
serveru výslovně, nový modul se do něj musí doplnit. Kontejner běží jako root
(kvůli vlastnictví volume).

## Konfigurace

Vše je volitelné kromě přihlášení do adminu. Úplný seznam je v
[api.md](api.md#proměnné-prostředí); hlavní proměnné:

| Proměnná | Účel (výchozí) |
|---|---|
| `SIGNAGE_ADMIN_USER`, `SIGNAGE_ADMIN_PASS` | Přihlášení do adminu (povinné) |
| `SIGNAGE_MEDIA`, `SIGNAGE_STATE` | Adresář s médii, soubor stavu |
| `SIGNAGE_PUBLIC_URL` | Veřejná základní URL pro QR kódy; jinak z hlaviček požadavku |
| `SIGNAGE_BUILD` | Řetězec verze v health a playlistu (`dev`); přehrávače se při změně obnoví |
| `SIGNAGE_TRUST_PROXY` | `1` za reverzní proxy: IP klienta z `X-Forwarded-For` |
| `SIGNAGE_MAX_FILE_MB`, `SIGNAGE_MAX_ITEMS`, `SIGNAGE_MAX_TOTAL_MB` | Limity uploadu (100 / 50 / 2000) |
| `SIGNAGE_TG_TOKEN`, `SIGNAGE_TG_CHAT`, `SIGNAGE_ALERT_OFFLINE_MIN`, `SIGNAGE_ALERT_LANG` | Telegram alerty (vypnuto; 10 min; `en`, nebo `cs` pro češtinu) |
| `SIGNAGE_KIOSK_DIR` | Adresář s release kiosku (`kiosk/` vedle souboru stavu) |
| `SIGNAGE_BRAND_*` | Výchozí brand, u obrazovky přepsatelný v adminu |
| `SIGNAGE_BRAND_WORDMARK` (`LEAD\|REST`), `_TAGLINE_CS`, `_TAGLINE_EN`, `_EXTRA_QR_URL`, `_EXTRA_QR_LABEL_CS`, `_EXTRA_QR_LABEL_EN` | Logotyp a slogan klidové obrazovky, volitelná třetí QR zkratka |
| `SIGNAGE_BRAND_FILE` (`brand.env` vedle souboru stavu), `SIGNAGE_ASSETS_DIR` (`assets/` vedle souboru stavu) | Tytéž `SIGNAGE_BRAND_*` jako soubor `KEY=VALUE`; soubory v `assets/` (`default-qr.png`, ikony, `manifest.webmanifest`) nahradí neutrální vestavěné |

Vestavěný brand, ikony a QR obrázek jsou neutrální ukázkové hodnoty: nastav si vlastní.

## Produkční nasazení

`deploy/deploy.sh` nasazuje přes SSH na Docker hostitele za reverzní proxy, která
ukončuje TLS. Podrobnosti: `./deploy/deploy.sh --help` a `deploy/README.md`.

Hostitele je třeba jednou připravit:

1. Založ adresáře:

   ```bash
   ssh <ssh-host> 'mkdir -p /opt/signage/data /opt/signage/backups /opt/signage/releases'
   ```

2. Přidej SSH alias do lokálního `~/.ssh/config` a skript spouštěj s
   `SIGNAGE_SSH_ALIAS=<ssh-host>` (povinná). Další proměnné `deploy.sh`
   (vypíše je i `./deploy/deploy.sh --help`):

   | Proměnná | Význam (výchozí) |
   |---|---|
   | `SIGNAGE_REMOTE_ROOT` | Kořen na serveru (`/opt/signage`) |
   | `SIGNAGE_CONTAINER` | Název kontejneru (`signage`) |
   | `SIGNAGE_HEALTH_URL`, `SIGNAGE_AUTH_URL`, `SIGNAGE_PLAYLIST_URL` | Interní adresy kontrolované na hostiteli (`http://172.17.0.1:8090/...`; kontrola playlistu používá obrazovku `default`) |
   | `SIGNAGE_CANARY_PORT` | Port canary kontejneru (18090) |
   | `SIGNAGE_BUILD` | Verze buildu (časová značka-gitSHA[-dirty]) |
   | `SIGNAGE_KEEP_RELEASES` | Kolik posledních image a adresářů vydání nechat (5) |
   | `SIGNAGE_PIDS_LIMIT` | `--pids-limit` kontejneru (256) |
   | `SIGNAGE_CAP_DROP` | `1` přidá `--cap-drop ALL` (0; ověř na svém hostiteli) |
   | `SIGNAGE_REQUIRE_CLEAN` | `1` skončí chybou při neuloženém stavu `deploy/` (0) |
   | `SIGNAGE_SKIP_TESTS` | `1` = `--skip-tests` (0) |
   | `SIGNAGE_SMOKE`, `SIGNAGE_SMOKE_ROLLBACK` | `1` spustí po nasazení `smoke-test.sh` (potřebuje `BASE`, `APASS`); `0` způsobí, že selhání jen varuje a nevrací zpět (1) |
3. Kontejner `signage` jednou vytvoř ručně. Skript z něj přebírá prostředí
   (tajemství nikdy nejdou na příkazovou řádku) a když neexistuje, skončí.

   ```bash
   ssh <ssh-host> 'mkdir -p /opt/signage/app'
   scp -r deploy/app/. <ssh-host>:/opt/signage/app/
   ssh <ssh-host> 'cd /opt/signage/app && docker build -t signage:latest .'
   ssh <ssh-host> 'docker run -d --name signage --restart unless-stopped \
     -p 172.17.0.1:8090:8090 \
     -e SIGNAGE_ADMIN_USER=<user> -e SIGNAGE_ADMIN_PASS=<password> \
     -e PYTHONUTF8=1 -e SIGNAGE_TRUST_PROXY=1 \
     -v /opt/signage/data:/data \
     --log-opt max-size=10m --log-opt max-file=3 signage:latest'
   ```

   Port je vázaný na adresu Docker bridge, ne na veřejné rozhraní.
4. Před server dej reverzní proxy (nginx), která posílá `X-Forwarded-For`, a v
   kontejneru nastav `SIGNAGE_TRUST_PROXY=1`. Bez toho mají všichni klienti IP
   proxy a deset překlepů v hesle zamkne všechny; s ním a bez proxy si klient
   může IP podvrhnout.

   ```nginx
   server {
       listen 443 ssl;
       server_name <host>;
       # ssl_certificate / ssl_certificate_key
       client_max_body_size 110m;              # nad SIGNAGE_MAX_FILE_MB
       location / {
           proxy_pass http://172.17.0.1:8090;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $remote_addr;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

   Staré panely bez HTTPS potřebují prostý HTTP server na portu 80 se stejným
   `proxy_pass`. Nový `limit_req_zone` `nginx -s reload` nenačte; nginx restartuj.

Nasazení:

```bash
./deploy/deploy.sh --dry-run     # testy a kontrola konfigurace, nic nenahrává
./deploy/deploy.sh               # testy, balíček, canary, přepnutí, kontroly health/auth/playlist, při chybě rollback
./deploy/deploy.sh --skip-tests  # jen v nouzi
```

Skript spustí testy, sestaví image na hostiteli a nastartuje canary kontejner
(bez Telegram údajů, data jen pro čtení). Potom uloží `tv-state.json` do
`/opt/signage/backups/tv-state.json.pre-<release>`, prohodí kontejnery a
zkontroluje znovu; při jakékoli chybě provede rollback. Přehrávače převezmou
novou verzi do zhruba dvou minut. Volitelný smoke test živé služby:

```bash
BASE=https://<host> AUSER='<user>' APASS='<password>' ./deploy/smoke-test.sh
```

`BASE` a `APASS` předávej vždy (`AUSER` má výchozí hodnotu `admin`). Jedna kontrola očekává hlavičky HSTS,
`X-Frame-Options` a `X-Content-Type-Options`, které musí přidat proxy.

## První přihlášení a přehrávače

Otevři `https://<host>/admin`. Nová služba nemá žádnou obrazovku: klikni na
**+ Nová obrazovka** a zadej popisek (obrazovka dostane název typu `<slug>-<12 hex>`),
nebo použij API:

```bash
curl -s -u '<user>:<password>' -H 'Content-Type: application/json' \
  -d '{"label":"Lobby"}' https://<host>/api/screens
# {"ok": true, "device": "lobby-<12 hex>", "label": "Lobby"}
```

`<screen>` v adresách je název obrazovky z adminu. Název `default` funguje, ale jde snadno uhodnout a média si přečte každý, kdo zná
adresu obrazovky, proto ho v produkci nepoužívej. Změna hesla admina znamená
vytvořit kontejner znovu s novým `-e SIGNAGE_ADMIN_PASS=...`; běžné nasazení ho
pak zachová.

Na TV nebo v prohlížeči otevři `https://<host>/tv/<screen>`. Přehrávač drží
média v Cache Storage a po úplném stažení playlistu hraje dál i bez sítě; první
načtení bez internetu nefunguje.

- Heartbeat se posílá jen pro existující obrazovku, nejdřív ji tedy založ.
- Zvuk a režim celé obrazovky v běžném prohlížeči čekají na klik nebo stisk klávesy.
- Jazyk: `?lang=cs|en`, jinak jazyk prohlížeče.
- Rozvrhy položek se řídí hodinami zařízení: zkontroluj datum a časové pásmo na boxu nebo TV.
- Video musí být H.264 v MP4: `./deploy/convert-video.sh INPUT [OUTPUT]`.

## Zálohy

`deploy/backup.sh` archivuje `/opt/signage/data` do
`/opt/signage/backups/signage-data-<STAMP>.tgz` (zámek, kontrola volného místa,
ověření `tar -tzf`, drží posledních 14). Zálohy zůstávají na stejném stroji, dokud je
nezkopíruješ jinam. Obnova: [troubleshooting.md](troubleshooting.md#obnova-ze-zálohy-dat).

```bash
scp deploy/backup.sh <ssh-host>:/opt/signage/backup.sh
ssh <ssh-host> 'chmod +x /opt/signage/backup.sh &&
  (crontab -l 2>/dev/null | grep -v signage/backup.sh;
   echo "10 3 * * * /opt/signage/backup.sh >> /opt/signage/backup.log 2>&1") | crontab -'
ssh <ssh-host> /opt/signage/backup.sh        # spustit jednu hned
```

## Ověření

```bash
BASE=https://<host>
curl -s "$BASE/api/health"                                            # ok=true, state_ok=true
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/admin"                # 401
curl -s -o /dev/null -w '%{http_code}\n' -u '<user>:<password>' "$BASE/admin"   # 200
curl -s "$BASE/api/tv/<screen>/playlist"                              # obsahuje "items"
curl -s -o /dev/null "$BASE/api/tv/<screen>/playlist?heartbeat=1&ok=1&err=0"
curl -s -u '<user>:<password>' "$BASE/api/devices"                    # "online": true do 45 s
docker inspect signage --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -c '^SIGNAGE_TRUST_PROXY=1$'   # 1
```

Poslední řádek vypíše jen počet, žádná tajemství.
