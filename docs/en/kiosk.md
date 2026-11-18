# Kiosk app, Android box and Wi-Fi helper

[Česky](../cs/kiosk.md)

The kiosk is a minimal launcher with one full-screen WebView for an Android
playback box (package `app.plainsignage.kiosk`, version 1.0, `versionCode` 1). It
was built and tested on a Mecool KM9 Pro (Android 10) and a Xiaomi Mi TV Stick
(Android 9). Every command below runs over ADB against `<box-ip>:5555`.

The apps carry the package names `app.plainsignage.kiosk` and `app.plainsignage.wifi`. A box that runs an earlier private build under the old package name needs an uninstall and a fresh install, because Android treats a different package as a different app.

Why not just a browser: after a power cut browsers do not start (no
`BOOT_COMPLETED`, and Android 10 forbids starting an activity from the
background). The app declares `category.HOME`, so the system starts it as part
of boot. It also has no browser chrome, plays sound without a tap
(`setMediaPlaybackRequiresUserGesture(false)`), keeps the screen on, and
retries a failed load every 5 s growing to 60 s.

## Build

No gradle and no dependencies: `aapt2 -> javac -> d8 -> zipalign -> apksigner`,
see `kiosk-app/build.sh`. You need the Android SDK (platform `android-34`,
override with `API=`) and `aapt2 javac d8 zipalign apksigner keytool` on `PATH`.
The SDK is looked up in `ANDROID_HOME`, `ANDROID_SDK_ROOT`, then `~/Android/Sdk`.
Use JDK 17: `d8` currently crashes under JDK 21.

```bash
cd kiosk-app
KIOSK_BASE_URL=https://<host> ./build.sh                                   # build/signage-kiosk.apk
KIOSK_ADB=<box-ip>:5555 KIOSK_BASE_URL=https://<host> ./build.sh install   # build and install over ADB
```

- `KIOSK_BASE_URL` is required and must be `https://`; there is no default, so an
  APK is never built against a wrong server by accident. The allowed host is
  derived from it.
- The signing key `kiosk-app/kiosk.keystore` is created on first build and stays
  local (in `.gitignore`). Back it up: a box refuses an update signed with another
  key, and the only way out is `adb uninstall app.plainsignage.kiosk`. The
  password is generated on the first build into `kiosk.keystore.pass` (or taken
  from `KIOSK_KS_PASS`): back it up together with the keystore.
- To be accepted as an update, raise `versionCode` and `versionName` in
  `kiosk-app/AndroidManifest.xml`.

Tests are plain Java without Android and are not part of the APK:

```bash
cd kiosk-app
T=$(mktemp -d)
javac -d "$T" src/app/plainsignage/kiosk/Allowlist.java test/AllowlistTest.java && java -cp "$T" app.plainsignage.kiosk.AllowlistTest
# the same pattern for WatchdogTest, UpdatePolicyTest (+ UpdatePolicy.java) and HealthTest (+ Health.java)
```

## Preparing a box

The steps are the same for both boxes. The Xiaomi stick is 32-bit with 1 GB RAM
(about 250 MB free after boot, 420 MB after debloating) and needs a newer
WebView from Play. The Mecool has 2 GB RAM, an Ethernet port, and a vendor OTA
package that is a dead update channel. Look for Android 9 or newer with Google
Play, at least 1 GB RAM, network ADB and a remote with OK and BACK.

Enable ADB (Settings, Device Preferences, About, select Build 7 times, then
Developer options, USB debugging) with the box and computer on the same LAN:

```bash
adb connect <box-ip>:5555        # confirm the key fingerprint on the TV ("Always allow")
adb devices                      # <box-ip>:5555   device
```

On the Mecool network ADB disappears after a restart, so do the whole install in
one session; on the Xiaomi stick port 5555 survives. A USB-A to USB-A cable into
the OTG port is the fallback, but a plain A-A cable joins the 5 V rails of both
devices.

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

Install the kiosk and make it the launcher:

```bash
adb -s <box-ip>:5555 install -r kiosk-app/build/signage-kiosk.apk
adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk SYSTEM_ALERT_WINDOW allow
adb -s <box-ip>:5555 shell appops set app.plainsignage.kiosk REQUEST_INSTALL_PACKAGES allow
adb -s <box-ip>:5555 shell cmd package set-home-activity app.plainsignage.kiosk/.MainActivity
adb -s <box-ip>:5555 shell pm disable-user --user 0 com.google.android.tvlauncher
```

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
- Revert: `pm enable com.google.android.tvlauncher` (and other disabled packages), `uninstall app.plainsignage.kiosk`, restore the settings above.

## First run and pairing

```bash
adb -s <box-ip>:5555 shell appops get app.plainsignage.kiosk SYSTEM_ALERT_WINDOW     # expect: allow
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity
```

Without a stored address the app opens `<KIOSK_BASE_URL>/connect`, which shows a
six-character code valid for 10 minutes and a QR code to the admin
(`https://<host>/admin?pair=<CODE>`). Open the screen the box belongs to and
click **Connect TV**. On its next poll the box moves to `/tv/<screen>`, stores
that address and starts on it after boot. Without pairing, start with the
address; the app accepts only `https://` and exactly its own host:

```bash
adb -s <box-ip>:5555 shell am start -n app.plainsignage.kiosk/.MainActivity \
  -d "https://<host>/tv/<screen>"
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
4. Connect through the VPN address: `adb connect <vpn-ip>:5555`, live view with `scrcpy -s <vpn-ip>:5555 --max-size 1280`.

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
