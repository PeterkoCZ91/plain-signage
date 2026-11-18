# Kiosk app, Android box and Wi-Fi helper

[Česky](../cs/kiosk.md)

The kiosk is a minimal launcher with one full-screen WebView for an Android
playback box (package `app.plainsignage.kiosk`, version 1.0, `versionCode` 1). It
was built and tested on a Mecool KM9 Pro (Android 10) and a Xiaomi Mi TV Stick
(Android 9). Every command below runs over ADB against `<box-ip>:5555`.

## Before you start

- A running server reachable over HTTPS at `<host>`, with a certificate that Android trusts. The Compose quick start serves plain HTTP on port 8080, which the kiosk cannot use (see [Build](#build)); TLS is set up in front of the server as described in [Production deployment](install.md#production-deployment).
- An Android box or stick (Android 9 or newer) on the same LAN as your computer for the install.
- A computer with JDK 17, the Android SDK (platform `android-34`, build-tools, platform-tools for `adb`) and `zip`. No prebuilt APK is published: the repository has no release that carries one, and the server address is compiled into the app, so you build your own APK once per server.
- ADB (Android Debug Bridge) enabled on the box, see [Preparing a box](#preparing-a-box). Optional: [scrcpy](#remote-access-optional) to see the box's screen on your computer.

Order of work: build the APK, prepare the box, install the APK, pair the box, run the [Quick check](#quick-check).

The apps carry the package names `app.plainsignage.kiosk` and `app.plainsignage.wifi`. A box that runs an earlier private build under the old package name needs an uninstall and a fresh install, because Android treats a different package as a different app.

Why not just a browser: after a power cut browsers do not start (no
`BOOT_COMPLETED`, and Android 10 forbids starting an activity from the
background). The app declares `category.HOME`, so the system starts it as part
of boot. It also has no browser chrome, plays sound without a tap
(`setMediaPlaybackRequiresUserGesture(false)`), keeps the screen on, and
retries a failed load every 5 s growing to 60 s.

## Build

No gradle and no dependencies: `aapt2 -> javac -> d8 -> zipalign -> apksigner`,
see `kiosk-app/build.sh`. You need JDK 17 (it provides `javac` and `keytool`),
`zip`, and the Android SDK with platform `android-34` (override with `API=`),
a build-tools package (`aapt2`, `d8`, `zipalign`, `apksigner`) and
platform-tools (`adb`, needed to install). The SDK is looked up in
`ANDROID_HOME`, `ANDROID_SDK_ROOT`, then `~/Android/Sdk`. Use JDK 17: `d8`
currently crashes under JDK 21.

The script stops if `aapt2 javac d8 zipalign apksigner keytool` is not on
`PATH`, but it does not add the SDK's `build-tools/<version>` directory to
`PATH` for you, and it does not check for `zip`, which it calls in step 4. Put
the tools on `PATH` first, for example (the SDK's `sdkmanager` installs the
packages; `<version>` is the installed build-tools version, such as `34.0.0`):

```bash
export ANDROID_HOME=~/Android/Sdk
export PATH="$ANDROID_HOME/build-tools/<version>:$ANDROID_HOME/platform-tools:$PATH"
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" "platforms;android-34" "build-tools;<version>" "platform-tools"
```

There is no prebuilt APK to download. The service address is compiled into the
APK and the app refuses every other host, so an APK belongs to one server; a new
address needs a new build.

```bash
cd kiosk-app
KIOSK_BASE_URL=https://<host> ./build.sh                                   # build/signage-kiosk.apk
KIOSK_ADB=<box-ip>:5555 KIOSK_BASE_URL=https://<host> ./build.sh install   # build and install over ADB
```

- `KIOSK_BASE_URL` is required and must be `https://`; there is no default, so an
  APK is never built against a wrong server by accident. The allowed host and
  port are derived from it (for example `https://signage.example.com`). Plain
  HTTP is not supported at all: the manifest disables cleartext traffic and the
  address allowlist rejects `http://`. The app does not override certificate
  errors, so Android's default applies: a self-signed or expired certificate, or
  a certificate for another name, means the page does not load and the box keeps
  retrying. Use a certificate from a public CA (for example Let's Encrypt) for
  that exact host, and check that the box's date and time are right.
- `build.sh install` only runs `adb connect` and `adb install -r`. The
  permissions and launcher steps in [Preparing a box](#preparing-a-box) are still
  needed.
- The signing key `kiosk-app/kiosk.keystore` is created on first build and stays
  local (in `.gitignore`). Back it up: a box refuses an update signed with another
  key, and the only way out is `adb uninstall app.plainsignage.kiosk`. The
  password is generated on the first build into `kiosk.keystore.pass` (or taken
  from `KIOSK_KS_PASS`): back it up together with the keystore. If the keystore
  or its password is lost there is no recovery: delete both files, build with a
  new key, and on every box run `adb uninstall app.plainsignage.kiosk`, install
  again, repeat the `appops` commands and pair the box again. Updates from the
  server do not work across keys.
- To be accepted as an update, raise `versionCode` and `versionName` in
  `kiosk-app/AndroidManifest.xml`.

Tests are plain Java without Android and are not part of the APK:

```bash
cd kiosk-app
T=$(mktemp -d)
javac -d "$T" src/app/plainsignage/kiosk/Allowlist.java test/AllowlistTest.java && java -cp "$T" app.plainsignage.kiosk.AllowlistTest
# the same pattern for WatchdogTest, UpdatePolicyTest (+ UpdatePolicy.java), HealthTest (+ Health.java),
# LoadErrorTest (+ LoadError.java) and DiagTest (+ Diag.java)
```

### Trying it at home without a domain

The kiosk needs `https://` with a certificate from a public CA, so a trial in a
home network without your own domain is awkward. For a trial only, you can use a
wildcard DNS service that publishes a certificate for download:
`local-ip.sh` resolves a host such as `192-168-1-10.local-ip.sh` to the LAN
address `192.168.1.10`, and the certificate and key offered on the
[local-ip.sh](https://local-ip.sh) page are valid for that name. Put them in
nginx in front of the server (the certificate paths are placeholders):

```nginx
server {
    listen 9443 ssl;
    server_name 192-168-1-10.local-ip.sh;
    ssl_certificate     /etc/nginx/local-ip/server.pem;
    ssl_certificate_key /etc/nginx/local-ip/server.key;
    client_max_body_size 110m;
    location / {
        proxy_pass http://<server-ip>:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

Then set `SIGNAGE_PUBLIC_URL=https://192-168-1-10.local-ip.sh:9443` on the
server and build the app with `KIOSK_BASE_URL=https://192-168-1-10.local-ip.sh:9443`
(same host and port in both). This was verified on a Philips Android TV.

The private key of that certificate is public, so anyone can impersonate the
host. Use it only for a trial with demo data and never for real operation. For
real operation use your own domain and Let's Encrypt, see
[Production deployment](install.md#production-deployment).

## Preparing a box

The steps are the same for both boxes. The Xiaomi stick is 32-bit with 1 GB RAM
(about 250 MB free after boot, 420 MB after debloating) and needs a newer
WebView from Play. The Mecool has 2 GB RAM, an Ethernet port, and a vendor OTA
package that is a dead update channel. Look for Android 9 or newer with Google
Play, at least 1 GB RAM, network ADB and a remote with OK and BACK.

Find `<box-ip>` in the box's network settings (details of the Wi-Fi or Ethernet
connection) or in your router's list of clients; a fixed lease in the router
keeps it from changing.

Enable ADB (Settings, Device Preferences, About, select Build 7 times, then
Developer options, USB debugging) with the box and computer on the same LAN.
Network ADB on port 5555 exists only if the box offers it: some boxes show
only USB debugging, and Android 11 and newer have Wireless debugging with its
own port and `adb pair` (not tested here). If `adb connect` is refused, look for
a network ADB option in Developer options.

```bash
adb connect <box-ip>:5555        # confirm the key fingerprint on the TV ("Always allow")
adb devices                      # <box-ip>:5555   device
```

On the Mecool network ADB disappears after a restart, so do the whole install in
one session; on the Xiaomi stick port 5555 survives. If it disappears, switch
USB debugging off and on in Developer options and run `adb connect` again (not
checked on every box). The installed kiosk runs without ADB; ADB is needed only
for setup and maintenance. A USB-A to USB-A cable into the OTG (USB host) port is
the fallback, but a plain A-A cable joins the 5 V rails of both devices.

Disable vendor telemetry, store, assistant, screensaver and updater packages
(reversible), after taking a baseline:

```bash
adb shell pm list packages > packages-before.txt
adb shell getprop > getprop-before.txt
adb shell pm disable-user --user 0 <package>      # undo: adb shell pm enable <package>
```

Keep Google Play: it is the only thing that updates WebView. Once WebView is
current you may disable Play (`pm disable-user --user 0 com.android.vending`)
to save RAM and avoid surprises, at the price that WebView is then updated only
by hand (re-enable Play, update, disable it again).

Package state is
written to disk with a delay, so wait about 40 seconds after changing packages
before `adb reboot`, or the change can be lost. On the Mecool,
`pm disable-user --user 0 com.droidlogic.vsota` removes the dead OTA channel.

WebView: the stock WebView 81 on the Xiaomi stick left the player black (CSS
`inset` needs Chrome 87). Sign in to Play with a dedicated account and update
"Android System WebView" (138 was installed there; the Mecool had 150).

Install the kiosk and make it the launcher (run from the repository root after
the [build](#build); if you used `build.sh install`, the `install` line is already
done):

```bash
adb -s <box-ip>:5555 install -r kiosk-app/build/signage-kiosk.apk
adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk SYSTEM_ALERT_WINDOW allow
adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk REQUEST_INSTALL_PACKAGES allow
adb -s <box-ip>:5555 shell cmd package set-home-activity app.plainsignage.kiosk/.MainActivity
adb -s <box-ip>:5555 shell pm disable-user --user 0 com.google.android.tvlauncher
```

On a box with Google Play, Play Protect may open a dialog on the TV during `adb install` and the command waits until someone answers it with the remote. Choose the option that installs the app anyway (it is under the more-details link; the wording differs by version). If it is rejected, `adb install` fails with `INSTALL_FAILED_VERIFICATION_FAILURE`; run it again. Tested on a Philips Android 12 TV, where the dialog appeared on the first install.

The dialog comes from Google Play (`com.android.vending`) and appears because the app is not from the Play Store and has no reputation there. Publishing the APK elsewhere does not change that, and every build is a different file anyway because the server address is compiled in. Ways to avoid answering it:

- Answer it once per install. This is the only way tested here.
- Turn off app scanning in the Play Store settings on the box (Play Protect, scan apps). Not tested here; the menu wording differs between versions.
- Use a box without Google Play, or one where Play is disabled (`adb shell pm disable-user --user 0 com.android.vending`). Then nothing asks. Not tested here, and without Play the system WebView is not updated automatically, see [Preparing a box](#preparing-a-box).

`set-home-activity` reports `Success` but does nothing on its own (the system
launcher has priority 2, the kiosk 0); disabling `com.google.android.tvlauncher`
is what makes HOME resolve to the kiosk. After `adb reboot` the kiosk starts
alone: about 60 to 77 s to the first report on the Mecool, about 33 s to the
foreground on the Xiaomi. Undo with `pm enable com.google.android.tvlauncher`.
With the system launcher disabled, leaving the kiosk (BACK twice) leads to an
app list and system settings, so Wi-Fi is always reachable.

Keep the box awake. Never use `screen_off_timeout -1`: the system raises it to
about 10 seconds and the screen turns off at once.

```bash
adb -s <box-ip>:5555 shell settings put secure sleep_timeout -1
adb -s <box-ip>:5555 shell settings put system screen_off_timeout 2147483647
adb -s <box-ip>:5555 shell settings put secure screensaver_enabled 0
adb -s <box-ip>:5555 shell settings put secure screensaver_activate_on_sleep 0
adb -s <box-ip>:5555 shell settings put secure screensaver_activate_on_dock 0
adb -s <box-ip>:5555 shell settings put global stay_on_while_plugged_in 7
```

Original Mecool values for reverting: `secure sleep_timeout 10800000`,
`system screen_off_timeout 900000`, `secure screensaver_enabled 1`,
`secure screensaver_activate_on_sleep 1`. HDMI-CEC is off from the factory on the
Xiaomi stick, so turning the TV off does not put it to sleep.

Network and hygiene:

- Prefer Ethernet when there is a port. Put the box on its own VLAN or guest SSID with egress only to the signage server; the player needs nothing from the LAN.
- Optional remote access over a VPN is described in [Remote access](#remote-access-optional).
- When the work is done, turn USB debugging off and check from another host that port 5555 is closed; unauthenticated ADB on 5555 is actively abused. Keep the baseline files to compare against later.
- Device owner plus lock task is not recommended: it needs a factory reset without any account, which loses Play and with it WebView updates.
- Revert: `pm enable com.google.android.tvlauncher` and every other package you disabled (`adb shell pm list packages -d` lists the disabled ones; `packages-before.txt` is your baseline), `adb uninstall app.plainsignage.kiosk`, and put back the settings changed above (the Mecool's original values are listed there; the steps do not save the original values of other boxes, so read them with `adb shell settings get <namespace> <key>` before you change them). A factory reset in the box's own Settings is the last resort; it removes everything, including the WebView update.

## First run and pairing

```bash
adb -s <box-ip>:5555 shell appops get app.plainsignage.kiosk SYSTEM_ALERT_WINDOW     # expect: allow
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity
```

Without a stored address the app opens `<KIOSK_BASE_URL>/connect`. The TV shows
the heading "Connect this TV in the admin panel", a QR code, a six-character
code and "The code is valid for 10 min." (in Czech or English, with CS and EN
buttons). If the page does not load, the box retries every 5 s growing to 60 s;
see the Quick check for causes. To pair:

1. Scan the QR code with a phone. It opens `https://<host>/admin?pair=<CODE>`;
   log in with the admin credentials. If you cannot scan, type that address by
   hand: the admin has no field for entering the code.
2. The admin shows a green bar "Connect a new TV with code <CODE> to screen
   <name>". The screen is the one last used in that browser; to choose another,
   open `https://<host>/admin?pair=<CODE>&device=<screen>`.
3. Click **Connect TV**. On its next poll the box moves to `/tv/<screen>`, stores
   that address and starts on it after boot.

The QR code is built from the request headers or from `SIGNAGE_PUBLIC_URL`; if it
points to `http://` or a wrong host, set `SIGNAGE_PUBLIC_URL=https://<host>` on
the server. An expired code is replaced by a new one when the app is started
again (`am start` above). Without pairing, start with the address; the app
accepts only `https://` and exactly its own host:

```bash
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity \
  -d "https://<host>/tv/<screen>"
```

## Quick check

1. Server: `curl -s https://<host>/api/health` from your computer returns `"ok": true` without `-k`. If curl complains about the certificate, the box will too.
2. Install: `adb -s <box-ip>:5555 shell pm list packages | grep app.plainsignage` lists `app.plainsignage.kiosk`.
3. Picture: after pairing the TV plays the screen's playlist. In the admin the screen shows as online within about 45 s, with the kiosk version.
4. Logs, if not: `adb -s <box-ip>:5555 logcat -d -s SignageKiosk` (rejected addresses and their reason). A blank or retrying screen is usually a certificate the box does not trust, a wrong `KIOSK_BASE_URL`, no route from the box to `<host>`, or a wrong date on the box.
5. Boot: `adb -s <box-ip>:5555 reboot`; the kiosk should return to the content on its own after about one to two minutes. A cold start after unplugging power is not verified (see the end of this page).

When done, turn USB debugging off (see Network and hygiene above).

### Status screen and diagnostics

When the main page cannot be loaded, the kiosk covers the WebView with a status screen (same style as the update prompt): the reason (certificate or SSL, server not responding or timed out, no network or name not resolved, HTTP 5xx), the server host, the box's IP address and a countdown to the next attempt (5, 10, 20, 40, then 60 s). It disappears when the page loads. The kiosk never accepts an untrusted certificate: the request is always cancelled and the screen only tells you why and keeps retrying. Certificate reasons: expired, not valid yet, issued for a different host, untrusted authority, invalid date. Most of them also say to check the date and time on the box, because a wrong clock (typical after a power outage on a box without a battery clock) makes a valid certificate look expired or untrusted.

At start the kiosk writes one line to the log (`adb logcat -d -s SignageKiosk`): version, server host and whether a screen is saved (only its `/tv/<name>` path).

For a technician with ADB, an overview for 15 s (version, server host, saved screen, last error, RAM, network and Wi-Fi signal, renderer crashes, age of the last loaded page):

```bash
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity --ez diag true
```

## Operation

- Leave the kiosk without ADB: press BACK twice within two seconds. The exit leads to the system launcher if enabled, otherwise to an app list, and finally to system settings.
- Diagnostics (last error, version, state): the MENU or INFO button on the remote. The admin shows the kiosk version per screen (`kiosk` in `GET /api/devices`).
- Address safety: `am start -d` and WebView navigation accept only `https://`, exactly the host and port of `KIOSK_BASE_URL`, and a path of `/`, `/connect` or `/tv/<something>` (no `..`, `//`, `\`, `%2e`, user-info). Anything else is ignored, including a stored address; the reason is logged under `SignageKiosk`. Sub-requests are not affected. Cleartext traffic and app backup are disabled.
- Watchdog: every 5 s the app checks it is in the foreground and after 20 s without it calls `startActivity` with `REORDER_TO_FRONT`. Double BACK pauses it for 10 minutes. Android 10 blocks background activity starts ("isBgStartWhitelisted: false" in the log), so it works only with `SYSTEM_ALERT_WINDOW`, which survives `install -r` but is lost after `pm clear` and `adb uninstall`. Without it a refused attempt is only logged every 20 s.
- Crashes: an uncaught exception restarts the kiosk, but not sooner than 30 s after the previous restart (delayed via `AlarmManager`).

## Updating the kiosk from the server

The app asks `GET /api/kiosk/release` once a minute. It updates
only when an admin requests it for the screen, or once after the box powers on
(within 10 minutes of boot).

Prerequisite: the box already runs a kiosk installed over ADB; the server only updates an existing one. The publish script works over SSH on the host layout from [Production deployment](install.md#production-deployment) (`$SIGNAGE_REMOTE_ROOT/data/kiosk`, in the container `/data/kiosk`, the same as `SIGNAGE_KIOSK_DIR`); for a Compose setup the script does not apply, and the files it would write are shown by `--dry-run`.

1. Build the new APK with a higher `versionCode` and the same keystore.
2. Publish it (verifies the APK, writes `signage-kiosk.apk` and `kiosk.json`, never lowers the version without `--force`; uses `SIGNAGE_SSH_ALIAS` and `SIGNAGE_REMOTE_ROOT` like `deploy.sh`):

   ```bash
   ./deploy/tools/publish-kiosk.sh --dry-run          # only verify, print kiosk.json and the target
   ./deploy/tools/publish-kiosk.sh                    # kiosk-app/build/signage-kiosk.apk
   ```

3. In the admin, in the screen's availability section, click **Update kiosk**.
4. The box downloads the APK, verifies size, SHA-256, package and a higher `versionCode`, and shows its own prompt ("New version <x> is ready", "Press OK on the remote"). OK opens the system install dialog (one OK on the remote; silent install is impossible on Android 9/10 without a device owner); Back means not now (`declined`). It waits 5 minutes, then returns to content and retries only on a new request.
5. After the update a `MY_PACKAGE_REPLACED` receiver starts the kiosk again.

Once per box: `adb shell appops set app.plainsignage.kiosk REQUEST_INSTALL_PACKAGES allow`.
Decision logic is in `UpdatePolicy.java`.

## Box health

The same poll carries free and total RAM, a low-memory flag, uptime, network type
and Wi-Fi signal in dBm, and WebView renderer crashes since the process started.
The SSID is never read or sent. The admin shows these per screen and Telegram can
alert on persistently low free RAM. Fields and thresholds: [api.md](api.md#box-health).
Logic is in `Health.java`.

## Remote access (optional)

Nothing in this section is required. The server, the player and the kiosk app work without any VPN; the kiosk only needs to reach your signage server.

The first line of remote care is built in: the kiosk reports RAM, Wi-Fi signal, crashes and the last restart to the server, and the admin shows them per screen (see [Box health](#box-health)). Reach for a VPN only when you need to look at the box itself, for example to read `logcat` or see the picture.

The box does not need to be reachable from the internet and should not be. Do not forward port 5555 on a router. Use a private network such as [Tailscale](https://tailscale.com) (any similar VPN works the same way):

1. Install the VPN app on the box and sign in. Tag the box (for example `tag:signage-box`) so that access is decided by an ACL and not by whose account it is.
2. Let the box accept connections but start none. With a deny-by-default ACL this means it appears only as a destination:

   ```json
   {
     "tagOwners": { "tag:signage-box": ["autogroup:admin"] },
     "acls": [
       { "action": "accept", "src": ["autogroup:admin"], "dst": ["tag:signage-box:5555"] }
     ]
   }
   ```

3. Start the app at boot: `adb shell settings put secure always_on_vpn_app com.tailscale.ipn` and `adb shell settings put secure always_on_vpn_lockdown 0`. Lockdown stays off, so a dead VPN does not cut the box off from the signage server.
4. Connect through the VPN address: `adb connect <vpn-ip>:5555`, live view with `scrcpy -s <vpn-ip>:5555 --max-size 1280` (scrcpy is a separate tool for your computer; install it from your package manager or its project page).

Expect about 40 MB of RAM for the VPN app, which matters on a 1 GB box. This procedure is not part of the automated tests. When you are done, turn USB debugging off if you do not need it and check from another host that port 5555 is closed on the box's normal address: unauthenticated ADB on 5555 is actively abused.

## Wi-Fi helper

`wifi-app/` (`app.plainsignage.wifi`) is a tiny app without a UI that adds, lists or
removes a saved Wi-Fi network over ADB, so also remotely over a VPN. It exists
because the ADB shell on Android 9/10 cannot add Wi-Fi (`cmd wifi add-network`
exists only from Android 11) and `WifiManager.addNetwork` on Android 10 works
only for an app with `targetSdk` 28 or lower, while the kiosk targets 29. Main
use: a service hotspot saved on every box in advance, so when the site's Wi-Fi
changes you turn on a phone hotspot, the box joins it and you configure the rest
remotely. The box joins the strongest known network in range.

```bash
cd wifi-app
./build.sh                                   # build/signage-wifi.apk
KIOSK_ADB=<box-ip>:5555 ./build.sh install   # build, install and grant location (needed for the list on Android 10)
```

Same SDK requirements as the kiosk; the key `wifi-app/wifi.keystore` is created
on first build and stays local. Its password is generated into `wifi.keystore.pass`
(chmod 600) or taken from `WIFI_KS_PASS`; if the keystore exists and neither is available, the build stops. Back both up. On Android 10 Play Protect rejects a
`targetSdk` 28 app installed over ADB (`INSTALL_FAILED_VERIFICATION_FAILURE`), so
turn the verification off only for the install:

```bash
adb shell settings put global verifier_verify_adb_installs 0
adb install -r build/signage-wifi.apk
adb shell settings delete global verifier_verify_adb_installs
```

Intent extras: `action`, `ssid`, `password`, `hidden`; actions `add`, `list`, `remove`.

```bash
adb shell 'am start -n app.plainsignage.wifi/.Wifi --es action add --es ssid "<network>" --es password "<password>"'
#   without --es password = open network, --ez hidden true = hidden network
adb shell 'am start -n app.plainsignage.wifi/.Wifi --es action list'
adb shell 'am start -n app.plainsignage.wifi/.Wifi --es action remove --es ssid "<network>"'
adb shell 'logcat -d -s SignageWifi'        # result (OK:/ERROR:), the password is never logged
```

An added network is only enabled; the box is not switched away from its current
one, and Android has no way to set priority. A network saved by hand in Settings
cannot be overwritten or removed by the app. A WPA password must be 8 to 63
characters. Only ADB can start the activity (it is protected by
`WRITE_SECURE_SETTINGS`). Keep real network names and passwords in a private,
untracked file.

## Verified and not verified

Verified on a box: the app starts, the player plays with zero render errors at
1920x1080, sound plays without a tap, and after `adb reboot` the kiosk starts as
HOME and reports to the server in about 60 s; network ADB survived the restart on
the test box. Not verified: a cold start after unplugging power (test it
physically, including HDMI-CEC, before leaving a box unattended), a TV power
cycle, and the watchdog during a real network outage.

In November 2026 the procedure was also verified on a clean Philips Android TV
(Android 12): build following this documentation, installation over `adb` with
the Play Protect dialog, launch, loading of the pairing page, pairing from the
admin, uploading an image and showing it, and uninstalling. On the same TV the
watchdog was verified: after pressing Home the kiosk came back to the foreground
by itself in about 20 s (log line `watchdog: kiosk is not in the foreground,
bringing it back`), with `SYSTEM_ALERT_WINDOW` granted. Still not verified: the
kiosk as the launcher (`set-home-activity` and disabling the system launcher), a
cold start, updates from the server and Wireless debugging.
