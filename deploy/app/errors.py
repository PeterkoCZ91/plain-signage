"""API errors: stable code + cs/en text, response language selection."""
import re


# ---------- API errors: stable `code` + cs/en text ----------
# Single source of truth. `default` is a terse technical English text, ASCII only
# (sent to clients that did not ask for a language); `cs` is Czech with diacritics,
# formal/neutral wording (?lang=cs / Accept-Language: cs), `en` is the English
# text meant for end users.
# Variable parts are {parameters} (str.format). User data (labels, file names)
# is never translated. The cs/en texts are read by clients, not technicians: use
# the admin's terms (screen, Appearance and contacts), no internal field names,
# repo paths or commands. In cs/en the `field` parameter (branding field key) is
# also available as {field_label} - the human-readable field name as the client
# sees it in the admin.
BRAND_FIELD_LABELS = {
    "cs": {"name": "Název", "email": "E-mail", "phone": "Telefon", "city": "Město",
           "url": "Web / QR 1", "qrEmail": "E-mail pro QR 2", "pwaTitle": "Titulek aplikace"},
    "en": {"name": "Name", "email": "E-mail", "phone": "Phone", "city": "City",
           "url": "Website / QR 1", "qrEmail": "E-mail for QR 2", "pwaTitle": "App title"},
}
ERRORS = {
    "invalid_device": {
        "default": "invalid device",
        "cs": "Neplatná obrazovka",
        "en": "Invalid screen"},
    "unauthorized": {
        "default": "invalid or missing credentials",
        "cs": "Neplatné nebo chybějící přihlášení",
        "en": "Invalid or missing login"},
    "too_many_attempts": {
        "default": "too many failed login attempts",
        "cs": "Příliš mnoho neúspěšných přihlášení",
        "en": "Too many failed login attempts"},
    "too_many_requests": {
        "default": "try again in a moment",
        "cs": "Zkuste to prosím za chvíli",
        "en": "Try again in a moment"},
    "pair_capacity_full": {
        "default": "try again in a moment",
        "cs": "V tuto chvíli se připojuje příliš mnoho televizí. Zkuste to prosím za chvíli",
        "en": "Too many TVs are being connected right now, try again in a moment"},
    "invalid_json": {
        "default": "body is not valid JSON",
        "cs": "Požadavek má neplatný formát (očekává se JSON)",
        "en": "The request has an invalid format (JSON expected)"},
    "body_not_object": {
        "default": "body must be a JSON object",
        "cs": "Požadavek musí obsahovat JSON objekt",
        "en": "The request must contain a JSON object"},
    "unknown_endpoint": {
        "default": "unknown endpoint",
        "cs": "Neznámá adresa API",
        "en": "Unknown API address"},
    "preview_unavailable": {
        "default": "preview is not available",
        "cs": "Náhled není k dispozici",
        "en": "Preview is not available"},
    "invalid_install_id": {
        "default": "invalid installation identity",
        "cs": "Neplatné ID instalace",
        "en": "Invalid installation ID"},
    "invalid_pair_input": {
        "default": "invalid code or screen name",
        "cs": "Neplatný kód nebo název obrazovky",
        "en": "Invalid code or screen name"},
    "pair_code_expired": {
        "default": "code has expired",
        "cs": "Kód už neplatí",
        "en": "The code has expired"},
    "pair_code_used": {
        "default": "code has already been used",
        "cs": "Kód už byl použit",
        "en": "The code has already been used"},
    "state_save_failed": {
        "default": "state could not be saved",
        "cs": "Změny se nepodařilo uložit",
        "en": "The changes could not be saved"},
    "no_kiosk_release": {
        "default": "no kiosk version on the server",
        "cs": "Na serveru není připravená žádná verze aplikace kiosku",
        "en": "No kiosk app version is available on the server"},
    "kiosk_up_to_date": {
        "default": "screen already has this kiosk version",
        "cs": "Obrazovka už má nejnovější verzi kiosku",
        "en": "The screen already has the latest kiosk version"},
    "hours_not_integer": {
        "default": "hours must be an integer",
        "cs": "Parametr hours musí být celé číslo",
        "en": "The hours parameter must be an integer"},
    "hours_out_of_range": {
        "default": "hours must be 1 to {max}",
        "cs": "Parametr hours musí být od 1 do {max}",
        "en": "The hours parameter must be between 1 and {max}"},
    "order_not_list": {
        "default": "order must be a list of file names",
        "cs": "Parametr order musí být seznam názvů souborů",
        "en": "The order parameter must be a list of file names"},
    "file_not_string": {
        "default": "file must be a string",
        "cs": "Parametr file musí být text",
        "en": "The file parameter must be text"},
    "seconds_invalid": {
        "default": "seconds must be a number up to {max}",
        "cs": "Parametr seconds musí být číslo do {max}",
        "en": "The seconds parameter must be a number up to {max}"},
    "invalid_content_length": {
        "default": "invalid Content-Length",
        "cs": "Neplatná hlavička Content-Length",
        "en": "Invalid Content-Length header"},
    "request_too_large": {
        "default": "request is too large",
        "cs": "Požadavek je příliš velký",
        "en": "The request is too large"},
    "label_not_text": {
        "default": "label must be text",
        "cs": "Popisek musí být text",
        "en": "The label must be text"},
    "label_too_long": {
        "default": "label is too long (max {max} characters)",
        "cs": "Popisek je příliš dlouhý (nejvýše {max} znaků)",
        "en": "The label is too long (max {max} characters)"},
    "label_invalid_chars": {
        "default": "label must not contain < > or control characters",
        "cs": "Popisek nesmí obsahovat < > ani řídicí znaky",
        "en": "The label must not contain < > or control characters"},
    "brand_not_object": {
        "default": "brand must be an object",
        "cs": "Vzhled a kontakty nejsou ve správném formátu",
        "en": "Look and contacts are in an invalid format"},
    "brand_field_not_text": {
        "default": "brand.{field} must be text",
        "cs": "Pole „{field_label}“ musí být text",
        "en": "The \"{field_label}\" field must be text"},
    "brand_field_too_long": {
        "default": "brand.{field} is too long",
        "cs": "Pole „{field_label}“ je příliš dlouhé",
        "en": "The \"{field_label}\" field is too long"},
    "brand_field_bad_chars": {
        "default": "brand.{field} contains disallowed characters",
        "cs": "Pole „{field_label}“ obsahuje nepovolené znaky",
        "en": "The \"{field_label}\" field contains characters that are not allowed"},
    "brand_field_not_email": {
        "default": "brand.{field} is not an e-mail address",
        "cs": "Pole „{field_label}“ neobsahuje platný e-mail",
        "en": "The \"{field_label}\" field is not a valid e-mail address"},
    "brand_url_invalid": {
        "default": "brand.url must be http(s), mailto or an e-mail address",
        "cs": "Pole „Web / QR 1“ musí začínat http:// nebo https://, nebo v něm musí být e-mail",
        "en": "The \"Website / QR 1\" field must start with http:// or https://, or contain an e-mail address"},
    "too_many_uploads": {
        "default": "too many uploads, try again in a moment",
        "cs": "Příliš mnoho souborů se nahrává najednou. Zkuste to prosím za chvíli",
        "en": "Too many uploads at once, try again in a moment"},
    "invalid_idempotency_key": {
        "default": "invalid Idempotency-Key header",
        "cs": "Neplatná hlavička Idempotency-Key",
        "en": "Invalid Idempotency-Key header"},
    "upload_in_progress": {
        "default": "an upload with the same key is already in progress",
        "cs": "Tento soubor se už nahrává, chvíli strpení",
        "en": "This file is already being uploaded, please wait a moment"},
    "server_busy": {
        "default": "the server is busy with other uploads, try again in a moment",
        "cs": "Server právě zpracovává jiné soubory. Zkuste to prosím za chvíli",
        "en": "The server is busy with other uploads, try again in a moment"},
    "batch_too_large": {
        "default": "batch is too large (max {max} MB)",
        "cs": "Soubory jsou dohromady příliš velké (nejvýše {max} MB)",
        "en": "The files are too large together (max {max} MB)"},
    "file_too_large": {
        "default": "file is too large (max {max} MB)",
        "cs": "Soubor je příliš velký (nejvýše {max} MB)",
        "en": "The file is too large (max {max} MB)"},
    "upload_corrupt": {
        "default": "corrupted upload - data cannot be read",
        "cs": "Soubor je poškozený a nejde přečíst",
        "en": "The file is damaged and cannot be read"},
    "unsupported_type": {
        "default": "unsupported type (allowed: {allowed})",
        "cs": "Tento typ souboru nejde nahrát (povolené: {allowed})",
        "en": "This file type is not supported (allowed: {allowed})"},
    "heic_unsupported": {
        "default": "photo is HEIC (iPhone) and the TV cannot show it - on the phone set Settings > Camera > Formats to 'Most Compatible', or e-mail the photo so it gets converted to jpg",
        "cs": "Fotka je ve formátu HEIC (iPhone) a TV ji nezobrazí. V telefonu otevřete Nastavení > Fotoaparát > Formáty a zvolte „Nejlepší kompatibilita“, nebo fotku před nahráním exportujte jako JPG.",
        "en": "The photo is in HEIC format (iPhone) and the TV cannot show it. On the phone open Settings > Camera > Formats and choose \"Most Compatible\", or export the photo as JPG before uploading it."},
    "video_format_unsupported": {
        "default": "the TV browser cannot play {ext} video - convert it to mp4 with H.264 - the repo has deploy/convert-video.sh for that, or: ffmpeg -i input -c:v libx264 -profile:v baseline -c:a aac -movflags +faststart output.mp4",
        "cs": "Video ve formátu {ext} televize nepřehraje. Uložte ho jako MP4 (H.264) a nahrajte ho znovu.",
        "en": "The TV cannot play {ext} video. Save it as MP4 (H.264) and upload it again."},
    "item_limit": {
        "default": "item limit per device reached ({max})",
        "cs": "Dosažen limit položek na obrazovku ({max})",
        "en": "The item limit per screen has been reached ({max})"},
    "empty_file": {
        "default": "empty file",
        "cs": "Soubor je prázdný",
        "en": "The file is empty"},
    "media_invalid": {
        "default": "{reason}",
        "cs": "Soubor nelze použít. {reason}",
        "en": "This file cannot be used. {reason}"},
    "storage_quota": {
        "default": "total storage capacity exceeded (max {max} MB)",
        "cs": "Úložiště je plné (celkem nejvýše {max} MB)",
        "en": "Storage is full (max {max} MB in total)"},
    "schedule_invalid": {
        "default": "invalid item schedule",
        "cs": "Neplatný rozvrh: vyber dny bez opakování a vyplň čas od i do (časy se musí lišit)",
        "en": "Invalid schedule: choose each day only once and enter both a start and an end time (they must differ)"},
    "invalid_file_path": {
        "default": "invalid file path",
        "cs": "Neplatná cesta k souboru",
        "en": "Invalid file path"},
    "file_save_failed": {
        "default": "error while saving the file",
        "cs": "Soubor se nepodařilo uložit",
        "en": "The file could not be saved"},
    # --- file content, warnings and fixes (texts from mediacheck.py / tvserver.py)
    "media_bad_content": {
        "default": "file content does not match the {ext} format",
        "cs": "Obsah souboru neodpovídá formátu {ext}",
        "en": "The file content does not match the {ext} format"},
    "media_bad_webm": {
        "default": "damaged or invalid webm file",
        "cs": "Soubor WebM je poškozený nebo neplatný",
        "en": "The WebM file is damaged or invalid"},
    "media_bad_mp4": {
        "default": "invalid video file (MP4 container missing)",
        "cs": "Soubor není platné video MP4. Uložte video jako MP4 (H.264) a nahrajte ho znovu",
        "en": "The file is not a valid MP4 video. Save the video as MP4 (H.264) and upload it again"},
    "media_quicktime": {
        "default": "file is QuickTime (typically .mov from a phone or chat) - the TV browser plays only mp4; convert it e.g. with ffmpeg -i input.mov -c:v libx264 -profile:v baseline -c:a aac -movflags +faststart output.mp4",
        "cs": "Video je ve formátu QuickTime (typicky z telefonu nebo z chatu) a TV ho nepřehraje. Uložte ho jako MP4 (H.264) a nahrajte ho znovu",
        "en": "The video is in QuickTime format (typically from a phone or chat) and the TV cannot play it. Save it as MP4 (H.264) and upload it again"},
    "media_codec": {
        "default": "video uses the {codec} codec - the TV browser reliably plays only H.264; convert the file (e.g. ffmpeg -i input -c:v libx264 -movflags +faststart output.mp4)",
        "cs": "Video je ve formátu {codec_name}, který TV spolehlivě nepřehraje. Uložte ho jako MP4 (H.264) a nahrajte ho znovu",
        "en": "The video is in {codec_name} format, which the TV cannot play reliably. Save it as MP4 (H.264) and upload it again"},
    "media_image_side": {
        "default": "image is {w}x{h} - one side exceeds {max_side} px; shrink it e.g. with ffmpeg -i input -vf scale=1920:-1 output.jpg or send it from the phone at a lower resolution",
        "cs": "Obrázek má rozlišení {w}x{h} a jedna strana může mít nejvýše {max_side} px. Zmenšete ho nebo ho z telefonu pošlete v menším rozlišení",
        "en": "The image is {w}x{h} and one side can be at most {max_side} px. Make it smaller or send it from the phone in a lower resolution"},
    "media_image_huge": {
        "default": "image is {w}x{h} ({mpx:.1f} Mpx) - no TV decodes that, the limit is {max_mpx} Mpx; shrink it e.g. with ffmpeg -i input -vf scale=1920:-1 output.jpg or send it from the phone at a lower resolution",
        "cs": "Obrázek má rozlišení {w}x{h} ({mpx:.1f} megapixelů), což je pro TV příliš mnoho. Zmenšete ho nebo ho z telefonu pošlete v menším rozlišení",
        "en": "The image is {w}x{h} ({mpx:.1f} megapixels), which is too much for a TV. Make it smaller or send it from the phone in a lower resolution"},
    "warn_video_big": {
        "default": "video is {w}x{h} - modern TVs can handle it, but older panels (e.g. Samsung DB10D) decode at most {long}x{short} and would stay black",
        "cs": "Video má rozlišení {w}x{h}. Novější TV ho zvládne, ale na starších obrazovkách může zůstat černé. Bezpečné je rozlišení do {long}x{short}",
        "en": "The video is {w}x{h}. Newer TVs can play it, but older screens may stay black. Up to {long}x{short} is safe"},
    "warn_video_over_screen": {
        "default": "video is {w}x{h}, but screen {dev} reports a canvas of {sw}x{sh} - it cannot show more pixels and an older TV may not play such a video at all",
        "cs": "Video má rozlišení {w}x{h}, ale obrazovka {dev} hlásí plátno {sw}x{sh}. Víc pixelů nezobrazí a starší TV takové video nemusí přehrát vůbec",
        "en": "The video is {w}x{h}, but screen {dev} reports a canvas of {sw}x{sh}. It cannot show more pixels, and an older TV may not play such a video at all"},
    "warn_image_big": {
        "default": "photo is {w}x{h} ({mpx:.1f} Mpx) - it is shown scaled down on the TV anyway, a large file only slows loading and older panels may crash on it; shrink it e.g. with ffmpeg -i input -vf scale=1920:-1 output.jpg or send it from the phone at a lower resolution",
        "cs": "Fotka má rozlišení {w}x{h} ({mpx:.1f} megapixelů). Na TV se stejně zobrazí zmenšená, velký soubor jen zdržuje načítání a starší obrazovky na něm mohou selhat. Zmenšete ji nebo ji z telefonu pošlete v menším rozlišení",
        "en": "The photo is {w}x{h} ({mpx:.1f} megapixels). It is shown scaled down on the TV anyway, a large file only slows down loading, and older screens may fail on it. Make it smaller or send it from the phone in a lower resolution"},
    "warn_image_over_screen": {
        "default": "photo is {w}x{h}, but screen {dev} reports a canvas of {sw}x{sh} - it can safely be smaller",
        "cs": "Fotka má rozlišení {w}x{h}, ale obrazovka {dev} hlásí plátno {sw}x{sh}. Klidně může být menší",
        "en": "The photo is {w}x{h}, but screen {dev} reports a canvas of {sw}x{sh}. It can safely be smaller"},
    "warn_no_faststart": {
        "default": "video has no faststart - the moov index is at the end of the file, so the screen must download all {mb} MB before showing the first frame; convert it with -movflags +faststart",
        "cs": "Video se na obrazovce spustí až po stažení celého souboru ({mb} MB), takže první snímek chvíli počká. Uložte ho znovu jako MP4 pro web (s rychlým startem)",
        "en": "The video starts on the screen only after the whole file ({mb} MB) has downloaded, so the first frame appears late. Save it again as web-optimized MP4 (fast start)"},
    "fix_repacked_quicktime": {
        "default": "QuickTime wrapper - the file was automatically repackaged to mp4; the video was not re-encoded, quality is unchanged",
        "cs": "Soubor byl automaticky převeden z formátu QuickTime na MP4, kvalita zůstala stejná",
        "en": "The file was automatically converted from QuickTime to MP4, quality is unchanged"},
    "fix_repacked_moov": {
        "default": "moov index after the data - the file was automatically repackaged to mp4; the video was not re-encoded, quality is unchanged",
        "cs": "Soubor byl automaticky upraven pro rychlý start videa, kvalita zůstala stejná",
        "en": "The file was automatically adjusted so the video starts quickly, quality is unchanged"},
    "fix_repacked": {
        "default": "{reason} - the file was automatically repackaged to mp4; the video was not re-encoded, quality is unchanged",
        "cs": "Soubor byl automaticky převeden na MP4, kvalita zůstala stejná",
        "en": "The file was automatically converted to MP4, quality is unchanged"},
}
SUPPORTED_LANGS = ("cs", "en")


