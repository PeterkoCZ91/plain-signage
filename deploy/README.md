# Deploy scripts

Full documentation: [install](../docs/en/install.md),
[troubleshooting and recovery](../docs/en/troubleshooting.md).
Czech: [install](../docs/cs/install.md), [troubleshooting and recovery](../docs/cs/troubleshooting.md).

| Script | Purpose |
|---|---|
| `deploy.sh` | Tests, canary container, switch, health/auth/playlist checks, rollback. `--dry-run`, `--skip-tests`, `--help` |
| `backup.sh` | Archive of the data directory, keeps the last 14 |
| `restore.sh` | Verified restore, moves current data aside; `--dry-run` |
| `smoke-test.sh` | Checks of a live service; pass `BASE`, `AUSER`, `APASS` |
| `test-backup-restore.sh` | Backup and restore round trip (also run in CI) |
| `convert-video.sh` | `./deploy/convert-video.sh INPUT [OUTPUT]`: H.264 MP4 for the web player |
| `tools/publish-kiosk.sh` | Publishes a kiosk APK release to the server (`--dry-run`, `--force`) |
| `tools/soak.py` | Manual load test of the server |
