#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# convert-video.sh — convert any video into a form the web player on the
# panel (/tv/<device>) can swallow.
#
# Recipe and reasons, in short: the panel's browser reports "probably" only for
# H.264 baseline in an MP4 container, the decoder tops out at 1920×1080 / Level 4.1
# and a file without faststart only starts playing after the whole mdat is downloaded.
#
#   ./deploy/convert-video.sh INPUT [OUTPUT]
#
# Without OUTPUT the name is derived from the input (extension .mp4; if the
# input already is .mp4, "-web" is appended so the original is not overwritten).
#
# What the script does:
#   * detects the orientation (including rotation from metadata) and picks a
#     1280×720 or 720×1280 target - portrait and landscape alike
#   * NEVER upscales, only downscales when needed; the aspect ratio is kept,
#     no black bars are added (the player uses object-fit: contain)
#   * rounds the resulting dimensions down to even numbers (H.264 requirement)
#   * when the source has no audio, mixes in a silent AAC track (some players
#     react oddly to video without audio)
#   * verifies the result at the end and exits non-zero on any mismatch
# ---------------------------------------------------------------------------
set -uo pipefail

# --- Encoding parameters (see docs/97) --------------------------------------
BOX_LONG=1280           # longer side of the panel canvas
BOX_SHORT=720           # shorter side of the panel canvas
FPS_MAX=30              # frame rate cap (a lower rate is not raised)
GOP=30                  # keyframe every 30 frames
VB=3500k                # target video bitrate
VMAX=4000k              # cap ~4 Mbps
VBUF=6000k
PRESET=${PRESET:-medium}
LEVEL=4.0               # the panel decoder handles at most 4.1
LEVEL_MAX=41            # ffprobe reports the level as an integer (40 = 4.0)

# --- Colors (only on a terminal) ---------------------------------------
if [ -t 1 ]; then
    GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; NC=$'\033[0m'
else
    GREEN=""; RED=""; DIM=""; NC=""
fi

die() { printf "%serror:%s %s\n" "$RED" "$NC" "$*" >&2; exit 1; }

