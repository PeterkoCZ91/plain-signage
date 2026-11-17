# Contributing

Issues and pull requests are welcome. Keep changes small and add a test where it
makes sense. Before opening a pull request run the checks listed in the
[README](README.md#tests), from the repository root:

- `(cd deploy/app && python3 -m pytest -q)` and `node --test deploy/app/tv/test/*.test.mjs`
- `(cd deploy/app && ruff check .)`
- `shellcheck -S warning deploy/*.sh deploy/tools/*.sh kiosk-app/build.sh wifi-app/build.sh`
- `bash deploy/test-backup-restore.sh`
- the kiosk Java tests when you touch `kiosk-app/` ([docs/en/kiosk.md](docs/en/kiosk.md#build))
- the E2E tests when you touch the admin or the player (`deploy/app/e2e/README.md`)

Do not commit secrets, real hostnames, personal data or absolute local paths.
Write new code, identifiers, comments and commit messages in English. Czech is
used for UI strings, API error texts (`errors.py`), some existing test data and
`docs/cs`; keep the English and Czech docs in step. Contributions are licensed
under the MIT License.
