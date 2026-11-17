# E2E smoke tests (real browser)

The tests start `tvserver.py` as a subprocess (free port, state and media in `tmp_path`,
test credentials) and drive the admin, `/connect` and the player in Chromium via Playwright.

**Why they exist:** a `t is not a function` regression in the admin passed all unit and
HTTP tests and surfaced only as the message "Server not responding" (a
`catch` swallowed the exception). Every test therefore captures `pageerror` and
`console.error` and fails if there are any; after the admin loads it also requires an
empty `#msgList`. (Exception: a 404 on `/api/thumb/...`, where the admin deliberately
falls back to the original image.)

## Coverage (Czech and English via `?lang=`)

1. an empty screen without errors
2. upload of an image and a short video (ffmpeg, otherwise skipped) -> tile, thumbnail
3. `#transition` and `#fitMode` survive a reload
4. saving the brand and checking it in `/api/tv/<dev>/playlist`
5. "+ Screen" appears in the selector
6. the CS/EN switch does not change the number of tiles
7. moving with an arrow, hiding, deleting -> the API agrees
8. `/connect` and `/tv/<dev>` without JS errors
9. a 390 px viewport without horizontal overflow

## Running

```bash
pip install pytest playwright segno
playwright install chromium        # in CI: --with-deps
cd deploy/app && python3 -m pytest e2e -q
```

Without playwright installed the e2e tests are skipped (`importorskip`), so a plain
`pytest -q` in `deploy/app` keeps working. A full run takes about half a minute.

Note: the admin page carries a CSP (a `<meta>` tag in `admin.html`) without `unsafe-eval`, so in `wait_for_function`/`evaluate`
use the `() => ...` form, not a bare expression string.
