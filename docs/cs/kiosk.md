# Kiosk, Android box a Wi-Fi pomocník

[English](../en/kiosk.md)

Kiosk je minimální launcher s jedním celoobrazovkovým WebView pro Android
přehrávací box (balíček `app.plainsignage.kiosk`, verze 1.0, `versionCode` 1).
Vznikl a ověřil se na Mecool KM9 Pro (Android 10) a Xiaomi Mi TV Stick
(Android 9). Všechny příkazy níže běží přes ADB proti `<box-ip>:5555`.

## Než začneš

- Běžící server dostupný přes HTTPS na `<host>` s certifikátem, kterému Android věří. Rychlý start s Compose obsluhuje prosté HTTP na portu 8080, které kiosk použít nemůže (viz [Build](#build)); TLS se řeší před serverem podle části [Produkční nasazení](install.md#produkční-nasazení).
- Android box nebo stick (Android 9 nebo novější) ve stejné LAN jako tvůj počítač po dobu instalace.
- Počítač s JDK 17, Android SDK (platforma `android-34`, build-tools, platform-tools kvůli `adb`) a `zip`. Hotové APK se nepublikuje: repozitář nemá žádné vydání, které by ho obsahovalo, a adresa serveru se zkompiluje do aplikace, takže si APK sestavíš sám, jednou pro každý server.
- ADB (Android Debug Bridge) zapnuté na boxu, viz [Příprava boxu](#příprava-boxu). Volitelně [scrcpy](#vzdálený-přístup-volitelný) pro zobrazení obrazovky boxu v počítači.

Pořadí práce: sestavit APK, připravit box, nainstalovat APK, spárovat box, projít [Rychlou kontrolu](#rychlá-kontrola).

Aplikace nesou názvy balíčků `app.plainsignage.kiosk` a `app.plainsignage.wifi`. Box s dřívější neveřejnou verzí pod starým názvem balíčku vyžaduje odinstalaci a novou instalaci, protože Android bere jiný balíček jako jinou aplikaci.

Proč ne prostý prohlížeč: po výpadku proudu prohlížeče nenastartují (nemají
`BOOT_COMPLETED` a Android 10 zakazuje spouštět aktivitu z pozadí). Aplikace
deklaruje `category.HOME`, takže ji systém spustí sám jako součást bootu. Navíc
nemá žádné ovládací prvky prohlížeče, přehrává zvuk bez klepnutí
(`setMediaPlaybackRequiresUserGesture(false)`), drží obrazovku zapnutou a
neúspěšné načtení opakuje každých 5 s s růstem až na 60 s.

## Build

Žádný gradle a žádné závislosti: `aapt2 -> javac -> d8 -> zipalign -> apksigner`,
viz `kiosk-app/build.sh`. Potřebuješ JDK 17 (poskytuje `javac` a `keytool`),
`zip` a Android SDK s platformou `android-34` (přepíše se přes `API=`),
balíčkem build-tools (`aapt2`, `d8`, `zipalign`, `apksigner`) a platform-tools
(`adb`, potřeba k instalaci). SDK se hledá v `ANDROID_HOME`, `ANDROID_SDK_ROOT`,
pak `~/Android/Sdk`. Použij JDK 17: `d8` zatím padá pod JDK 21.

Skript skončí chybou, pokud v `PATH` chybí `aapt2 javac d8 zipalign apksigner
keytool`, ale adresář `build-tools/<verze>` z SDK do `PATH` nepřidá a nekontroluje
`zip`, který volá v kroku 4. Nástroje proto nejdřív dej do `PATH`, například
(balíčky nainstaluje `sdkmanager` ze SDK; `<verze>` je nainstalovaná verze
build-tools, třeba `34.0.0`):

```bash
export ANDROID_HOME=~/Android/Sdk
export PATH="$ANDROID_HOME/build-tools/<verze>:$ANDROID_HOME/platform-tools:$PATH"
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" "platforms;android-34" "build-tools;<verze>" "platform-tools"
```

Hotové APK ke stažení není. Adresa služby se zkompiluje do APK a aplikace
odmítne každý jiný host, takže APK patří k jednomu serveru; nová adresa
vyžaduje nový build.

```bash
cd kiosk-app
KIOSK_BASE_URL=https://<host> ./build.sh                                   # build/signage-kiosk.apk
KIOSK_ADB=<box-ip>:5555 KIOSK_BASE_URL=https://<host> ./build.sh install   # sestavit a nainstalovat přes ADB
```

- `KIOSK_BASE_URL` je povinná a musí být `https://`; výchozí hodnota není, takže
  APK nikdy omylem nevznikne proti špatnému serveru. Povolený host a port se
  odvozují z ní (například `https://signage.example.com`). Prosté HTTP nejde
  vůbec: manifest vypíná nešifrovaný provoz a allowlist adres `http://`
  odmítne. Aplikace chyby certifikátu nepřebíjí, platí tedy výchozí chování
  Androidu: self-signed nebo prošlý certifikát či certifikát pro jiné jméno
  znamená, že se stránka nenačte a box to dokola zkouší. Použij certifikát od
  veřejné CA (například Let's Encrypt) přesně pro daný host a zkontroluj datum
  a čas na boxu.
- `build.sh install` jen spustí `adb connect` a `adb install -r`. Kroky s
  oprávněními a launcherem z [Přípravy boxu](#příprava-boxu) jsou dál potřeba.
- Podpisový klíč `kiosk-app/kiosk.keystore` se vytvoří při prvním buildu a
  zůstává lokální (je v `.gitignore`). Zálohuj ho: box odmítne aktualizaci
  podepsanou jiným klíčem a jediná cesta ven je `adb uninstall app.plainsignage.kiosk`.
  Heslo se při
  prvním buildu vygeneruje do `kiosk.keystore.pass` (nebo se vezme z `KIOSK_KS_PASS`):
  zálohuj ho spolu s keystore. Když se keystore nebo jeho heslo ztratí, nejde
  nic obnovit: smaž oba soubory, sestav s novým klíčem a na každém boxu spusť
  `adb uninstall app.plainsignage.kiosk`, nainstaluj znovu, zopakuj příkazy
  `appops` a box znovu spáruj. Aktualizace ze serveru přes hranici klíčů nefungují.
- Aby se APK přijalo jako aktualizace, zvyš `versionCode` a `versionName` v
  `kiosk-app/AndroidManifest.xml`.

Testy jsou v čisté Javě bez Androidu a nejsou součástí APK:

```bash
cd kiosk-app
T=$(mktemp -d)
javac -d "$T" src/app/plainsignage/kiosk/Allowlist.java test/AllowlistTest.java && java -cp "$T" app.plainsignage.kiosk.AllowlistTest
# stejně pro WatchdogTest, UpdatePolicyTest (+ UpdatePolicy.java), HealthTest (+ Health.java),
# LoadErrorTest (+ LoadError.java) a DiagTest (+ Diag.java)
```

### Zkouška doma bez vlastní domény

Kiosk potřebuje `https://` s certifikátem veřejné CA, takže zkouška v domácí síti
bez vlastní domény je nepohodlná. Čistě na zkoušku lze použít wildcard DNS
službu, která certifikát zveřejňuje ke stažení: `local-ip.sh` přeloží host
například `192-168-1-10.local-ip.sh` na LAN adresu `192.168.1.10` a certifikát
s klíčem ze stránky [local-ip.sh](https://local-ip.sh) platí pro toto jméno.
Dej je do nginx před server (cesty k certifikátu jsou zástupné):

```nginx
server {
    listen 9443 ssl;
    server_name 192-168-1-10.local-ip.sh;
    ssl_certificate     /etc/nginx/local-ip/server.pem;
    ssl_certificate_key /etc/nginx/local-ip/server.key;
    client_max_body_size 110m;
    location / {
        proxy_pass http://<ip-serveru>:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

Na serveru pak nastav `SIGNAGE_PUBLIC_URL=https://192-168-1-10.local-ip.sh:9443`
a aplikaci sestav s `KIOSK_BASE_URL=https://192-168-1-10.local-ip.sh:9443`
(v obou stejný host i port). Takto to bylo ověřeno na Philips Android TV.

Soukromý klíč tohoto certifikátu je veřejný, takže se za tento host může
vydávat kdokoli. Použij to jen na zkoušku s demo daty a nikdy pro reálný provoz.
Pro reálný provoz je vlastní doména a Let's Encrypt, viz
[Produkční nasazení](install.md#produkční-nasazení).

## Příprava boxu

Postup je pro oba boxy stejný. Xiaomi stick je 32bitový s 1 GB RAM (po bootu
zhruba 250 MB volných, po odstranění balastu 420 MB) a potřebuje novější WebView
z Play. Mecool má 2 GB RAM, ethernetový port a vendor OTA balíček, který je
mrtvý aktualizační kanál. Hledej Android 9 nebo novější s Google Play, aspoň
1 GB RAM, síťové ADB a ovladač s tlačítky OK a BACK.

Najdi `<box-ip>` v nastavení sítě boxu (podrobnosti připojení Wi-Fi nebo
Ethernet) nebo v seznamu klientů na routeru; pevná adresa z routeru zabrání její
změně.

Zapni ADB (Nastavení, Předvolby zařízení, Informace, vyber 7x Sestavení, pak
Možnosti pro vývojáře, Ladění USB), box i počítač musí být ve stejné LAN.
Síťové ADB na portu 5555 existuje jen tehdy, když ho box nabízí: některé boxy
ukazují jen USB ladění a Android 11 a novější má Bezdrátové ladění s vlastním
portem a `adb pair` (zde netestováno). Když `adb connect` odmítne spojení,
hledej v Možnostech pro vývojáře volbu síťového ADB.

```bash
adb connect <box-ip>:5555        # potvrď otisk klíče na TV ("Vždy povolit")
adb devices                      # <box-ip>:5555   device
```

Na Mecoolu síťové ADB po restartu zmizí, takže celou instalaci udělej v jedné
relaci; na Xiaomi sticku port 5555 restart přežije. Když zmizí, vypni a zapni
ladění USB v Možnostech pro vývojáře a znovu spusť `adb connect` (neověřeno na
každém boxu). Nainstalovaný kiosk běží i bez ADB; ADB je potřeba jen k nastavení
a údržbě. Záložní cesta je kabel USB-A na USB-A do OTG portu (USB host), ale
obyčejný A-A kabel spojí 5V větve obou zařízení.

Vypni vendor telemetrii, obchod, asistenta, spořiče a aktualizační balíčky
(vratně), po uložení výchozího stavu:

```bash
adb shell pm list packages > packages-before.txt
adb shell getprop > getprop-before.txt
adb shell pm disable-user --user 0 <package>      # zpět: adb shell pm enable <package>
```

Google Play nech: je to jediné, co aktualizuje WebView. Až je WebView aktuální,
lze Play vypnout (`pm disable-user --user 0 com.android.vending`), což šetří RAM
a předchází překvapením; cenou je, že WebView se pak aktualizuje jen ručně
(Play zapnout, aktualizovat, znovu vypnout).

Stav balíčků se na disk
zapisuje se zpožděním, takže po změně balíčků počkej asi 40 sekund před
`adb reboot`, jinak se změna může ztratit. Na Mecoolu odstraní mrtvý OTA kanál
`pm disable-user --user 0 com.droidlogic.vsota`.

WebView: původní WebView 81 na Xiaomi sticku nechal přehrávač černý (CSS `inset`
potřebuje Chrome 87). Přihlas se do Play vyhrazeným účtem a aktualizuj „Android
System WebView“ (tam šlo o verzi 138; Mecool měl 150).

Nainstaluj kiosk a nastav ho jako launcher (spusť z kořene repozitáře po
[buildu](#build); pokud jsi použil `build.sh install`, řádek `install` je už
hotový):

```bash
adb -s <box-ip>:5555 install -r kiosk-app/build/signage-kiosk.apk
adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk SYSTEM_ALERT_WINDOW allow
adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk REQUEST_INSTALL_PACKAGES allow
adb -s <box-ip>:5555 shell cmd package set-home-activity app.plainsignage.kiosk/.MainActivity
adb -s <box-ip>:5555 shell pm disable-user --user 0 com.google.android.tvlauncher
```

Na boxu s Google Play může během `adb install` na TV vyskočit dialog Play Protect a příkaz čeká, dokud na něj někdo neodpoví ovladačem. Vyber možnost, která aplikaci přesto nainstaluje (je pod odkazem s dalšími podrobnostmi, přesné znění se liší podle verze). Když dialog odmítneš, `adb install` skončí chybou `INSTALL_FAILED_VERIFICATION_FAILURE`; spusť ho znovu. Vyzkoušeno na televizi Philips s Androidem 12, kde se dialog objevil při první instalaci.

Dialog pochází z Google Play (`com.android.vending`) a objeví se proto, že aplikace není z Play Store a nemá tam žádnou reputaci. Zveřejnění APK jinde na tom nic nemění a každé sestavení je navíc jiný soubor, protože se do něj kompiluje adresa serveru. Jak se mu vyhnout:

- Odpovědět na něj při každé instalaci. Jen tuto cestu jsem tu vyzkoušel.
- Vypnout kontrolu aplikací v nastavení Play Store na boxu (Play Protect, kontrolovat aplikace). Nevyzkoušeno, znění nabídky se liší podle verze.
- Použít box bez Google Play nebo takový, kde je Play vypnutý (`adb shell pm disable-user --user 0 com.android.vending`). Pak se nikdo neptá. Nevyzkoušeno a bez Play se systémový WebView neaktualizuje automaticky, viz [Příprava boxu](#příprava-boxu).

`set-home-activity` hlásí `Success`, ale samo nic nezmění (systémový launcher má
prioritu 2, kiosk 0); HOME na kiosk přepne až vypnutí
`com.google.android.tvlauncher`. Po `adb reboot` kiosk nastartuje sám: na
Mecoolu zhruba 60 až 77 s do prvního hlášení, na Xiaomi asi 33 s do popředí.
Zpět `pm enable com.google.android.tvlauncher`. Při vypnutém systémovém
launcheru vede opuštění kiosku (dvakrát BACK) na seznam aplikací a do
nastavení systému, takže Wi-Fi je vždy dosažitelná.

Udrž box vzhůru. Nikdy nepoužívej `screen_off_timeout -1`: systém ho zvedne na
asi 10 sekund a obrazovka se hned vypne.

```bash
adb -s <box-ip>:5555 shell settings put secure sleep_timeout -1
adb -s <box-ip>:5555 shell settings put system screen_off_timeout 2147483647
adb -s <box-ip>:5555 shell settings put secure screensaver_enabled 0
adb -s <box-ip>:5555 shell settings put secure screensaver_activate_on_sleep 0
adb -s <box-ip>:5555 shell settings put secure screensaver_activate_on_dock 0
adb -s <box-ip>:5555 shell settings put global stay_on_while_plugged_in 7
```

Původní hodnoty Mecoolu pro návrat: `secure sleep_timeout 10800000`,
`system screen_off_timeout 900000`, `secure screensaver_enabled 1`,
`secure screensaver_activate_on_sleep 1`. HDMI-CEC je na Xiaomi sticku z výroby
vypnuté, takže vypnutí TV stick neuspí.

Síť a hygiena:

- Má-li box ethernetový port, dej přednost kabelu. Box dej do vlastní VLAN nebo hostovské SSID s odchozím provozem jen na signage server; přehrávač nic z LAN nepotřebuje.
- Volitelný vzdálený přístup přes VPN je popsán v části [Vzdálený přístup](#vzdálený-přístup-volitelný).
- Po dokončení práce vypni ladění USB a z jiného stroje ověř, že port 5555 je zavřený; ADB bez ověření na 5555 se aktivně zneužívá. Soubory s výchozím stavem si nech k pozdějšímu porovnání.
- Device owner s lock task se nedoporučuje: vyžaduje tovární reset bez jakéhokoli účtu, čímž se ztratí Play a s ním aktualizace WebView.
- Návrat: `pm enable com.google.android.tvlauncher` a každý další vypnutý balíček (`adb shell pm list packages -d` vypíše vypnuté; `packages-before.txt` je tvůj výchozí stav), `adb uninstall app.plainsignage.kiosk` a vrácení nastavení změněných výše (původní hodnoty Mecoolu jsou uvedené výše; u jiných boxů je kroky neukládají, proto si je před změnou přečti přes `adb shell settings get <namespace> <key>`). Tovární reset v nastavení boxu je poslední možnost; smaže vše, včetně aktualizace WebView.

## První spuštění a párování

```bash
adb -s <box-ip>:5555 shell appops get app.plainsignage.kiosk SYSTEM_ALERT_WINDOW     # očekávej: allow
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity
```

Bez uložené adresy aplikace otevře `<KIOSK_BASE_URL>/connect`. TV ukáže
nadpis „Připoj tuto TV v administraci“, QR kód, šestiznakový kód a „Kód platí
10 min.“ (česky nebo anglicky, s tlačítky CS a EN). Když se stránka nenačte, box
to opakuje každých 5 s s růstem až na 60 s; příčiny jsou v Rychlé kontrole.
Párování:

1. Naskenuj QR kód telefonem. Otevře `https://<host>/admin?pair=<CODE>`;
   přihlas se údaji admina. Když skenovat nejde, napiš tu adresu ručně: admin
   nemá pole pro zadání kódu.
2. Admin ukáže zelený pruh „Připojit novou TV s kódem <CODE> k obrazovce
   <název>“. Obrazovka je ta, kterou jsi v daném prohlížeči naposledy použil;
   jinou vybereš adresou `https://<host>/admin?pair=<CODE>&device=<screen>`.
3. Klikni na **Připojit TV**. Při dalším pollu box přejde na `/tv/<screen>`, adresu
   uloží a po bootu na ní startuje.

QR kód se skládá z hlaviček požadavku nebo z `SIGNAGE_PUBLIC_URL`; když míří na
`http://` nebo na špatný host, nastav na serveru `SIGNAGE_PUBLIC_URL=https://<host>`.
Prošlý kód se při novém spuštění aplikace (`am start` výše) nahradí novým. Bez
párování spusť s adresou; aplikace přijme jen `https://` a přesně svůj host:

```bash
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity \
  -d "https://<host>/tv/<screen>"
```

## Rychlá kontrola

1. Server: `curl -s https://<host>/api/health` z tvého počítače vrátí `"ok": true` bez `-k`. Když si curl stěžuje na certifikát, bude i box.
2. Instalace: `adb -s <box-ip>:5555 shell pm list packages | grep app.plainsignage` vypíše `app.plainsignage.kiosk`.
3. Obraz: po spárování TV přehrává playlist obrazovky. V adminu je obrazovka do asi 45 s online a má verzi kiosku.
4. Logy, když ne: `adb -s <box-ip>:5555 logcat -d -s SignageKiosk` (odmítnuté adresy a důvod). Prázdná nebo opakující se obrazovka je obvykle certifikát, kterému box nevěří, špatná `KIOSK_BASE_URL`, chybějící cesta z boxu na `<host>` nebo špatné datum na boxu.
5. Boot: `adb -s <box-ip>:5555 reboot`; kiosk se má sám vrátit k obsahu asi za jednu až dvě minuty. Studený start po vytažení napájení není ověřený (viz konec stránky).

Až skončíš, vypni ladění USB (viz Síť a hygiena výše).

### Stavová obrazovka a diagnostika

Když se hlavní stránka nedá načíst, kiosk přes WebView ukáže stavovou obrazovku (stejný styl jako výzva k aktualizaci): důvod (certifikát nebo SSL, server neodpovídá nebo vypršel limit, bez sítě nebo nepřeložené jméno, HTTP 5xx), host serveru, IP adresu boxu a odpočet do dalšího pokusu (5, 10, 20, 40, pak 60 s). Zmizí, jakmile se stránka načte. Kiosk nikdy nepřijme nedůvěryhodný certifikát: požadavek se vždy zruší a obrazovka jen ukáže důvod a dál zkouší znovu. Důvody certifikátu: vypršel, ještě není platný, vydán pro jiný host, nedůvěryhodná autorita, neplatné datum. Většina z nich radí zkontrolovat datum a čas na boxu, protože špatné hodiny (typicky po výpadku proudu na boxu bez záložních hodin) udělají z platného certifikátu vypršelý nebo nedůvěryhodný.

Při startu kiosk zapíše do logu jeden řádek (`adb logcat -d -s SignageKiosk`): verze, host serveru a zda je uložená obrazovka (jen její cesta `/tv/<name>`).

Pro technika s ADB je tu na 15 s přehled (verze, host serveru, uložená obrazovka, poslední chyba, RAM, síť a signál Wi-Fi, pády rendereru, stáří poslední načtené stránky):

```bash
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity --ez diag true
```

## Provoz

- Opuštění kiosku bez ADB: dvakrát stiskni BACK do dvou sekund. Výstup vede na systémový launcher, je-li zapnutý, jinak na seznam aplikací a nakonec do nastavení systému.
- Diagnostika (poslední chyba, verze, stav): tlačítko MENU nebo INFO na ovladači. Admin u obrazovky ukazuje verzi kiosku (`kiosk` v `GET /api/devices`).
- Bezpečnost adresy: `am start -d` i navigace ve WebView přijmou jen `https://`, přesně host a port z `KIOSK_BASE_URL` a cestu `/`, `/connect` nebo `/tv/<něco>` (bez `..`, `//`, `\`, `%2e`, user-info). Cokoli jiného se ignoruje, včetně uložené adresy; důvod se zapíše do logu pod `SignageKiosk`. Dílčích požadavků se to netýká. Nešifrovaný provoz a záloha aplikace jsou vypnuté.
- Watchdog: každých 5 s aplikace kontroluje, že je v popředí, a po 20 s bez popředí volá `startActivity` s `REORDER_TO_FRONT`. Dvojí BACK ho na 10 minut pozastaví. Android 10 blokuje start aktivity z pozadí („isBgStartWhitelisted: false“ v logu), takže funguje jen se `SYSTEM_ALERT_WINDOW`, které přežije `install -r`, ale ztratí se po `pm clear` a `adb uninstall`. Bez něj se odmítnutý pokus jen zapíše do logu každých 20 s.
- Pády: neodchycená výjimka kiosk restartuje, ale ne dřív než 30 s po předchozím restartu (zpoždění přes `AlarmManager`).

## Aktualizace kiosku ze serveru

Aplikace se jednou za minutu ptá na `GET /api/kiosk/release`.
Aktualizuje se jen na požadavek admina pro danou obrazovku, nebo jednou po
zapnutí boxu (do 10 minut po bootu).

Předpoklad: box už má kiosk nainstalovaný přes ADB; server aktualizuje jen existující. Publikační skript pracuje přes SSH s rozložením hostitele z části [Produkční nasazení](install.md#produkční-nasazení) (`$SIGNAGE_REMOTE_ROOT/data/kiosk`, v kontejneru `/data/kiosk`, stejné jako `SIGNAGE_KIOSK_DIR`); pro Compose se skript nehodí a soubory, které by zapsal, ukáže `--dry-run`.

1. Sestav nové APK s vyšším `versionCode` a stejným keystore.
2. Publikuj ho (ověří APK, zapíše `signage-kiosk.apk` a `kiosk.json`, bez `--force` nikdy nesníží verzi; používá `SIGNAGE_SSH_ALIAS` a `SIGNAGE_REMOTE_ROOT` jako `deploy.sh`):

   ```bash
   ./deploy/tools/publish-kiosk.sh --dry-run          # jen ověří, vypíše kiosk.json a cíl
   ./deploy/tools/publish-kiosk.sh                    # kiosk-app/build/signage-kiosk.apk
   ```

3. V adminu v sekci dostupnosti obrazovky klikni na **Aktualizovat kiosek**.
4. Box stáhne APK, ověří velikost, SHA-256, balíček a vyšší `versionCode` a ukáže vlastní výzvu („Nová verze <x> je připravena“, „Stiskni OK na ovladači“). OK otevře systémový instalační dialog (jedno OK na ovladači; tichá instalace na Androidu 9/10 bez device owner nejde); Zpět znamená „teď ne“ (`declined`). Čeká 5 minut, pak se vrátí k obsahu a znovu to zkusí jen na nový požadavek.
5. Po aktualizaci kiosk znovu spustí příjemce `MY_PACKAGE_REPLACED`.

Jednou na box: `adb shell appops set app.plainsignage.kiosk REQUEST_INSTALL_PACKAGES allow`.
Rozhodovací logika je v `UpdatePolicy.java`.

## Zdraví boxu

Stejný poll nese volnou a celkovou RAM, příznak nízké paměti, uptime, typ sítě
a sílu Wi-Fi v dBm a pády rendereru WebView od startu procesu. SSID se nikdy
nečte ani neposílá. Admin to ukazuje u obrazovky a Telegram může upozornit na
trvale nízkou volnou RAM. Pole a prahy: [api.md](api.md#zdraví-boxu). Logika je v
`Health.java`.

## Vzdálený přístup (volitelný)

Nic z této části není povinné. Server, přehrávač i kiosk fungují bez jakékoli VPN; kiosk potřebuje jen dosáhnout na tvůj server s cedulemi.

První linie vzdálené péče je vestavěná: kiosk hlásí serveru RAM, signál Wi-Fi, pády a poslední restart a admin je ukazuje u každé obrazovky (viz [Zdraví boxu](#zdraví-boxu)). VPN sáhni až tehdy, když potřebuješ vidět samotný box, třeba přečíst `logcat` nebo obraz.

Box nemusí být dostupný z internetu a neměl by být. Nepřesměrovávej port 5555 na routeru. Použij soukromou síť, například [Tailscale](https://tailscale.com) (podobná VPN funguje stejně):

1. Nainstaluj na box VPN aplikaci a přihlas se. Box označ štítkem (třeba `tag:signage-box`), aby o přístupu rozhodovala ACL, a ne účet, pod kterým je přihlášený.
2. Box nech přijímat spojení, ale žádná nezačínat. S ACL, která vše zakazuje, to znamená, že se objeví jen jako cíl:

   ```json
   {
     "tagOwners": { "tag:signage-box": ["autogroup:admin"] },
     "acls": [
       { "action": "accept", "src": ["autogroup:admin"], "dst": ["tag:signage-box:5555"] }
     ]
   }
   ```

3. Spusť aplikaci při bootu: `adb shell settings put secure always_on_vpn_app com.tailscale.ipn` a `adb shell settings put secure always_on_vpn_lockdown 0`. Lockdown zůstává vypnutý, aby mrtvá VPN box neodřízla od serveru s cedulemi.
4. Připoj se přes adresu ve VPN: `adb connect <vpn-ip>:5555`, živý náhled `scrcpy -s <vpn-ip>:5555 --max-size 1280` (scrcpy je samostatný nástroj pro tvůj počítač; nainstaluj ho z balíčků distribuce nebo ze stránky projektu).

Počítej asi s 40 MB RAM na VPN aplikaci, což na boxu s 1 GB paměti něco znamená. Tento postup není součástí automatických testů. Až skončíš, vypni USB ladění, pokud ho nepotřebuješ, a z jiného stroje ověř, že je port 5555 na běžné adrese boxu zavřený: ADB bez ověření na 5555 se aktivně zneužívá.

## Wi-Fi pomocník

`wifi-app/` (`app.plainsignage.wifi`) je malá aplikace bez UI, která přes ADB přidá,
vypíše nebo odebere uloženou Wi-Fi síť, tedy i na dálku přes VPN. Existuje,
protože ADB shell na Androidu 9/10 Wi-Fi přidat neumí (`cmd wifi add-network`
je až od Androidu 11) a `WifiManager.addNetwork` na Androidu 10 funguje jen pro
aplikaci s `targetSdk` 28 nebo nižším, kdežto kiosk cílí na 29. Hlavní využití:
servisní hotspot uložený předem na každém boxu; když se změní Wi-Fi na místě,
zapneš hotspot telefonu, box se k němu připojí a zbytek nastavíš na dálku. Box se
připojí k nejsilnější známé síti v dosahu.

```bash
cd wifi-app
./build.sh                                   # build/signage-wifi.apk
KIOSK_ADB=<box-ip>:5555 ./build.sh install   # sestaví, nainstaluje a udělí polohu (na Androidu 10 nutná pro výpis)
```

SDK požadavky jsou stejné jako u kiosku; klíč `wifi-app/wifi.keystore` se vytvoří
při prvním buildu a zůstává lokální. Jeho heslo se vygeneruje do `wifi.keystore.pass`
(chmod 600) nebo se vezme z `WIFI_KS_PASS`; když keystore existuje a nic z toho chybí, build skončí. Zálohuj obojí. Na Androidu 10 Play Protect odmítne
aplikaci s `targetSdk` 28 instalovanou přes ADB (`INSTALL_FAILED_VERIFICATION_FAILURE`),
proto ověřování vypni jen na dobu instalace:

```bash
adb shell settings put global verifier_verify_adb_installs 0
adb install -r build/signage-wifi.apk
adb shell settings delete global verifier_verify_adb_installs
```

Extras intentu: `action`, `ssid`, `password`, `hidden`; akce `add`, `list`, `remove`.

```bash
adb shell 'am start -n app.plainsignage.wifi/.Wifi --es action add --es ssid "<network>" --es password "<password>"'
#   bez --es password = otevřená síť, --ez hidden true = skrytá síť
adb shell 'am start -n app.plainsignage.wifi/.Wifi --es action list'
adb shell 'am start -n app.plainsignage.wifi/.Wifi --es action remove --es ssid "<network>"'
adb shell 'logcat -d -s SignageWifi'        # výsledek (OK:/ERROR:), heslo se nikdy nelogí
```

Přidaná síť se jen povolí; box se od aktuální sítě nepřepne a Android neumí
nastavit prioritu. Síť uloženou ručně v nastavení aplikace nepřepíše ani
neodebere. Heslo WPA musí mít 8 až 63 znaků. Aktivitu spustí jen ADB (chrání ji
`WRITE_SECURE_SETTINGS`). Skutečné názvy sítí a hesla drž v soukromém souboru
mimo git.

## Ověřeno a neověřeno

Ověřeno na boxu: aplikace startuje, přehrávač hraje bez chyb vykreslení v
1920x1080, zvuk hraje bez klepnutí a po `adb reboot` kiosk nastartuje jako HOME
a do zhruba 60 s se ohlásí serveru; síťové ADB na testovacím boxu restart
přežilo. Neověřeno: studený start po odpojení napájení (před nechráněným
provozem ho vyzkoušej fyzicky, včetně HDMI-CEC), vypnutí a zapnutí TV a chování
watchdogu při skutečném výpadku sítě.

V listopadu 2026 byl postup ověřen také na čistém Philips Android TV
(Android 12): build podle této dokumentace, instalace přes `adb` s dialogem
Play Protect, spuštění, načtení párovací stránky, spárování z adminu, nahrání
obrázku a jeho zobrazení a odinstalace. Na stejné televizi byl ověřen i watchdog:
po stisku Domů se kiosk sám vrátil do popředí za zhruba 20 s (řádek v logu
`watchdog: kiosk is not in the foreground, bringing it back`), při udělené
`SYSTEM_ALERT_WINDOW`. Stále neověřeno: kiosk jako launcher (`set-home-activity`
a vypnutí systémového launcheru), studený start, aktualizace ze serveru a
Wireless debugging.
