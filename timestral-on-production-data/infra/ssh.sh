#!/bin/bash
# SSH to the GPU instance launch.sh started. SSH_USER defaults to ubuntu (the user on Ubuntu-based GPU AMIs).
. "$(dirname "$0")/instance.env"
exec ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR \
  -o ServerAliveInterval=30 -i "$(dirname "$0")/$KEY_NAME.pem" "${SSH_USER:-ubuntu}@$INSTANCE_IP" "$@"
