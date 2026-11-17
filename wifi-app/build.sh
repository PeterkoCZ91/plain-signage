#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# build.sh — builds the Wi-Fi helper (app.plainsignage.wifi) without gradle, just like
# the kiosk: aapt2 -> javac -> d8 -> zipalign -> apksigner.
#
#   ./build.sh                 # builds build/signage-wifi.apk
#   ./build.sh install         # builds and installs via ADB (KIOSK_ADB=<ip>:5555)
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"

SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}
API=${API:-34}
ANDROID_JAR="$SDK/platforms/android-$API/android.jar"
MIN_API=21

if [[ ! -f "$ANDROID_JAR" ]]; then
  echo "Not found: $ANDROID_JAR — set ANDROID_HOME or API=<version>." >&2
  exit 1
fi
for tool in aapt2 javac d8 zipalign apksigner keytool; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done

rm -rf build
mkdir -p build/classes build/dex

echo "1/5  aapt2 — manifest"
aapt2 link -I "$ANDROID_JAR" --manifest AndroidManifest.xml \
  --min-sdk-version "$MIN_API" -o build/base.apk

echo "2/5  javac"
mapfile -d '' SOURCES < <(find src -name '*.java' -print0)
javac -nowarn -source 8 -target 8 \
  -bootclasspath "$ANDROID_JAR" -classpath "$ANDROID_JAR" \
  -d build/classes "${SOURCES[@]}"

echo "3/5  d8"
mapfile -d '' CLASSES < <(find build/classes -name '*.class' -print0)
d8 --lib "$ANDROID_JAR" --min-api "$MIN_API" --output build/dex "${CLASSES[@]}"

echo "4/5  zip + zipalign"
( cd build/dex && zip -q ../base.apk classes.dex )
zipalign -f 4 build/base.apk build/aligned.apk

echo "5/5  sign"
# The signing key is generated locally on the first build and stays local
# (*.keystore and *.keystore.pass are in .gitignore). If it is lost, the next
# install over an existing app first requires an `adb uninstall` of the old app.
# Password: ${WIFI_KS_PASS} if set; otherwise a random one is generated on the first
# build and stored next to the keystore in wifi.keystore.pass (chmod 600).
PASS_FILE=wifi.keystore.pass
if [[ -n "${WIFI_KS_PASS:-}" ]]; then
  KS_PASS=$WIFI_KS_PASS
elif [[ -f "$PASS_FILE" ]]; then
  KS_PASS=$(<"$PASS_FILE")
elif [[ -f wifi.keystore ]]; then
  echo "wifi.keystore exists but its password is unknown: set WIFI_KS_PASS or create $PASS_FILE." >&2
  exit 1
else
  KS_PASS=$(head -c 24 /dev/urandom | base64 | tr -d '/+=\n')
  ( umask 077; printf '%s\n' "$KS_PASS" > "$PASS_FILE" )
fi
if [[ ! -f wifi.keystore ]]; then
  echo "     (creating a new signing key wifi.keystore)"
  keytool -genkeypair -keystore wifi.keystore -storepass "$KS_PASS" \
    -keypass "$KS_PASS" -alias wifi -keyalg RSA -keysize 2048 \
    -validity 10000 -dname "CN=Signage Wifi" >/dev/null 2>&1
fi
export KS_PASS
apksigner sign --ks wifi.keystore --ks-pass env:KS_PASS \
  --key-pass env:KS_PASS --out build/signage-wifi.apk build/aligned.apk
rm -f build/base.apk build/aligned.apk build/signage-wifi.apk.idsig
ls -la build/signage-wifi.apk

if [[ "${1:-}" == "install" ]]; then
  target=${KIOSK_ADB:-}
  [[ -n "$target" ]] || { echo "Set KIOSK_ADB=<ip>:5555" >&2; exit 1; }
  adb connect "$target" >/dev/null || true
  adb -s "$target" install -r build/signage-wifi.apk
  adb -s "$target" shell pm grant app.plainsignage.wifi android.permission.ACCESS_FINE_LOCATION
fi