# Help = the header comment of this file (from line 2 up to the first
# non-comment line), without the hash marks.
usage() {
    sed -n '2,${/^#/!q;p;}' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

# --- Tool and argument checks -----------------------------------------
[ $# -ge 1 ] || usage 1
case "$1" in -h|--help) usage 0 ;; esac

for bin in ffmpeg ffprobe; do
    command -v "$bin" >/dev/null 2>&1 || die \
"$bin not found. Install ffmpeg:
    Debian/Ubuntu:  sudo apt install ffmpeg
    Fedora:         sudo dnf install ffmpeg
    macOS:          brew install ffmpeg"
done

IN=$1
[ -e "$IN" ] || die "input file does not exist: $IN"
[ -f "$IN" ] || die "input is not a file: $IN"
[ -r "$IN" ] || die "input file is not readable: $IN"
[ -s "$IN" ] || die "input file is empty: $IN"

if [ $# -ge 2 ] && [ -n "$2" ]; then
    OUT=$2
else
    base=${IN%.*}
    [ "$base" = "$IN" ] && base="$IN"          # file without an extension
    OUT="$base.mp4"
    [ "$OUT" = "$IN" ] && OUT="$base-web.mp4"  # input already is .mp4
fi
[ "$(readlink -f "$OUT" 2>/dev/null || echo "$OUT")" = \
  "$(readlink -f "$IN"  2>/dev/null || echo "$IN")" ] && \
    die "output would overwrite the input ($IN) — give a different target"
outdir=$(dirname "$OUT")
[ -d "$outdir" ] || die "target directory does not exist: $outdir"

# --- Input probe ----------------------------------------------------------
probe() { ffprobe -v error "$@" -of default=noprint_wrappers=1:nokey=1 "$IN" 2>/dev/null; }

read -r SRC_W SRC_H < <(probe -select_streams v:0 -show_entries stream=width,height | tr '\n' ' ')
[ -n "${SRC_W:-}" ] && [ -n "${SRC_H:-}" ] || \
    die "no readable video stream in the input (corrupted file?): $IN"

# Rotation from metadata: ffmpeg applies it itself while encoding, so we have
# to account for it already when choosing the orientation. Newer ffmpeg puts
# it into side_data, older into the 'rotate' tag.
ROT=$(probe -select_streams v:0 -show_entries stream_side_data=rotation | head -1)
[ -n "${ROT:-}" ] || ROT=$(probe -select_streams v:0 -show_entries stream_tags=rotate | head -1)
ROT=${ROT:-0}
ROT=${ROT%%.*}; ROT=${ROT#+}
ROT_ABS=${ROT#-}

DISP_W=$SRC_W; DISP_H=$SRC_H
if [ "$ROT_ABS" = "90" ] || [ "$ROT_ABS" = "270" ]; then
    DISP_W=$SRC_H; DISP_H=$SRC_W
fi

# Frame rate - only a cap, a lower rate is never raised (frame duplication).
SRC_FPS=$(probe -select_streams v:0 -show_entries stream=avg_frame_rate | head -1)
case "${SRC_FPS:-}" in ""|"0/0"|"N/A") SRC_FPS=$(probe -select_streams v:0 -show_entries stream=r_frame_rate | head -1) ;; esac
FPS_NUM=$(awk -v f="${SRC_FPS:-0/0}" 'BEGIN{n=split(f,a,"/"); if(n==2 && a[2]+0>0) printf "%.3f", a[1]/a[2]; else print 0}')

# Audio: if missing, we mix in silence.
HAS_AUDIO=0
[ -n "$(probe -select_streams a:0 -show_entries stream=codec_type | head -1)" ] && HAS_AUDIO=1

# --- Target selection: orientation + downscale without upscaling --------------------------
if [ "$DISP_W" -ge "$DISP_H" ]; then
    BOX_W=$BOX_LONG; BOX_H=$BOX_SHORT; ORIENT="landscape"
else
    BOX_W=$BOX_SHORT; BOX_H=$BOX_LONG; ORIENT="portrait"
fi

# s = min(1, BOX_W/W, BOX_H/H) → we never enlarge; even numbers rounded down so
# that rounding never takes us above the original (H.264 wants even dimensions).
read -r OUT_W OUT_H < <(awk -v w="$DISP_W" -v h="$DISP_H" -v bw="$BOX_W" -v bh="$BOX_H" 'BEGIN{
    s = 1
    if (bw/w < s) s = bw/w
    if (bh/h < s) s = bh/h
    ow = int(w*s/2)*2; oh = int(h*s/2)*2
    if (ow < 2) ow = 2
    if (oh < 2) oh = 2
    print ow, oh
}')

VF="scale=${OUT_W}:${OUT_H}:flags=lanczos"
[ "$OUT_W" = "$DISP_W" ] && [ "$OUT_H" = "$DISP_H" ] && VF="scale=${OUT_W}:${OUT_H}"
FPS_NOTE="unchanged"
if awk -v f="$FPS_NUM" -v m="$FPS_MAX" 'BEGIN{exit !(f > m + 0.01)}'; then
    VF="fps=${FPS_MAX},${VF}"
    FPS_NOTE="capped to $FPS_MAX"
fi
VF="${VF},format=yuv420p"

SIZE_IN=$(stat -c%s "$IN")

printf "%sinput%s   %s\n" "$DIM" "$NC" "$IN"
printf "%s        %s×%s%s, rotation %s°, %s fps, audio: %s, %s\n" \
    "$DIM" "$SRC_W" "$SRC_H" \
    "$([ "$DISP_W" != "$SRC_W" ] && echo " (displayed ${DISP_W}×${DISP_H})")" \
    "$ROT" "$FPS_NUM" "$([ "$HAS_AUDIO" = 1 ] && echo yes || echo "no → adding silence")" \
    "$(numfmt --to=iec --suffix=B "$SIZE_IN" 2>/dev/null || echo "$SIZE_IN B")$NC"
printf "%soutput%s  %s\n" "$DIM" "$NC" "$OUT"
printf "%s        %s → target %s×%s, result %s×%s, fps %s%s\n\n" \
    "$DIM" "$ORIENT" "$BOX_W" "$BOX_H" "$OUT_W" "$OUT_H" "$FPS_NOTE" "$NC"

# --- Transcoding -----------------------------------------------------------
FFARGS=(-y -hide_banner -loglevel warning -stats -i "$IN")
if [ "$HAS_AUDIO" = 0 ]; then
    FFARGS+=(-f lavfi -i "anullsrc=channel_layout=stereo:sample_rate=48000"
             -map 0:v:0 -map 1:a:0 -shortest)
else
    FFARGS+=(-map 0:v:0 -map 0:a:0)
fi
FFARGS+=(
    -vf "$VF"
    -c:v libx264 -profile:v baseline -level "$LEVEL" -bf 0
    -preset "$PRESET" -g "$GOP" -keyint_min "$GOP" -sc_threshold 0
    -b:v "$VB" -maxrate "$VMAX" -bufsize "$VBUF"
    -c:a aac -b:a 128k -ar 48000 -ac 2
    -movflags +faststart -f mp4 "$OUT"
)
ffmpeg "${FFARGS[@]}" || die "ffmpeg failed (output $OUT incomplete)"
echo

# ---------------------------------------------------------------------------
# Result verification - anything that does not match, we exit non-zero
# ---------------------------------------------------------------------------
FAIL=0
ok()   { printf "  %sOK%s    %s\n" "$GREEN" "$NC" "$*"; }
bad()  { printf "  %sFAIL%s  %s\n" "$RED" "$NC" "$*"; FAIL=$((FAIL + 1)); }

# Walks the top-level MP4 boxes and prints their names in file order.
box_order() {
    local f=$1 off=0 hdr size name fsize
    fsize=$(stat -c%s "$f")
    while [ "$off" -lt "$fsize" ] && [ "$off" -ge 0 ]; do
        hdr=$(dd if="$f" bs=1 skip="$off" count=8 2>/dev/null | od -An -tx1 -v | tr -d ' \n')
        [ ${#hdr} -eq 16 ] || break
        size=$((16#${hdr:0:8}))
        name=$(printf '%b' "\\x${hdr:8:2}\\x${hdr:10:2}\\x${hdr:12:2}\\x${hdr:14:2}")
        echo "$name"
        if [ "$size" -eq 1 ]; then                 # 64-bit size
            local big
            big=$(dd if="$f" bs=1 skip=$((off + 8)) count=8 2>/dev/null | od -An -tx1 -v | tr -d ' \n')
            [ ${#big} -eq 16 ] || break
            size=$((16#$big))
        elif [ "$size" -eq 0 ]; then               # box extends to the end of the file
            break
        fi
        [ "$size" -lt 8 ] && break
        off=$((off + size))
    done
}

vprobe() { ffprobe -v error "$@" -of default=noprint_wrappers=1:nokey=1 "$OUT" 2>/dev/null; }

[ -s "$OUT" ] || die "output file was not created or is empty: $OUT"
SIZE_OUT=$(stat -c%s "$OUT")

MAJOR=$(ffprobe -v error -show_entries format_tags=major_brand -of default=noprint_wrappers=1:nokey=1 "$OUT" 2>/dev/null)
COMPAT=$(ffprobe -v error -show_entries format_tags=compatible_brands -of default=noprint_wrappers=1:nokey=1 "$OUT" 2>/dev/null)
read -r V_W V_H < <(vprobe -select_streams v:0 -show_entries stream=width,height | tr '\n' ' ')
V_PROFILE=$(vprobe -select_streams v:0 -show_entries stream=profile | head -1)
V_LEVEL=$(vprobe -select_streams v:0 -show_entries stream=level | head -1)
V_PIXFMT=$(vprobe -select_streams v:0 -show_entries stream=pix_fmt | head -1)
V_FPS=$(vprobe -select_streams v:0 -show_entries stream=avg_frame_rate | head -1)
V_FPSN=$(awk -v f="${V_FPS:-0/0}" 'BEGIN{n=split(f,a,"/"); if(n==2 && a[2]+0>0) printf "%.3f", a[1]/a[2]; else print 0}')
A_CODEC=$(vprobe -select_streams a:0 -show_entries stream=codec_name | head -1)
A_RATE=$(vprobe -select_streams a:0 -show_entries stream=sample_rate | head -1)
BITRATE=$(vprobe -show_entries format=bit_rate | head -1)
BOXES=$(box_order "$OUT" | head -12 | tr '\n' ' ')
LEVEL_H=$(awk -v l="${V_LEVEL:-0}" 'BEGIN{printf "%.1f", l/10}')

printf "%sVerification %s%s\n" "$DIM" "$OUT" "$NC"

case " $MAJOR $COMPAT " in
    *isom*|*iso2*|*mp41*|*mp42*|*avc1*) ok  "ftyp brand: major=$MAJOR, compatible=$COMPAT" ;;
    *) bad "ftyp brand is not MP4: major='$MAJOR', compatible='$COMPAT' (the panel will reject it)" ;;
esac

MOOV=$(echo "$BOXES" | tr ' ' '\n' | grep -n '^moov$' | head -1 | cut -d: -f1)
MDAT=$(echo "$BOXES" | tr ' ' '\n' | grep -n '^mdat$' | head -1 | cut -d: -f1)
if [ -n "$MOOV" ] && [ -n "$MDAT" ] && [ "$MOOV" -lt "$MDAT" ]; then
    ok  "faststart: moov before mdat (box order: ${BOXES% })"
else
    bad "faststart missing — box order: ${BOXES% } (moov must come before mdat)"
fi

case "$V_PROFILE" in
    *Baseline*) ok  "profile: $V_PROFILE" ;;
    *) bad "profile is '$V_PROFILE', the panel is confirmed only for Baseline" ;;
esac

if [ -n "${V_LEVEL:-}" ] && [ "$V_LEVEL" -le "$LEVEL_MAX" ] 2>/dev/null; then
    ok  "level: $LEVEL_H (max ${LEVEL_MAX:0:1}.${LEVEL_MAX:1:1})"
else
    bad "level $LEVEL_H exceeds the panel decoder's capabilities"
fi

if [ "$V_W" = "$OUT_W" ] && [ "$V_H" = "$OUT_H" ] \
   && [ $((V_W % 2)) -eq 0 ] && [ $((V_H % 2)) -eq 0 ] \
   && [ "$V_W" -le "$BOX_W" ] && [ "$V_H" -le "$BOX_H" ] \
   && [ "$V_W" -le "$DISP_W" ] && [ "$V_H" -le "$DISP_H" ]; then
    ok  "dimensions: ${V_W}×${V_H} (even, within ${BOX_W}×${BOX_H}, no upscaling from ${DISP_W}×${DISP_H})"
else
    bad "dimensions ${V_W}×${V_H} do not match (expected ${OUT_W}×${OUT_H}, even, within ${BOX_W}×${BOX_H}, no upscaling)"
fi

[ "$V_PIXFMT" = "yuv420p" ] && ok "pixel format: $V_PIXFMT" || bad "pixel format is '$V_PIXFMT', expected yuv420p"

if awk -v f="$V_FPSN" -v m="$FPS_MAX" 'BEGIN{exit !(f <= m + 0.01 && f > 0)}'; then
    ok  "frame rate: $V_FPSN fps (cap $FPS_MAX)"
else
    bad "frame rate $V_FPSN fps is above the cap $FPS_MAX"
fi

if [ "$A_CODEC" = "aac" ]; then
    ok  "audio: $A_CODEC $A_RATE Hz$([ "$HAS_AUDIO" = 0 ] && echo " (silence mixed in)")"
else
    bad "audio track is missing or not AAC (codec: '${A_CODEC:-none}')"
fi

if awk -v b="${BITRATE:-0}" 'BEGIN{exit !(b > 0 && b <= 4600000)}'; then
    ok  "bitrate: $(awk -v b="$BITRATE" 'BEGIN{printf "%.1f", b/1000000}') Mbps (cap ~4)"
else
    bad "bitrate $(awk -v b="${BITRATE:-0}" 'BEGIN{printf "%.1f", b/1000000}') Mbps is above the ~4 Mbps cap"
fi

printf "  %ssize: %s → %s (%s)%s\n" "$DIM" \
    "$(numfmt --to=iec --suffix=B "$SIZE_IN" 2>/dev/null || echo "$SIZE_IN B")" \
    "$(numfmt --to=iec --suffix=B "$SIZE_OUT" 2>/dev/null || echo "$SIZE_OUT B")" \
    "$(awk -v a="$SIZE_IN" -v b="$SIZE_OUT" 'BEGIN{printf "%+.0f %%", (b-a)*100/a}')" "$NC"

echo
if [ "$FAIL" -gt 0 ]; then
    printf "%sResult: %d checks failed — do NOT send the file to the panel.%s\n" "$RED" "$FAIL" "$NC"
    exit 1
fi
printf "%sDone: %s is ready for the web player.%s\n" "$GREEN" "$OUT" "$NC"
