# Android kiosk app (`app.plainsignage.kiosk`)

Full documentation: [docs/en/kiosk.md](../docs/en/kiosk.md) (English),
[docs/cs/kiosk.md](../docs/cs/kiosk.md) (Česky), including box preparation.

```bash
KIOSK_BASE_URL=https://<host> ./build.sh                       # build/signage-kiosk.apk
KIOSK_ADB=<box-ip>:5555 KIOSK_BASE_URL=https://<host> ./build.sh install
```
