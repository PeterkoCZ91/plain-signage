# Řešení potíží a obnova

[English](../en/troubleshooting.md)

Zástupné hodnoty: `<host>`, `<ssh-host>`, `<box-ip>`, `<screen>`, `<user>`/`<password>`.
Cesty na hostiteli: data `/opt/signage/data`, zálohy `/opt/signage/backups`,
kontejner `signage`.

Začni tímto:

```bash
curl -s https://<host>/api/health                      # ok, state_ok, blank_screens, alerts_enabled, autoconvert
curl -s https://<host>/api/tv/<screen>/playlist        # má "items"?
curl -s -u '<user>:<password>' https://<host>/api/devices   # online, blank, render_err, h264, kiosk
ssh <ssh-host> 'docker logs signage 2>&1 | tail -50'
```

## Port 8080 je obsazený

`docker compose up` skončí chybou „port is already allocated“ nebo „address
already in use“. Nastav v `.env` `SIGNAGE_PORT=<volný port>`, spusť znovu
`docker compose up -d` a ve všech adresách použij ten port
(`http://localhost:<port>/admin`).

## Obrazovka je offline

- Vypadlo napájení, síť nebo box. Online znamená, že se obrazovka ozvala za posledních 45 s; heartbeat posílá jen přehrávač.
- U boxu zkus `adb connect <box-ip>:5555` nebo diagnostiku (MENU/INFO na ovladači).
- Obrazovka v adminu neexistuje, nebo je v URL překlep. Heartbeat pro neznámý název se ignoruje (200, nic se nevytvoří).
- Hledej `<screen>` v `/api/devices`; založ ji přes **+ Nová obrazovka**.
- Kiosk byl sestavený pro jinou adresu; přijme jen `https://` a přesně host z `KIOSK_BASE_URL`.
- Zkontroluj „last error“ v MENU/INFO nebo `adb logcat` filtrovaný na `SignageKiosk` a sestav ho znovu ([kiosk.md](kiosk.md#build)).
- Server nebo proxy neběží: `curl` na health URL, `ssh <ssh-host> 'docker ps -a'`, `docker logs signage`.
- Při výpadku serveru přehrávače hrají z offline cache, takže „offline“ neznamená černou obrazovku.
- První načtení bez internetu nefunguje.

## Černá nebo zamrzlá obrazovka

- Prázdný playlist je normální stav (heartbeat hlásí `ok=1`). Zkontroluj `items`, nahraj obsah, zkontroluj rozvrhy položek (hodiny zařízení, datum a časové pásmo).
- Přehrávač nic nevykresluje (chyba obrázku nebo videa, prohlížeč bez H.264). Zkontroluj `blank_screens` v `/api/health` a `blank`, `screen`, `render_err`, `render_why`, `h264` v `/api/devices`. Telegram pošle zprávu `[WARNING]` po více než 2 min bez vykreslení. Chybějící `h264` znamená neznámé, ne „neumí“.
- Video se zasekává: použij menší rozlišení nebo H.264 MP4 přes `./deploy/convert-video.sh INPUT [OUTPUT]`.
- Kiosk něco překrývá: watchdog ho vrátí po 20 s, ale jen se `SYSTEM_ALERT_WINDOW` (viz sekce o kiosku níže).
- Jednorázové obnovení kiosku: `adb shell am start -n app.plainsignage.kiosk/.MainActivity`.

## Video se nepřehrává

- H.265 a AV1 se odmítnou s `media_invalid`, stejně jako obrázky nad 100 Mpx nebo 20 000 px a obsah, který neodpovídá příponě. Převeď na H.264 MP4 přes `./deploy/convert-video.sh INPUT [OUTPUT]` (nenulový návratový kód, když výsledek neprojde vlastními kontrolami) nebo `ffmpeg -i in -c:v libx264 -profile:v baseline -c:a aac -movflags +faststart out.mp4`.
- `.heic`, `.mkv`, `.avi`, `.mts` a podobné dají `heic_unsupported` nebo `video_format_unsupported`: fotky exportuj jako JPG, video jako MP4 (H.264).
- Vadný kontejner (značka QuickTime, `moov` za daty, typické pro `.mov` z telefonu) se opraví přebalením (`-c copy`, přípona se změní na `.mp4`, v odpovědi `fixes`), jen když je k dispozici ffmpeg (`"autoconvert": true` v `/api/health`). Pro soubory, které už na disku jsou:

  ```bash
  ssh <ssh-host> docker exec signage python3 fix_media.py --dry-run
  ssh <ssh-host> docker exec signage python3 fix_media.py
  ```

  Přebaluje na místě (atomické přepsání, kontroluje, že video stopa zůstane bit po bitu stejná, nikdy nepřejmenuje) a bez ffmpeg nedělá nic.
- Video nad 5 MB bez faststart, nebo rozlišení, které obrazovka nezvládne, jen varuje (`warnings`); soubor se uložil.
- Velmi staré panely nemají service worker a potřebují H.264 MP4.

## Upload selhává

Odpověď je JSON s `code` a lokalizovanou zprávou. Podívej se do `docker logs signage`.

| HTTP | `code` | Příčina a oprava |
|---|---|---|
| 503 + `Retry-After: 5` | `server_busy` | Tři uploady naráz, nebo běží dva převody. Počkej a zkus znovu. |
| 507 | `file_save_failed` | Plný disk. Uvolni místo (viz Plný disk) a zkus znovu. Zbylé soubory `.signage-upload-*` starší než `SIGNAGE_STALE_TMP_SECONDS` se při startu smažou. |
| 429 + `Retry-After` | `too_many_uploads` | 30 za 60 s na IP. Počkej; když 429 dostanou všichni, viz Špatné heslo a rate limity. |
| 413 | `file_too_large` | Nad `SIGNAGE_MAX_FILE_MB`. Za nginx musí být `client_max_body_size` vyšší, jinak 413 vrátí nejdřív nginx. |
| 413 | `batch_too_large` | Nad `SIGNAGE_MAX_TOTAL_MB`. Nahrávej po částech. |
| 200, `ok: false` | `storage_quota`, `item_limit` | Celkové úložiště nebo 50 položek na obrazovku. Smaž položky v adminu; limity jsou v `/api/health`. |
| 400 | `upload_corrupt` | Poškozený nebo přerušený upload, nebo text formuláře přes 1 MB. Zkus znovu. |
| 409 | `upload_in_progress` | Stejný `Idempotency-Key` ještě běží. Počkej 5 s. |

Změna limitů znamená vytvořit kontejner znovu s jinými `-e SIGNAGE_*`
(`deploy.sh` přebírá prostředí existujícího kontejneru).

## Kiosk po výpadku proudu nenastartuje

- Není to HOME aplikace. `set-home-activity` hlásí `Success`, ale box skončí v launcheru Android TV. Oprava (vratná):

  ```bash
  adb -s <box-ip>:5555 shell cmd package set-home-activity app.plainsignage.kiosk/.MainActivity
  adb -s <box-ip>:5555 shell pm disable-user --user 0 com.google.android.tvlauncher
  # zpět: adb -s <box-ip>:5555 shell pm enable com.google.android.tvlauncher
  ```

- Chybí `SYSTEM_ALERT_WINDOW`: kiosk překrývá jiná aplikace a nevrátí se; v logu je každých 20 s „isBgStartWhitelisted: false“. Udělení přežije `install -r`, ale ne `pm clear` ani `adb uninstall`.

  ```bash
  adb -s <box-ip>:5555 shell appops get app.plainsignage.kiosk SYSTEM_ALERT_WINDOW     # očekávej: allow
  adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk SYSTEM_ALERT_WINDOW allow
  ```

- Watchdog je pozastavený záměrně: dvojí BACK do 2 s ho na 10 minut pozastaví.
- Po bootu není síť: kiosk ukáže chybu a opakuje (každých 5 s, u síťových chyb a HTTP 5xx s růstem až na 60 s). Zkontroluj Wi-Fi nebo kabel a `KIOSK_BASE_URL`.
- ADB nedostupné: na některých boxech `adb connect` po restartu zmizí; zapni ladění USB znovu a celou instalaci udělej v jedné relaci.
- Instalace APK selže (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`): jiný podpisový klíč nebo nezměněný `versionCode`. Zvyš verzi v `kiosk-app/AndroidManifest.xml` a použij stejný keystore; jinak nejdřív `adb uninstall app.plainsignage.kiosk` a znovu udělit oprávnění.
- Bez ADB a kiosk nejde opustit: dvakrát stiskni BACK do 2 s.
- Spořič a uspávání: nastavení viz [kiosk.md](kiosk.md#příprava-boxu) (nikdy `-1`).
- Studený start po odpojení napájení a vypnutí a zapnutí TV nejsou ověřené; vyzkoušej je fyzicky.

## Nová verze, cache a service worker

- „New version, reloading“ je v pořádku: po nasazení nese playlist vyšší `build` a přehrávač se obnoví při přechodu mezi položkami do 2 minut. Při výpadku má cachovaný playlist starý build a obnova se nekoná.
- „Offline, playing the saved copy“: přehrávač hraje z Cache Storage. Playlist se stane offline balíčkem až po stažení všech médií, takže otevři `/tv/<screen>` online, počkej na stažení a pak teprve zkoušej offline.
- Obrazovka dál ukazuje starý kód: porovnej `version` v `/api/health` s buildem posledního `deploy.sh`, pak přehrávač restartuj nebo udělej tvrdé obnovení.
- Plná cache nebo selhávající ukládání: přehrávač posílá `quota` (volné místo) jen když už něco selhává; viz `storage_mb` v `/api/devices`. Uložený obsah se při nedostatku místa nemaže. Cache jsou `signage-media-v1`, `signage-shell-v2`, `signage-playlists-v1`; starší `signage-*` se při aktivaci smažou. HTML se servíruje s `no-store`.

## Špatné heslo a rate limity

- 401: špatný nebo chybějící basic auth. Zkontroluj `curl -s -o /dev/null -w '%{http_code}\n' -u '<user>:<password>' https://<host>/admin` (200). Hesla se znaky mimo ASCII potřebují `PYTHONUTF8=1`.
- 429 s `Retry-After`: 10 neúspěšných přihlášení za 300 s na IP, `pair/request` 30 za 300 s, upload 30 za 60 s (`SIGNAGE_RL_*`). Počkej, nebo `ssh <ssh-host> docker restart signage` (limity jsou jen v paměti; restart smaže i idempotentní cache a krátce přeruší službu, přehrávače hrají dál z cache). Nejdřív se podívej do `docker logs signage` a logu proxy, jestli nejde o útok; trvalá blokace patří do firewallu.
- 429 pro všechny najednou: chybí `SIGNAGE_TRUST_PROXY=1`, nebo proxy neposílá `X-Forwarded-For`. S `=1` a bez proxy si kdokoli podvrhne IP. Kontrola (vypíše počet):

  ```bash
  ssh <ssh-host> "docker inspect signage --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -c '^SIGNAGE_TRUST_PROXY=1\$'"
  ```

  `deploy.sh` proměnnou doplní, když chybí.
- Párování: kód platí 10 minut; neplatný nebo použitý kód vrátí 404 nebo 409. Vytvoř na TV nový.

## Plný disk

Příznaky: HTTP 507, `file_save_failed`, `backup.sh` hlásí málo místa.

1. `ssh <ssh-host> 'df -h /opt/signage; du -sh /opt/signage/*'`
2. Uvolni, co nepotřebuješ: staré zálohy (nech poslední ověřenou), staré images (`docker images signage`, `docker image prune`), `releases/`, `data.broken-*` po úspěšné obnově, logy.
3. Média ani `tv-state.json` nemaž ručně; smaž je v adminu.
4. Zkontroluj `/api/health` (zápisy stavu jsou atomické) a spusť `ssh <ssh-host> /opt/signage/backup.sh`.

Docker logy se rotují (10 MB x 3), pokud byl kontejner vytvořen s příznaky `--log-opt` z [install.md](install.md#docker-compose), zálohy drží 14, `deploy.sh` drží 5 images. Když
`/api/health` ukazuje `state_error`, je poškozený stav, ne disk.

## Poškozený soubor stavu

Příznak: `/api/health` není `ok` a ukazuje `state_error`, zápisy jsou zablokované.
Když server načte nečitelný soubor, zkopíruje ho vedle souboru stavu jako
`tv-state.json.corrupt-<YYYYMMDD-HHMMSS>-<n>.bak` (mód 600, drží se posledních 5,
`SIGNAGE_STATE_BACKUP_RETENTION`); když se kopie nepovede, zápisy zůstanou
zablokované, aby se nic nepřepsalo.

1. `docker logs signage 2>&1 | grep -iE "state|backup|error"`
2. `python3 -m json.tool /opt/signage/data/tv-state.json` a totéž na `.corrupt-*.bak`.
3. `docker stop signage`, obnov poslední dobrý stav (další dvě sekce), `docker start signage`, zkontroluj `/api/health`.

## Obnova ze zálohy dat

Denní `signage-data-<STAMP>.tgz` v `/opt/signage/backups`, drží se posledních 14
(nastavení v [install.md](install.md#zálohy)).

```bash
scp deploy/restore.sh <ssh-host>:/opt/signage/restore.sh && ssh <ssh-host> chmod +x /opt/signage/restore.sh
ssh <ssh-host>
/opt/signage/restore.sh --dry-run latest          # jen vypíše, co by udělal
/opt/signage/restore.sh latest                    # nejnovější záloha
/opt/signage/restore.sh /opt/signage/backups/signage-data-<STAMP>.tgz
```

Skript ověří archiv (`tar -tzf`, platný `tv-state.json`, volné místo), zastaví
kontejner, přesune stávající data do `/opt/signage/data.broken-<stamp>` (nikdy
nemaže), rozbalí, ověří a kontejner nastartuje. Při jakékoli chybě vrátí původní
adresář a skončí nenulovým kódem. Sdílí zámek s `backup.sh`; cesty lze změnit
přes `SIGNAGE_DATA_DIR`, `SIGNAGE_BACKUP_DIR`, `SIGNAGE_CONTAINER`
(`SIGNAGE_RESTORE_SKIP_DOCKER=1` nechá docker na pokoji). Potom zkontroluj
`/api/health` a že se obrazovky hlásí. Když byla obnova omyl:

```bash
docker stop signage
mv /opt/signage/data /opt/signage/data.restored-$(date +%Y%m%d-%H%M%S)
mv /opt/signage/data.broken-<STAMP> /opt/signage/data
docker start signage
```

Bez Dockeru: skript zastavuje jen Docker kontejner, takže běžící `tvserver.py`
by obnovený stav přepsal. Nejdřív ho zastav, obnov bez zásahu do dockeru a pak
server spusť znovu:

```bash
# zastav tvserver.py (Ctrl+C nebo kill), potom:
SIGNAGE_RESTORE_SKIP_DOCKER=1 SIGNAGE_DATA_DIR=./data SIGNAGE_BACKUP_DIR=./backups ./deploy/restore.sh latest
# znovu spusť tvserver.py, viz install.md "Bez Dockeru"
```

`data.broken-*` smaž až po ověření provozu. Ověřeno jen testem
`deploy/test-backup-restore.sh`, ne na živé službě.

## Obnova snapshotu před nasazením

Před přepnutím `deploy.sh` uloží `tv-state.json` do
`/opt/signage/backups/tv-state.json.pre-<release>` (release =
`timestamp-g<gitSHA>[-dirty]`, drží se posledních 5). Obnoví jen stav, ne média ani kód.

```bash
docker stop signage
cp -p /opt/signage/backups/tv-state.json.pre-<release> /opt/signage/data/tv-state.json
docker start signage
```

## Nasazení a rollback

- `deploy.sh` selže před přepnutím (testy, canary, health): produkce zůstává beze změny. Přečti výstup a `docker logs`. Typicky: `docker inspect signage` selže, protože kontejner ještě neexistuje (skript neumí první instalaci, viz [install.md](install.md#produkční-nasazení)); `pytest: command not found`.
- Selže po přepnutí: skript se vrátí sám (starý kontejner byl jen přejmenovaný; jinak z image `signage:rollback-<release>`). Když selže i to, zkontroluj `docker ps -a`, `docker images signage` a `signage` vytvoř ručně původním `docker run` a tímto image.
- Rollback vrací kód, ne data; stav obnov ze snapshotu.
- Po nasazení se přehrávače aktualizují do 2 minut; zbylá chyba je přehrávač v cache se starým kódem. Ověř přes `BASE=https://<host> AUSER='<user>' APASS='<password>' ./deploy/smoke-test.sh`. Test hlaviček (HSTS, `X-Frame-Options`, `X-Content-Type-Options`) je za holým nginx červený; to je proxy, ne služba.

## Rotace přístupů

- Heslo admina: vygeneruj ho (`openssl rand -base64 24`), vytvoř kontejner znovu s novým `-e SIGNAGE_ADMIN_PASS=...` (běžné nasazení ho pak zachová), ověř, že staré přihlášení dá 401 a nové 200. Nikdy ho necommituj ani nenechávej v historii shellu.
- Telegram token: `/revoke` u @BotFather (starý token přestane platit hned), vytvoř kontejner znovu s `-e SIGNAGE_TG_TOKEN=<TOKEN> -e SIGNAGE_TG_CHAT=<chat id>`, zkontroluj `alerts_enabled: true` v `/api/health`. Canary při nasazení nikdy nemá Telegram údaje.
- Podpisový keystore kiosku rotaci nemá: jeho ztráta znamená přeinstalovat každý box po `adb uninstall`. Zálohuj ho.

## Telegram alert nedorazí

Alerty jsou zapnuté jen s oběma `SIGNAGE_TG_TOKEN` a `SIGNAGE_TG_CHAT`.

1. `alerts_enabled` v `/api/health` musí být true; jinak kontejner vytvoř znovu s oběma. `chat.id` zjistíš z `https://api.telegram.org/bot<TOKEN>/getUpdates` po zprávě botovi.
2. `ssh <ssh-host> 'docker logs signage 2>&1 | grep "alert: telegram"'` (token je zamaskovaný). Příčiny: neplatný nebo zrušený token, špatné chat id, bot nemá s chatem konverzaci (napiš mu první), nebo není cesta na `api.telegram.org`. Neodeslané zprávy se zkoušejí každou minutu.
3. Offline alert přijde po `SIGNAGE_ALERT_OFFLINE_MIN` (10) minutách; po restartu se hlásí jen obrazovky, které se od startu ozvaly. Prázdná (`[WARNING]`) a offline (`[CRITICAL]`) se nehlásí zároveň.

## Neověřeno

- Studený start boxu po odpojení napájení, vypnutí a zapnutí TV a watchdog při skutečném výpadku sítě.
- `restore.sh` a `backup.sh` na živé službě; spuštěn byl jen test `deploy/test-backup-restore.sh`.
