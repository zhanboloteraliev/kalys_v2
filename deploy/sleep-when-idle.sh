#!/usr/bin/env bash
#
# Stop the instance when nobody has used the site for a while.
#
# Run by kalys-sleep.timer every few minutes. The instance's
# InstanceInitiatedShutdownBehavior is `stop` (not `terminate`), so
# `shutdown -h now` from inside stops the instance and keeps the disk. That is
# why this needs no AWS credentials and no IAM permission at all - check it
# with:
#
#   aws ec2 describe-instance-attribute --instance-id <id> \
#       --attribute instanceInitiatedShutdownBehavior
#
# Waking up again is deploy/waker/, reached through CloudFront. Do not enable
# this timer before the waker works, or the site is simply down once it sleeps.
set -euo pipefail

IDLE_MINUTES=${KALYS_IDLE_MINUTES:-20}
ACCESS_LOG=${KALYS_ACCESS_LOG:-/var/log/caddy/access.log}
APP_PORT=${KALYS_APP_PORT:-8000}
# A browser tab left open holds a WebSocket for ever, so an open connection
# cannot mean "stay awake" without a limit. After this long with no new
# request, the tab is treated as abandoned and the instance sleeps anyway.
ABANDONED_MINUTES=${KALYS_ABANDONED_MINUTES:-60}

say() { logger -t kalys-sleep "$1"; echo "$1"; }

if [ ! -f "$ACCESS_LOG" ]; then
    # No log means no evidence of idleness. Never sleep on a guess.
    say "no access log at $ACCESS_LOG; staying awake"
    exit 0
fi

now=$(date +%s)

# Caddy touches the access log on every request, so its modification time is
# the time of the last request. No parsing needed.
last_request=$(stat -c %Y "$ACCESS_LOG")

# Boot time counts as activity. Without this the instance would wake, see a log
# file last touched yesterday, and go straight back to sleep under the feet of
# the visitor who just woke it.
boot=$(date -d "$(uptime -s)" +%s)

last=$(( last_request > boot ? last_request : boot ))
idle=$(( (now - last) / 60 ))

if pgrep -f ssm-session-worker >/dev/null 2>&1 || [ -n "$(who -s 2>/dev/null)" ]; then
    say "someone is logged in; staying awake"
    exit 0
fi

if [ "$idle" -lt "$IDLE_MINUTES" ]; then
    exit 0
fi

if [ "$idle" -lt "$ABANDONED_MINUTES" ] \
    && ss -tn state established "( sport = :$APP_PORT )" 2>/dev/null | tail -n +2 | grep -q .; then
    say "idle ${idle}m but a connection is still open; staying awake"
    exit 0
fi

say "idle for ${idle}m; stopping the instance"
/sbin/shutdown -h now
