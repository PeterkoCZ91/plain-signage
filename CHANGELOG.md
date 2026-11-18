# Changelog

## 1.15.0

- Kiosk app (1.15, `versionCode` 16): when the main page cannot be loaded, a status screen in the style of the update prompt now covers the WebView. It shows the reason (certificate or SSL, server not responding or timed out, no network or name not resolved, HTTP 5xx), the server host, the box's IP address and a countdown to the next attempt, and disappears when the page loads. Certificate errors (reported by WebView through `onReceivedSslError`) are shown too, with the specific reason and a hint to check the box clock; the request is always cancelled, an invalid certificate is never accepted, and the page is retried. Texts in English and Czech.
- Kiosk app: one log line at start (`SignageKiosk` tag) with the version, the server host and whether a screen is saved (only its `/tv/<name>` path, never the full address).
- Kiosk app: `adb shell am start -n app.plainsignage.kiosk/.MainActivity --ez diag true` shows a diagnostics overview for 15 s (version, server host, saved screen, last error, RAM, network and Wi-Fi signal, renderer crashes, time of the last loaded page).
- Tests: `LoadErrorTest` and `DiagTest` (plain Java), added to the `kiosk-java` CI job.

## 1.14.0

First public release.

- Server: one Python process (standard library only), JSON API with stable error codes, English and Czech texts, optional `segno` for QR codes and `ffmpeg` for automatic video conversion.
- Web admin in English and Czech: screens, pairing by QR and six-character code, uploads from a phone, ordering, per-item schedule, per-screen branding and QR targets.
- Player for TV browsers: playlist loop, offline cache through a service worker, transitions, portrait and fill modes, idle screen with QR cards.
- Android kiosk app (1.14, `versionCode` 15): start after reboot, watchdog, self-update from your server, box health reporting.
- Wi-Fi helper app (1.0, `versionCode` 1) for setting up the network on a box.
- Monitoring: online state, blank-screen detection, optional Telegram alerts (`[CRITICAL]`, `[WARNING]`, `[OK]`).
- Deployment: Docker Compose, deploy, backup and restore scripts, smoke test.
