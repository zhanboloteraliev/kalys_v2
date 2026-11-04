#!/usr/bin/env bash
#
# Build README demo GIF from a handful of screenshots.
#
#   ./scripts/make_demo_gif.sh <frames-dir> <output.gif>
#
# The frames directory holds PNGs named so that `sort` puts them in order
# (01-..., 02-..., ...). Each one is a real screenshot of the app; this script
# supplies the motion between them.
#
# Why a script instead of a screen recorder: the browser is driven through an
# extension that can take screenshots but cannot record video, and recording
# the whole desktop catches whatever else is on it. Six stills plus ffmpeg
# gives a smoother result than a screen capture anyway, because every frame is
# sharp and nothing is dropped.
#
# How the smoothness is produced, which is the part worth remembering:
#
#   1. UPSCALE BEFORE ZOOMING. `zoompan` rounds the crop origin to whole
#      pixels of its INPUT, so a slow zoom moves in visible jerks when the
#      input is no bigger than the output - the single biggest cause of ugly
#      zoom GIFs. What matters is the ratio of input width to output width:
#      about 3x is plenty. These stills are 1512px for an 860px GIF, so an
#      upscale of 2 reaches 3.5x and is enough. Do not raise it without
#      reason: at 4x the filter holds 6048x3236 frames in memory, which is how
#      the first run of this script got killed for memory pressure.
#      Scenes that do not zoom skip the upscale entirely.
#   2. HOLD, THEN MOVE. A frame the reader has to read (a full answer, a
#      citation) is held still first. Motion under text makes it unreadable.
#   3. CROSSFADE, DO NOT CUT. `xfade` interpolates every frame between two
#      stills, which is what makes six pictures look like a recording.
#   4. TWO-PASS PALETTE, THROUGH A FILE. A GIF holds 256 colours. One palette
#      generated from the whole clip (`palettegen`) and applied with
#      error-diffusion dithering (`paletteuse`) avoids the banding a one-shot
#      conversion produces on gradients. `stats_mode=diff` weights the palette
#      towards the pixels that actually change. Write the palette to a PNG and
#      read it back in a second pass: chaining the two with `split` in one
#      command holds every frame in memory while it waits for the palette, and
#      gets ffmpeg killed.
#   5. PAY FOR IT IN SIZE. Every pixel changes during a zoom or a fade, so GIF
#      compression has nothing to work with. Width and frame rate are the two
#      knobs; both are set below.
set -euo pipefail

FRAMES_DIR=${1:?usage: make_demo_gif.sh <frames-dir> <output.gif>}
OUTPUT=${2:?usage: make_demo_gif.sh <frames-dir> <output.gif>}

WIDTH=860          # README width. Bigger looks better and costs megabytes.
FPS=20             # Below ~15 the motion stutters; above ~25 the file doubles.
                   # Use a rate that divides 100: a GIF stores each frame's
                   # delay in hundredths of a second, so 18 fps is written as
                   # 5cs and plays back as 20 anyway. 20, 25 and 10 are exact;
                   # 18, 24 and 30 are not.
FADE=0.45          # Seconds of crossfade between two stills.
UPSCALE=2          # Step 1 above. See the note there: 2 is enough HERE because
                   # the stills are already wider than the output.

# Seconds to hold each still, in frame order. A still that is being read gets
# longer than one that is only being passed through.
HOLDS=(2.0 1.8 1.2 3.0 3.4)

# Zoom for each still: the factor reached by the end of its hold. 1.0 is still.
# The last one is the close-up on the citation, which is the only strong move.
ZOOMS=(1.06 1.0 1.0 1.0 1.30)

# Where the zoom is aimed, as a fraction of the frame. 0.5 0.5 is the centre.
ZOOM_X=(0.5 0.5 0.5 0.5 0.55)
ZOOM_Y=(0.5 0.5 0.5 0.5 0.35)   # aimed at the cited articles in the answer

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mapfile -t STILLS < <(find "$FRAMES_DIR" -maxdepth 1 -name '*.png' | sort)
if [ "${#STILLS[@]}" -eq 0 ]; then
    echo "No PNG frames in $FRAMES_DIR" >&2
    exit 1
fi

# zoompan wants an exact output size, so the height comes from the first still's
# aspect ratio, rounded to an even number (h.264 needs even dimensions).
SRC=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height \
    -of csv=p=0:s=x "${STILLS[0]}")
