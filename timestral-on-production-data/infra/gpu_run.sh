#!/bin/bash
# Copy the code, the series list and the production data to the GPU instance, run remote_run.sh there,
# and copy the results back to results/production/tsfm_cuda/.
set -eu
cd "$(dirname "$0")/.."
. infra/instance.env
SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i infra/$KEY_NAME.pem"
DEST="${SSH_USER:-ubuntu}@$INSTANCE_IP"
until infra/ssh.sh true 2>/dev/null; do sleep 5; done
rsync -az -e "$SSH" --exclude envs --exclude results --exclude '.venv' --exclude '*.pem' --exclude instance.env \
  ./ "$DEST:bench/"
infra/ssh.sh 'bash bench/infra/remote_run.sh'
mkdir -p results/production
rsync -az -e "$SSH" "$DEST:bench/results/production/tsfm_cuda" results/production/
