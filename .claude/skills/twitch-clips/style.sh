# The clips' style, shared by every run: source this from the run's clips.sh after setting
#   SRC  the stream recording (1920x1080, the whole display)
#   OUT  the folder the clips go to
#   CAM  the ffmpeg crop of the camera picture-in-picture, "crop=W:H:X:Y" (moments.py --layout)
# It is the style of ~/Downloads/bathero-clips-20260913 (2026-09-13, kept since: the user asked
# for it to stay the style of every clip). Menlo, Spanish caps titles, twitch.tv/xantwav under
# the picture, -14 LUFS.
#
#   game():     1080x1920. The camera, blurred and darkened, as the ground; the screen as a strip
#               across the top (1080x608 at y 320); the camera big under it (1080x609 at y 990,
#               cropped out of the PiP and upscaled, a touch of unsharp); the title (Menlo 46,
#               0xEAF0FF) over the top band at y 150 with a smaller line under it (32, 0xBFD4F2);
#               twitch.tv/xantwav (30, 0xD8C0F0) at y 1741 under the camera. The chat strip of the
#               2026-09-13 clips (the pane's last lines under the camera) is only worth it when the
#               chat has lines; use game_chat() then.
#   game_chat():the same with the chat pane's last lines (crop CHAT, 704x170 at 24,895 in the
#               2026-09-13 layout) under the camera: screen at y 150, camera at 790, chat at 1440,
#               handle at 1820, title at y 58.
#   gameonly(): the screen strip centred (y 656) over a blurred copy of its highway column,
#               tinted purple like the camera clips, for a stretch streamed with the camera off
#               (the game alone is nearly black). Title at y 470, handle at y 1330.
#   computer(): the 16:9 as seen, the title for 4 s in the empty area right of the highway
#               (x 1180, y 340: under the right HUD, above the camera and the description line
#               at y 600; the highway is x 770..1150, the velocity panel and the chat pane fill
#               the bottom left).
# Every function takes: start (video seconds), duration, title, subtitle, output file name.
# Fades: 0.3 s in, 0.4 s out on the picture, 0.3 / 0.5 s on the sound.
FF=/opt/homebrew/bin/ffmpeg     # the anaconda ffmpeg on PATH has no drawtext
FONT=/System/Library/Fonts/Menlo.ttc
ENC="-shortest -c:v libx264 -profile:v high -level 4.2 -crf 21 -maxrate 8M -bufsize 16M \
 -preset medium -pix_fmt yuv420p -r 30 -g 60 -c:a aac -b:a 192k -ar 48000 -movflags +faststart"
ENC169="-shortest -c:v libx264 -profile:v high -crf 16 -preset medium -pix_fmt yuv420p -r 30 -g 60 \
 -c:a aac -b:a 256k -ar 48000 -movflags +faststart"
# The platforms normalise to -14 LUFS; the stream is brought there. loudnorm's single pass let one
# accent through at +1.3 dBFS after the AAC encode (2026-09-16), so a limiter follows it, at
# -2 dBFS because the 192 kbps AAC overshoots transients by up to 1.3 dB.
LOUD="loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.79:attack=2:release=60:level=false"
: "${CHAT:=crop=704:170:24:895}"
mkdir -p "$OUT"

text () {   # $1 text $2 y $3 size $4 colour   (centred; commas and colons in the text break drawtext:
            # use the middle dot, as in '31 · PLENA · 96 BPM')
  echo "drawtext=fontfile=${FONT}:text='${1}':x=(w-text_w)/2:y=${2}:fontsize=${3}:fontcolor=${4}:shadowcolor=0x000000@0.85:shadowx=0:shadowy=2"
}
fades ()  { awk -v d="$1" 'BEGIN{printf "fade=t=in:d=0.3,fade=t=out:st=%.2f:d=0.4", d-0.4}'; }
afades () { awk -v d="$1" 'BEGIN{printf "afade=t=in:d=0.3,afade=t=out:st=%.2f:d=0.5", d-0.5}'; }
bg_cam () { echo "${CAM},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=48,eq=brightness=-0.28:saturation=1.25"; }

