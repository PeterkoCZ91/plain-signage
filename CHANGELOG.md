# Changelog

## 1.14.0

First public release.

- Server: one Python process (standard library only), JSON API with stable error codes, English and Czech texts, optional `segno` for QR codes and `ffmpeg` for automatic video conversion.
- Web admin in English and Czech: screens, pairing by QR and six-character code, uploads from a phone, ordering, per-item schedule, per-screen branding and QR targets.
- Player for TV browsers: playlist loop, offline cache through a service worker, transitions, portrait and fill modes, idle screen with QR cards.
- Android kiosk app (1.14, `versionCode` 15): start after reboot, watchdog, self-update from your server, box health reporting.
- Wi-Fi helper app (1.0, `versionCode` 1) for setting up the network on a box.
- Monitoring: online state, blank-screen detection, optional Telegram alerts (`[CRITICAL]`, `[WARNING]`, `[OK]`).
- Deployment: Docker Compose, deploy, backup and restore scripts, smoke test.