def error_text(code, lang=None, **params):
    """Error text: lang None = terse technical English (ASCII), otherwise 'cs'/'en'."""
    t = ERRORS[code]
    if lang in SUPPORTED_LANGS:
        if "field" in params:
            labels = BRAND_FIELD_LABELS[lang]
            params = {**params, "field_label": labels.get(params["field"], params["field"])}
        return t[lang].format(**params)
    return t["default"].format(**params)


class ErrText(str):
    """Default error text that carries its `code` and parameters (for localization)."""

    def __new__(cls, code, **params):
        obj = super().__new__(cls, error_text(code, None, **params))
        obj.code = code
        obj.params = params
        return obj


def localize(text, lang=None):
    """Text in the response language; ErrText is translated, a plain string is left as is."""
    if lang in SUPPORTED_LANGS and isinstance(text, ErrText):
        return error_text(text.code, lang, **text.params)
    return str(text)


def pick_lang(query_lang, accept_language):
    """Response language: None (client did not ask -> default English text) | 'cs' | 'en'.

    `?lang=` takes precedence over the Accept-Language header (the highest q
    among the supported languages wins). An unsupported value is ignored.
    """
    q = (query_lang or "").strip().lower().split("-")[0]
    if q in SUPPORTED_LANGS:
        return q
    best, best_q = None, 0.0
    for part in (accept_language or "").split(","):
        tag, _, rest = part.partition(";")
        tag = tag.strip().lower().split("-")[0]
        if tag not in SUPPORTED_LANGS:
            continue
        weight = 1.0
        m = re.search(r"q\s*=\s*([0-9.]+)", rest)
        if m:
            try:
                weight = float(m.group(1))
            except ValueError:
                continue
        if weight > best_q:
            best, best_q = tag, weight
    return best