game () {   # $1 start  $2 dur  $3 title  $4 subtitle  $5 out
  $FF -v warning -y -ss "$1" -t "$2" -i "$SRC" -filter_complex "
    [0:v]split=3[a][b][d];
    [d]$(bg_cam)[bg];
    [a]scale=1080:608:flags=lanczos[screen];
    [b]${CAM},scale=1080:609:flags=lanczos,unsharp=3:3:0.45:3:3:0.0[cam];
    [bg][screen]overlay=0:320[t1];
    [t1][cam]overlay=0:990[t2];
    [t2]$(text "$3" 150 46 0xEAF0FF),$(text "$4" 222 32 0xBFD4F2),
       $(text 'twitch.tv/xantwav' 1741 30 0xD8C0F0),$(fades "$2")[v]" \
    -map "[v]" -map 0:a -af "$LOUD,$(afades "$2")" $ENC "$OUT/$5"
  echo "   $5"
}

game_chat () {   # $1 start  $2 dur  $3 title  $4 subtitle  $5 out   (the chat's last lines under the camera)
  $FF -v warning -y -ss "$1" -t "$2" -i "$SRC" -filter_complex "
    [0:v]split=4[a][b][c][d];
    [d]$(bg_cam)[bg];
    [a]scale=1080:608:flags=lanczos[screen];
    [b]${CAM},scale=1080:609:flags=lanczos,unsharp=3:3:0.45:3:3:0.0[cam];
    [c]${CHAT},scale=1080:261:flags=lanczos[chat];
    [bg][screen]overlay=0:150[t1];
    [t1][cam]overlay=0:790[t2];
    [t2][chat]overlay=0:1440[t3];
    [t3]$(text "$3" 58 46 0xEAF0FF),$(text "$4" 130 32 0xBFD4F2),
       $(text 'twitch.tv/xantwav' 1820 30 0xD8C0F0),$(fades "$2")[v]" \
    -map "[v]" -map 0:a -af "$LOUD,$(afades "$2")" $ENC "$OUT/$5"
  echo "   $5"
}

gameonly () {   # $1 start  $2 dur  $3 title  $4 subtitle  $5 out
  $FF -v warning -y -ss "$1" -t "$2" -i "$SRC" -filter_complex "
    [0:v]split=2[a][b];
    [a]crop=760:1080:580:0,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,
       gblur=sigma=56,eq=brightness=0.06:saturation=1.6,
       colorbalance=rs=0.2:rm=0.12:bs=0.45:bm=0.35:gs=-0.1[bg];
    [b]scale=1080:608:flags=lanczos[scr];
    [bg][scr]overlay=0:656[t1];
    [t1]$(text "$3" 470 46 0xEAF0FF),$(text "$4" 542 32 0xBFD4F2),
       $(text 'twitch.tv/xantwav' 1330 30 0xD8C0F0),$(fades "$2")[v]" \
    -map "[v]" -map 0:a -af "$LOUD,$(afades "$2")" $ENC "$OUT/$5"
  echo "   $5"
}

computer () {   # $1 start  $2 dur  $3 title  $4 subtitle  $5 out   (16:9, the frame as seen)
  $FF -v warning -y -ss "$1" -t "$2" -i "$SRC" -filter_complex "
    [0:v]drawtext=fontfile=${FONT}:text='${3}':x=1180:y=340:fontsize=32:fontcolor=0xEAF0FF:
         box=1:boxcolor=0x000000@0.55:boxborderw=12:enable='lt(t,4)',
         drawtext=fontfile=${FONT}:text='${4}':x=1180:y=396:fontsize=24:fontcolor=0xBFD4F2:
         box=1:boxcolor=0x000000@0.55:boxborderw=9:enable='lt(t,4)',$(fades "$2")[v]" \
    -map "[v]" -map 0:a -af "$LOUD,$(afades "$2")" $ENC169 "$OUT/$5"
  echo "   $5"
}