HEIGHT=$(python3 -c "
w, h = '$SRC'.split('x')
print(round(int(h) * $WIDTH / int(w) / 2) * 2)
")
echo "Output ${WIDTH}x${HEIGHT} at ${FPS} fps, from ${SRC} stills"

echo "Building ${#STILLS[@]} scenes..."
for i in "${!STILLS[@]}"; do
    hold=${HOLDS[$i]:-1.5}
    zoom=${ZOOMS[$i]:-1.0}
    zx=${ZOOM_X[$i]:-0.5}
    zy=${ZOOM_Y[$i]:-0.5}
    frames=$(python3 -c "print(max(1, round($hold * $FPS)))")

    # zoompan needs the zoom as a per-frame increment, not a destination.
    step=$(python3 -c "print(($zoom - 1.0) / max(1, $frames - 1))")

    # A still scene needs no zoompan at all: scale it once and hold it.
    #
    # The length is set differently in the two cases, and getting this wrong is
    # expensive. `zoompan`'s `d` is the number of frames emitted PER INPUT
    # FRAME, and `-loop 1` supplies input frames for ever, so pairing it with
    # `-t` multiplies the two and produced a 294-second, 351 MB GIF on the
    # first run. `-frames:v` caps the OUTPUT instead, which is what is wanted:
    # one input frame expanded into exactly this many.
    if [ "$zoom" = "1.0" ]; then
        vf="scale=${WIDTH}:${HEIGHT}:flags=lanczos,fps=${FPS},setsar=1,format=yuv420p"
        length=(-t "$hold")
    else
        vf="scale=iw*${UPSCALE}:ih*${UPSCALE}:flags=lanczos,\
zoompan=z='min(zoom+${step},${zoom})':d=${frames}:\
x='(iw-iw/zoom)*${zx}':y='(ih-ih/zoom)*${zy}':s=${WIDTH}x${HEIGHT}:fps=${FPS},\
setsar=1,format=yuv420p"
        length=(-frames:v "$frames")
    fi

    ffmpeg -nostdin -v error -y -loop 1 -i "${STILLS[$i]}" \
        -vf "$vf" "${length[@]}" -c:v ffv1 -level 3 "$WORK/scene-$i.mkv"
done

# Chain the scenes with crossfades. Each fade overlaps the two clips, so every
# join shortens the result by FADE seconds - the offsets have to account for
# that or the last scenes are cut off.
inputs=()
for i in "${!STILLS[@]}"; do inputs+=(-i "$WORK/scene-$i.mkv"); done

if [ "${#STILLS[@]}" -eq 1 ]; then
    filter="[0:v]null[out]"
else
    filter=""
    offset=0
    prev="[0:v]"
    for ((i = 1; i < ${#STILLS[@]}; i++)); do
        hold=${HOLDS[$((i - 1))]:-1.5}
        offset=$(python3 -c "print(round($offset + $hold - $FADE, 3))")
        label="[x$i]"
        [ "$i" -eq $((${#STILLS[@]} - 1)) ] && label="[out]"
        filter+="${prev}[${i}:v]xfade=transition=fade:duration=${FADE}:offset=${offset}${label};"
        prev="$label"
    done
    filter=${filter%;}
fi

echo "Crossfading..."
ffmpeg -nostdin -v error -y "${inputs[@]}" \
    -filter_complex "$filter" -map "[out]" \
    -c:v ffv1 -level 3 "$WORK/joined.mkv"

# Two passes, writing the palette to a file in between. Doing it in one
# command with `split` instead looks tidier and is a memory trap: paletteuse
# cannot start until palettegen has seen the last frame, so the whole clip is
# held in memory and ffmpeg is killed on a machine with a few GB free. Two
# passes stream.
echo "Converting to GIF (pass 1: palette)..."
ffmpeg -nostdin -v error -y -i "$WORK/joined.mkv" \
    -vf "palettegen=max_colors=256:stats_mode=diff" "$WORK/palette.png"

echo "Converting to GIF (pass 2: dither)..."
ffmpeg -nostdin -v error -y -i "$WORK/joined.mkv" -i "$WORK/palette.png" \
    -lavfi "paletteuse=dither=sierra2_4a:diff_mode=rectangle" \
    "$OUTPUT"

# An MP4 is kept beside the GIF: it is about a tenth of the size and plays
# more smoothly, which is worth having if the README ever moves to <video>.
# The intermediates are lossless (ffv1) so that six crossfades do not stack up
# compression artefacts; only this last step is lossy.
# Read the encoder list into a variable rather than piping it to `grep -q`:
# grep closes the pipe as soon as it matches, ffmpeg takes SIGPIPE, and under
# `pipefail` that reads as "not found" - so the MP4 was silently skipped.
ENCODERS=$(ffmpeg -hide_banner -encoders 2>/dev/null || true)
case "$ENCODERS" in
    *libopenh264*)
        ffmpeg -nostdin -v error -y -i "$WORK/joined.mkv" \
            -c:v libopenh264 -b:v 2500k -pix_fmt yuv420p "${OUTPUT%.gif}.mp4"
        ;;
    *)
        echo "No h.264 encoder found; skipping the MP4 companion."
        ;;
esac

echo
ls -lh "$OUTPUT" "${OUTPUT%.gif}.mp4" 2>/dev/null || ls -lh "$OUTPUT"
ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,nb_frames -of default=noprint_wrappers=1 "$OUTPUT"
