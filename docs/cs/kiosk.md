# Kiosk, Android box a Wi-Fi pomocník

[English](../en/kiosk.md)

Kiosk je minimální launcher s jedním celoobrazovkovým WebView pro Android
přehrávací box (balíček `app.plainsignage.kiosk`, verze 1.0, `versionCode` 1).
Vznikl a ověřil se na Mecool KM9 Pro (Android 10) a Xiaomi Mi TV Stick
(Android 9). Všechny příkazy níže běží přes ADB proti `<box-ip>:5555`.

Aplikace nesou názvy balíčků `app.plainsignage.kiosk` a `app.plainsignage.wifi`. Box s dřívější neveřejnou verzí pod starým názvem balíčku vyžaduje odinstalaci a novou instalaci, protože Android bere jiný balíček jako jinou aplikaci.

Proč ne prostý prohlížeč: po výpadku proudu prohlížeče nenastartují (nemají
`BOOT_COMPLETED` a Android 10 zakazuje spouštět aktivitu z pozadí). Aplikace
deklaruje `category.HOME`, takže ji systém spustí sám jako součást bootu. Navíc
nemá žádné ovládací prvky prohlížeče, přehrává zvuk bez klepnutí
(`setMediaPlaybackRequiresUserGesture(false)`), drží obrazovku zapnutou a
neúspěšné načtení opakuje každých 5 s s růstem až na 60 s.

## Build

Žádný gradle a žádné závislosti: `aapt2 -> javac -> d8 -> zipalign -> apksigner`,
viz `kiosk-app/build.sh`. Potřebuješ Android SDK (platforma `android-34`,
přepíše se přes `API=`) a v `PATH` `aapt2 javac d8 zipalign apksigner keytool`.
SDK se hledá v `ANDROID_HOME`, `ANDROID_SDK_ROOT`, pak `~/Android/Sdk`.
Použij JDK 17: `d8` zatím padá pod JDK 21.

```bash
cd kiosk-app
KIOSK_BASE_URL=https://<host> ./build.sh                                   # build/signage-kiosk.apk
KIOSK_ADB=<box-ip>:5555 KIOSK_BASE_URL=https://<host> ./build.sh install   # sestavit a nainstalovat přes ADB
```

- `KIOSK_BASE_URL` je povinná a musí být `https://`; výchozí hodnota není, takže
  APK nikdy omylem nevznikne proti špatnému serveru. Povolený host se odvozuje z ní.
- Podpisový klíč `kiosk-app/kiosk.keystore` se vytvoří při prvním buildu a
  zůstává lokální (je v `.gitignore`). Zálohuj ho: box odmítne aktualizaci
  podepsanou jiným klíčem a jediná cesta ven je `adb uninstall app.plainsignage.kiosk`.
  Heslo se při
  prvním buildu vygeneruje do `kiosk.keystore.pass` (nebo se vezme z `KIOSK_KS_PASS`):
  zálohuj ho spolu s keystore.
- Aby se APK přijalo jako aktualizace, zvyš `versionCode` a `versionName` v
  `kiosk-app/AndroidManifest.xml`.

Testy jsou v čisté Javě bez Androidu a nejsou součástí APK:

```bash
cd kiosk-app
T=$(mktemp -d)
javac -d "$T" src/app/plainsignage/kiosk/Allowlist.java test/AllowlistTest.java && java -cp "$T" app.plainsignage.kiosk.AllowlistTest
# stejně pro WatchdogTest, UpdatePolicyTest (+ UpdatePolicy.java) a HealthTest (+ Health.java)
```

## Příprava boxu

Postup je pro oba boxy stejný. Xiaomi stick je 32bitový s 1 GB RAM (po bootu
zhruba 250 MB volných, po odstranění balastu 420 MB) a potřebuje novější WebView
z Play. Mecool má 2 GB RAM, ethernetový port a vendor OTA balíček, který je
mrtvý aktualizační kanál. Hledej Android 9 nebo novější s Google Play, aspoň
1 GB RAM, síťové ADB a ovladač s tlačítky OK a BACK.

Zapni ADB (Nastavení, Předvolby zařízení, Informace, vyber 7x Sestavení, pak
Možnosti pro vývojáře, Ladění USB), box i počítač musí být ve stejné LAN:

```bash
adb connect <box-ip>:5555        # potvrď otisk klíče na TV ("Vždy povolit")
adb devices                      # <box-ip>:5555   device
```

Na Mecoolu síťové ADB po restartu zmizí, takže celou instalaci udělej v jedné
relaci; na Xiaomi sticku port 5555 restart přežije. Záložní cesta je kabel USB-A
na USB-A do OTG portu, ale obyčejný A-A kabel spojí 5V větve obou zařízení.

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

Nainstaluj kiosk a nastav ho jako launcher:

```bash
adb -s <box-ip>:5555 install -r kiosk-app/build/signage-kiosk.apk
adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk SYSTEM_ALERT_WINDOW allow
adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk REQUEST_INSTALL_PACKAGES allow
adb -s <box-ip>:5555 shell cmd package set-home-activity app.plainsignage.kiosk/.MainActivity
adb -s <box-ip>:5555 shell pm disable-user --user 0 com.google.android.tvlauncher
```

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
- Volitelný vzdálený přístup: VPN klient jako Tailscale spouštěný při bootu (`settings put secure always_on_vpn_app com.tailscale.ipn`, `always_on_vpn_lockdown 0`) s ACL, která boxu nedovolí nic dosáhnout. Živý náhled: `scrcpy -s <vpn-ip>:5555 --max-size 1280`. Počítej asi s 40 MB RAM na VPN aplikaci.
- Po dokončení práce vypni ladění USB a z jiného stroje ověř, že port 5555 je zavřený; ADB bez ověření na 5555 se aktivně zneužívá. Soubory s výchozím stavem si nech k pozdějšímu porovnání.
- Device owner s lock task se nedoporučuje: vyžaduje tovární reset bez jakéhokoli účtu, čímž se ztratí Play a s ním aktualizace WebView.
- Návrat: `pm enable com.google.android.tvlauncher` (a další vypnuté balíčky), `uninstall app.plainsignage.kiosk`, vrácení nastavení výše.

## První spuštění a párování

```bash
adb -s <box-ip>:5555 shell appops get app.plainsignage.kiosk SYSTEM_ALERT_WINDOW     # očekávej: allow
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity
```

Bez uložené adresy aplikace otevře `<KIOSK_BASE_URL>/connect`, kde je
šestiznakový kód platný 10 minut a QR kód do adminu
(`https://<host>/admin?pair=<CODE>`). Otevři obrazovku, ke které box patří, a
klikni na **Připojit TV**. Při dalším pollu box přejde na `/tv/<screen>`, adresu
uloží a po bootu na ní startuje. Bez párování spusť s adresou; aplikace přijme
jen `https://` a přesně svůj host:

```bash
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity \
  -d "https://<host>/tv/<screen>"
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
