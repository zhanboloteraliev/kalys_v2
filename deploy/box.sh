#!/usr/bin/env bash
#
# Run a command on the Kalys instance, without SSH.
#
#   ./deploy/box.sh 'cd ~/kalys_v2 && git pull && sudo systemctl restart kalys'
#   ./deploy/box.sh 'systemctl is-active kalys'
#   ./deploy/box.sh 'sudo journalctl -u kalys -n 30 --no-pager' 20
#
# $1 = the command. $2 = seconds to wait for it (default 25); a git pull and a
# restart need about 20, a log read needs 5.
#
# Why this exists: port 22 is closed (docs/deploy.md step 7e), so there is no
# `ssh box 'command'`. `kalysdev` is allowed ssm:StartSession but NOT
# ssm:SendCommand, so the command cannot be sent as data either - it has to be
# typed into an interactive session's shell, which is what this does.
#
# Two details that are easy to get wrong:
#   - A session logs in as `ssm-user`, but the project belongs to `ubuntu`, so
#     every command is wrapped in `sudo -u ubuntu bash -lc`.
#   - stdin has to stay open until the command is done. Piping the text
#     straight in closes it immediately and the plugin dies with
#     "Cannot perform start session: EOF" - hence the sleeps.
set -u

# Same two lines as forward.sh: the account's default profile is a different
# (and stale) identity, and session-manager-plugin lives in ~/.local/bin.
export AWS_PROFILE=${AWS_PROFILE:-kalys}
export PATH="$HOME/.local/bin:$PATH"
INSTANCE=${KALYS_INSTANCE:-i-0aa39205f5024aa85}
COMMAND=${1:?usage: box.sh '<command>' [seconds-to-wait]}
WAIT=${2:-25}

{
    sleep 4
    printf 'sudo -u ubuntu bash -lc %q\n' "$COMMAND"
    sleep "$WAIT"
    printf 'exit\n'
    sleep 2
} | aws ssm start-session --target "$INSTANCE" 2>&1 \
    | grep -v -E '^(Starting session|Exiting session|SessionId)'
