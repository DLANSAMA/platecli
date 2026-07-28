#!/usr/bin/env bash
# Turn the raw job-hero capture into a postable ~45-60 s cut:
# keep the command + download at real speed, timelapse the slicing dead air,
# keep the upload/print-start/status ending at real speed.
#
# Usage: scripts/trim_hero.sh <slice_start_s> <slice_end_s>
#   slice_start_s  capture time where slicing begins (download done)
#   slice_end_s    capture time where upload output appears
# Find the two timestamps by scrubbing docs/job-hero.mp4 once in any player.
set -euo pipefail

IN=docs/job-hero.mp4
OUT=docs/job-hero-post.mp4
A="${1:?slice_start_s}"
B="${2:?slice_end_s}"
SPEED=12  # timelapse factor for the quiet middle

ffmpeg -y -i "$IN" -filter_complex "
  [0:v]trim=0:${A},setpts=PTS-STARTPTS[head];
  [0:v]trim=${A}:${B},setpts=(PTS-STARTPTS)/${SPEED}[mid];
  [0:v]trim=${B},setpts=PTS-STARTPTS[tail];
  [head][mid][tail]concat=n=3:v=1:a=0[v]
" -map "[v]" -an -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p "$OUT"

echo "Wrote $OUT"
echo "Now CHECK FRAMES for leaks before posting, e.g.:"
echo "  ffmpeg -i $OUT -vsync 0 -q:v 2 /tmp/hero_%03d.jpg  # then actually look at them"
