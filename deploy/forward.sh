#!/usr/bin/env bash
# Open the site at http://localhost:8000 by forwarding a port to the server.
#
# The app on the instance listens on 127.0.0.1 only, so there is no port open
# to the internet. Session Manager tunnels through the SSM agent instead, which
# is why this needs no SSH key and no open security group rule.
#
# Stop the tunnel with Ctrl-C.
set -euo pipefail

INSTANCE="${KALYS_INSTANCE:-i-0aa39205f5024aa85}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
PORT="${KALYS_PORT:-8000}"

# The account's default profile is a different (and currently stale) identity,
# so name the profile here rather than relying on whatever is in the shell.
export AWS_PROFILE="${AWS_PROFILE:-kalys}"
export PATH="$HOME/.local/bin:$PATH"   # session-manager-plugin lives here

echo "Forwarding localhost:$PORT to $INSTANCE ..."
echo "Open http://localhost:$PORT once it says 'Waiting for connections'."
echo "The app loads its embedding model at startup, so give it a minute after a restart."

exec aws ssm start-session \
  --region "$REGION" \
  --target "$INSTANCE" \
  --document-name AWS-StartPortForwardingSession \
  --parameters "{\"portNumber\":[\"$PORT\"],\"localPortNumber\":[\"$PORT\"]}"
