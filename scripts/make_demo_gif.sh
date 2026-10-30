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
#   4. TWO-PASS PALETTE. A GIF holds 256 colours. One palette generated from
#      the whole clip (`palettegen`) and applied with error-diffusion dithering
#      (`paletteuse`) avoids the banding a single-pass conversion produces on
#      gradients. `stats_mode=diff` weights the palette towards the pixels that
#      actually change.
#   5. PAY FOR IT IN SIZE. Every pixel changes during a zoom or a fade, so GIF
#      compression has nothing to work with. Width and frame rate are the two
#      knobs; both are set below.
set -euo pipefail

FRAMES_DIR=${1:?usage: make_demo_gif.sh <frames-dir> <output.gif>}
OUTPUT=${2:?usage: make_demo_gif.sh <frames-dir> <output.gif>}

WIDTH=860          # README width. Bigger looks better and costs megabytes.
FPS=18             # Below ~15 the motion stutters; above ~20 the file doubles.
FADE=0.45          # Seconds of crossfade between two stills.
UPSCALE=2          # Step 1 above. See the note there: 2 is enough HERE because
                   # the stills are already wider than the output.

# Seconds to hold each still, in frame order. A still that is being read gets
# longer than one that is only being passed through.
HOLDS=(2.0 1.8 1.4 1.4 2.6 3.4)

# Zoom for each still: the factor reached by the end of its hold. 1.0 is still.
# The last one is the close-up on the citation, which is the only strong move.
ZOOMS=(1.06 1.0 1.0 1.0 1.04 1.30)

# Where the zoom is aimed, as a fraction of the frame. 0.5 0.5 is the centre.
ZOOM_X=(0.5 0.5 0.5 0.5 0.5 0.5)
ZOOM_Y=(0.5 0.5 0.5 0.5 0.5 0.55)   # aimed at the cited articles in the answer

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
    if [ "$zoom" = "1.0" ]; then
        vf="scale=${WIDTH}:${HEIGHT}:flags=lanczos,fps=${FPS},setsar=1,format=yuv420p"
    else
        vf="scale=iw*${UPSCALE}:ih*${UPSCALE}:flags=lanczos,\
zoompan=z='min(zoom+${step},${zoom})':d=${frames}:\
x='(iw-iw/zoom)*${zx}':y='(ih-ih/zoom)*${zy}':s=${WIDTH}x${HEIGHT}:fps=${FPS},\
setsar=1,format=yuv420p"
    fi

    ffmpeg -nostdin -v error -y -loop 1 -t "$hold" -i "${STILLS[$i]}" \
        -vf "$vf" -c:v ffv1 -level 3 "$WORK/scene-$i.mkv"
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

echo "Converting to GIF..."
ffmpeg -nostdin -v error -y -i "$WORK/joined.mkv" \
    -filter_complex "[0:v]split[a][b];\
[a]palettegen=max_colors=256:stats_mode=diff[p];\
[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle" \
    "$OUTPUT"

# An MP4 is kept beside the GIF: it is about a tenth of the size and plays
# more smoothly, which is worth having if the README ever moves to <video>.
# The intermediates are lossless (ffv1) so that six crossfades do not stack up
# compression artefacts; only this last step is lossy.
if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libopenh264; then
    ffmpeg -nostdin -v error -y -i "$WORK/joined.mkv" \
        -c:v libopenh264 -b:v 2500k -pix_fmt yuv420p "${OUTPUT%.gif}.mp4"
fi

echo
ls -lh "$OUTPUT" "${OUTPUT%.gif}.mp4" 2>/dev/null || ls -lh "$OUTPUT"
ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,nb_frames -of default=noprint_wrappers=1 "$OUTPUT"
