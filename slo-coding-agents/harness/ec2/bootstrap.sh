#!/bin/bash
# EC2 user-data: run ONE Larder trial unattended, upload results, then terminate this instance.
#
# Rendered by harness/ec2/launch.py, which fills in the double-at placeholders. Runs as root at first boot on
# Amazon Linux 2023. Everything after setup runs as the unprivileged user "bench".
# The instance is launched with instance-initiated-shutdown-behavior=terminate, so every exit path
# that reaches `shutdown` terminates the instance and deletes its EBS volume.
set -uo pipefail

S3=@@S3_PREFIX@@                 # s3://bucket/prefix (bundle in, results out)
PARAMS=@@PARAM_PATH@@            # SSM Parameter Store path holding the credentials
CONDITION=@@CONDITION@@
NOT_BEFORE=@@NOT_BEFORE@@        # UTC ISO time: don't touch the Coralogix account before this
MAX_MIN=@@MAX_MIN@@              # dead-man switch: terminate after this many minutes, whatever happens
AGENT_MODEL=@@AGENT_MODEL@@
PREPARE_ARGS="@@PREPARE_ARGS@@"   # extra ./bench prepare arguments, for example "--run-min 180"
RPM=@@RPM@@                      # per-agent model request budget enforced by the local LLM governor
REGION=@@REGION@@
NODE_VERSION=@@NODE_VERSION@@
OPENCODE_VERSION=@@OPENCODE_VERSION@@
TAG=@@TAG@@

export HOME=/root PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin   # cloud-init runs without HOME
LOG=/var/log/larder-trial.log
exec > >(tee -a $LOG) 2>&1
echo "[$(date -u +%FT%TZ)] bootstrap start (tag=$TAG)"

shutdown -h +"$MAX_MIN" "larder dead-man switch" || true

finish() {  # upload whatever exists, scrub credentials, terminate
  echo "[$(date -u +%FT%TZ)] finishing: $1"
  echo "$1" > /home/bench/STATUS 2>/dev/null || true
  aws s3 cp $LOG "$S3/$TAG/bootstrap.log" --region $REGION --only-show-errors || true
  aws s3 cp /home/bench/STATUS "$S3/$TAG/STATUS" --region $REGION --only-show-errors || true
  [ -f /home/bench/trial.out ] && aws s3 cp /home/bench/trial.out "$S3/$TAG/trial.out" --region $REGION --only-show-errors || true
  for n in $(aws ssm get-parameters-by-path --path "$PARAMS" --recursive --region $REGION --query 'Parameters[].Name' --output text 2>/dev/null); do
    aws ssm delete-parameter --name "$n" --region $REGION || true
  done
  shred -u /home/bench/.cx/profiles/* /home/bench/.config/opencode/model-api-key /home/agent/.cx/profiles/* /home/agent/.config/opencode/model-api-key /home/reviewer/.config/opencode/model-api-key 2>/dev/null || true
  sync; shutdown -h now
  exit 0
}

# ------------------------------------------------------------------ system setup
dnf install -y -q git tar gzip xz findutils procps-ng jq libatomic >/dev/null || finish "FAILED: dnf"
useradd -m -s /bin/bash bench || true
curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" -o /tmp/node.txz || finish "FAILED: node download"
tar -xJf /tmp/node.txz -C /usr/local --strip-components=1 || finish "FAILED: node extract"
node --version || finish "FAILED: node does not run"
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh >/dev/null || finish "FAILED: uv"
npm install -g --no-fund --no-audit "opencode-ai@$OPENCODE_VERSION" 2>&1 | tail -5
if ! command -v opencode >/dev/null; then
  echo "npm install failed; trying the official installer"
  curl -fsSL https://opencode.ai/install | bash 2>&1 | tail -5
  [ -x /root/.opencode/bin/opencode ] && install -m 755 /root/.opencode/bin/opencode /usr/local/bin/opencode
fi
command -v opencode >/dev/null || finish "FAILED: opencode"
su - bench -c 'curl -fsSL https://get.coralogix.dev/cli | sh' 2>&1 | tail -3
su - bench -c 'test -x ~/.local/bin/cx' || finish "FAILED: cx cli"
install -m 755 /home/bench/.local/bin/cx /usr/local/bin/cx                     # usable by the agent user too
mkdir -p /opt/uv-python && chown bench /opt/uv-python && chmod 755 /opt/uv-python   # world-readable managed Pythons
echo "[$(date -u +%FT%TZ)] tools: node $(node --version) uv $(uv --version | cut -d' ' -f2) opencode $(opencode --version 2>/dev/null) cx $(su - bench -c '~/.local/bin/cx --version' 2>/dev/null)"

# ------------------------------------------------------------------ code + credentials
mkdir -p /home/bench/larder /home/bench/.cx/profiles /home/bench/.config/opencode
aws s3 cp "$S3/bundle.tgz" /tmp/bundle.tgz --region $REGION --only-show-errors || finish "FAILED: bundle download"
tar -xzf /tmp/bundle.tgz -C /home/bench/larder || finish "FAILED: bundle extract"
getp() { aws ssm get-parameter --name "$PARAMS/$1" --with-decryption --region $REGION --query Parameter.Value --output text; }
for p in $(aws ssm get-parameters-by-path --path "$PARAMS/cx" --region $REGION --query 'Parameters[].Name' --output text); do
  getp "cx/$(basename $p)" > "/home/bench/.cx/profiles/$(basename $p).toml" || finish "FAILED: cx profile $p"
done
# per-trial accounts: replace the bundled accounts.env only if the parameter exists and is non-empty
if getp accounts.env > /tmp/accounts.env 2>/dev/null && [ -s /tmp/accounts.env ]; then mv /tmp/accounts.env /home/bench/larder/harness/config/accounts.env; fi
case "$CONDITION" in
  basic) CK=BASIC ;;
  slo-guided) CK=SLO ;;
  *) finish "FAILED: unknown condition $CONDITION" ;;
esac
printf 'default_profile = "%s"\ndefault_output_format = "text"\n' "$(grep ^LARDER_${CK}_OPERATOR_PROFILE /home/bench/larder/harness/config/accounts.env | cut -d= -f2)" > /home/bench/.cx/config.toml
getp opencode/model-api-key > /home/bench/.config/opencode/model-api-key || finish "FAILED: model API key"
getp opencode/opencode.json > /home/bench/.config/opencode/opencode.json || finish "FAILED: opencode config"
chmod 600 /home/bench/.cx/profiles/* /home/bench/.config/opencode/model-api-key
chown -R bench:bench /home/bench

# ------------------------------------------------------------------ separate OS user for the evaluated agent
# The agent can read/write the workspace (shared group) but not /home/bench: operator keys, harness, admin token.
groupadd -f larder; useradd -m -s /bin/bash agent || true
usermod -aG larder bench; usermod -aG larder agent
chmod 700 /home/bench
mkdir -p /srv/larder-workspaces && chown bench:larder /srv/larder-workspaces && chmod 2775 /srv/larder-workspaces
sed -i 's#^LARDER_WORKSPACE_ROOT=.*#LARDER_WORKSPACE_ROOT=/srv/larder-workspaces#' /home/bench/larder/harness/config/accounts.env
echo "bench ALL=(agent) NOPASSWD: ALL" > /etc/sudoers.d/larder-agent && chmod 440 /etc/sudoers.d/larder-agent
AGP=$(grep "^LARDER_${CK}_AGENT_PROFILE" /home/bench/larder/harness/config/accounts.env | cut -d= -f2)
mkdir -p /home/agent/.cx/profiles /home/agent/.config/opencode
cp "/home/bench/.cx/profiles/$AGP.toml" /home/agent/.cx/profiles/
printf 'default_profile = "%s"\ndefault_output_format = "text"\n' "$AGP" > /home/agent/.cx/config.toml
cp /home/bench/.config/opencode/model-api-key /home/agent/.config/opencode/model-api-key
PROV=${AGENT_MODEL%%/*}
jq --arg base "http://127.0.0.1:18080/v1" --arg key "{file:/home/agent/.config/opencode/model-api-key}" --arg prov "$PROV" \
  '.provider[$prov].options.baseURL=$base | .provider[$prov].options.apiKey=$key' \
  /home/bench/.config/opencode/opencode.json > /home/agent/.config/opencode/opencode.json || finish "FAILED: agent opencode config"
chown -R agent:agent /home/agent && chmod 700 /home/agent && chmod 600 /home/agent/.config/opencode/model-api-key /home/agent/.cx/profiles/*
# reviewer: third OS user. Works on review snapshots (shared group dir), reads the workspace's dependencies,
# cannot write the coder's tree and cannot read /home/bench (hidden tests, evaluator docs, credentials).
groupadd -f lreview; useradd -m -s /bin/bash reviewer || true
usermod -aG lreview bench; usermod -aG lreview reviewer
mkdir -p /srv/larder-reviews && chown bench:lreview /srv/larder-reviews && chmod 2775 /srv/larder-reviews
echo "bench ALL=(reviewer) NOPASSWD: ALL" > /etc/sudoers.d/larder-reviewer && chmod 440 /etc/sudoers.d/larder-reviewer
mkdir -p /home/reviewer/.config/opencode
cp /home/agent/.config/opencode/opencode.json /home/reviewer/.config/opencode/opencode.json
cp /home/bench/.config/opencode/model-api-key /home/reviewer/.config/opencode/model-api-key
sed -i 's#/home/agent/#/home/reviewer/#' /home/reviewer/.config/opencode/opencode.json
chown -R reviewer:reviewer /home/reviewer && chmod 700 /home/reviewer && chmod 600 /home/reviewer/.config/opencode/model-api-key
su - bench -c "git config --global safe.directory '*'"; su - agent -c "git config --global safe.directory '*'"; su - reviewer -c "git config --global safe.directory '*'"
# the model endpoint is the provider's baseURL in opencode.json (LARDER_MODEL_BASE_URL on the operator machine)
UPSTREAM=$(jq -r --arg prov "$PROV" '.provider[$prov].options.baseURL' /home/bench/.config/opencode/opencode.json | sed 's#/v1/*$##')
[ -n "$UPSTREAM" ] && [ "$UPSTREAM" != "null" ] || finish "FAILED: no baseURL for provider $PROV in opencode.json"

# ------------------------------------------------------------------ preflight
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Authorization: Bearer $(cat /home/bench/.config/opencode/model-api-key)" "$UPSTREAM/v1/models")
echo "[$(date -u +%FT%TZ)] model endpoint /v1/models: HTTP $code"
[ "$code" = "200" ] || finish "ABORTED: model endpoint not reachable from EC2 (HTTP $code)"

BENCH="cd /home/bench/larder && export PATH=/home/bench/.local/bin:/usr/local/bin:\$PATH UV_PYTHON_INSTALL_DIR=/opt/uv-python &&"
su - bench -c "$BENCH uv sync --frozen -q" || finish "FAILED: harness uv sync"
su - bench -c "$BENCH ./bench doctor" || echo "(doctor: this instance has only this condition's profiles, so the other condition reports MISSING)"

# ------------------------------------------------------------------ wait for the account to be free
now=$(date -u +%s); nb=$(date -u -d "$NOT_BEFORE" +%s)
if [ "$now" -lt "$nb" ]; then echo "[$(date -u +%FT%TZ)] waiting until $NOT_BEFORE"; sleep $((nb - now)); fi

# ------------------------------------------------------------------ the trial
OUT=/home/bench/trial.out
su - bench -c "$BENCH ./bench prepare --condition $CONDITION $PREPARE_ARGS" > $OUT 2>&1 || finish "FAILED: prepare"
RUN=$(grep -o '\[[0-9]\{8\}-[0-9]\{4\}-[a-z]*-[0-9a-f]\{4\}\] prepared' $OUT | tr -d '[]' | cut -d' ' -f1)
[ -n "$RUN" ] || finish "FAILED: no run id"
echo "[$(date -u +%FT%TZ)] run id $RUN"
WS=/srv/larder-workspaces/$RUN
chgrp -R larder "$WS" && chmod -R g+rwX "$WS" && find "$WS" -type d -exec chmod g+s {} +   # agent can edit
su - bench -c "$BENCH ./bench verify-isolation $RUN" >> $OUT 2>&1 || finish "FAILED: isolation check (run $RUN)"
AGENT_CMD="opencode run --auto -m $AGENT_MODEL --format json \"Read AGENT_INSTRUCTIONS.md and follow it. Keep working until ./larderctl time shows about 5 minutes remaining, then write REPORT.md and stop.\""
su - bench -c "$BENCH ./bench run $RUN --agent-user agent --agent-home /home/agent --llm-governor-rpm $RPM --llm-upstream $UPSTREAM --review-model $AGENT_MODEL --reviewer-user reviewer --reviewer-home /home/reviewer --review-snapshot-root /srv/larder-reviews --agent-cmd '$AGENT_CMD'" >> $OUT 2>&1
echo "[$(date -u +%FT%TZ)] trial finished (exit $?)"
su - bench -c "$BENCH ./bench collect $RUN"  >> $OUT 2>&1
su - bench -c "$BENCH ./bench evaluate $RUN" >> $OUT 2>&1
su - bench -c "$BENCH ./bench score $RUN"    >> $OUT 2>&1
su - bench -c "$BENCH ./bench teardown $RUN" >> $OUT 2>&1

# ------------------------------------------------------------------ results out
cd /home/bench/larder
tar -czf /tmp/$RUN.tgz runs/$RUN results/$RUN.json results/$RUN.md 2>/dev/null
aws s3 cp /tmp/$RUN.tgz "$S3/$TAG/$RUN.tgz" --region $REGION --only-show-errors
aws s3 cp results/$RUN.md "$S3/$TAG/$RUN.md" --region $REGION --only-show-errors || true
aws s3 cp $OUT "$S3/$TAG/trial.out" --region $REGION --only-show-errors || true
finish "DONE: run $RUN"
