"""Launch one unattended Larder trial on a self-terminating EC2 instance.

    uv run python -m harness.ec2.launch --condition basic --not-before <UTC ISO time>
    uv run python -m harness.ec2.launch status  --tag <tag>          # instance state + uploaded files
    uv run python -m harness.ec2.launch fetch   --tag <tag>          # download results into runs/ and results/
    uv run python -m harness.ec2.launch cleanup                      # remove the roles, instance profile, security group, schedules, launch templates and leftover parameters

What `launch` creates (all tagged project=larder-slo-benchmark):
  * IAM role + instance profile `larder-bench-runner`: SSM core, read the bundle / write results under the
    S3 prefix, read+delete the SSM parameters under /larder-bench/<tag>/
  * security group `larder-bench-runner` in the default VPC, no inbound rules
  * SSM SecureString parameters with the condition's cx profiles, the opencode provider config and the
    model API key (deleted by the instance when it finishes)
  * one EC2 instance (shutdown behaviour = terminate, gp3 root deleted on termination, IMDSv2 only)
The instance runs harness/ec2/bootstrap.sh and terminates itself; a dead-man timer caps its lifetime.
Credentials never touch the repository or the S3 bundle.

Settings come from the environment (see .env.example): AWS_PROFILE, AWS_REGION, LARDER_RESULTS_S3,
LARDER_MODEL, LARDER_MODEL_BASE_URL, LARDER_OPENCODE_CONFIG and LARDER_MODEL_KEY_FILE.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "larder-bench-runner"
TAGS = [{"Key": "project", "Value": "larder-slo-benchmark"}]
OPENCODE_CONFIG = Path(os.environ.get("LARDER_OPENCODE_CONFIG") or "~/.config/opencode/opencode.json").expanduser()
MODEL_KEY_FILE = Path(os.environ.get("LARDER_MODEL_KEY_FILE") or "~/.config/opencode/model-api-key").expanduser()


def aws(args, profile, region, json_out=True, check=True, input_=None):
    cmd = ["aws", *args, *(["--profile", profile] if profile else []), "--region", region] + (["--output", "json"] if json_out else [])
    p = subprocess.run(cmd, capture_output=True, text=True, input=input_)
    if check and p.returncode != 0:
        raise SystemExit(f"aws {' '.join(args[:2])} failed: {p.stderr.strip()[-600:]}")
    if json_out and p.stdout.strip():
        return json.loads(p.stdout)
    return p


def cfg_value(key):
    for ln in (ROOT / "harness" / "config" / "accounts.env").read_text().splitlines():
        if ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip()
    raise SystemExit(f"{key} missing from accounts.env")


def ensure_role(a, bucket, prefix):
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
                                                     "Action": "sts:AssumeRole"}]}
    exists = aws(["iam", "get-role", "--role-name", NAME], a.aws_profile, a.region, check=False)
    if isinstance(exists, subprocess.CompletedProcess) or not exists:
        aws(["iam", "create-role", "--role-name", NAME, "--assume-role-policy-document", json.dumps(trust),
             "--tags", json.dumps(TAGS)], a.aws_profile, a.region)
        aws(["iam", "attach-role-policy", "--role-name", NAME, "--policy-arn",
             "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"], a.aws_profile, a.region, json_out=False)
    account = aws(["sts", "get-caller-identity"], a.aws_profile, a.region)["Account"]
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": f"arn:aws:s3:::{bucket}/{prefix}/*"},
        {"Effect": "Allow", "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:DeleteParameter"],
         "Resource": f"arn:aws:ssm:{a.region}:{account}:parameter/larder-bench/*"},
        {"Effect": "Allow", "Action": "kms:Decrypt", "Resource": "*",
         "Condition": {"StringEquals": {"kms:ViaService": f"ssm.{a.region}.amazonaws.com"}}},
    ]}
    aws(["iam", "put-role-policy", "--role-name", NAME, "--policy-name", "larder-bench-runner",
         "--policy-document", json.dumps(policy)], a.aws_profile, a.region, json_out=False)
    ip = aws(["iam", "get-instance-profile", "--instance-profile-name", NAME], a.aws_profile, a.region, check=False)
    if isinstance(ip, subprocess.CompletedProcess) or not ip:
        aws(["iam", "create-instance-profile", "--instance-profile-name", NAME, "--tags", json.dumps(TAGS)], a.aws_profile, a.region)
        aws(["iam", "add-role-to-instance-profile", "--instance-profile-name", NAME, "--role-name", NAME],
            a.aws_profile, a.region, json_out=False)


def ensure_sg(a):
    vpc = aws(["ec2", "describe-vpcs", "--filters", "Name=isDefault,Values=true"], a.aws_profile, a.region)["Vpcs"][0]["VpcId"]
    sgs = aws(["ec2", "describe-security-groups", "--filters", f"Name=group-name,Values={NAME}", f"Name=vpc-id,Values={vpc}"],
              a.aws_profile, a.region)["SecurityGroups"]
    if sgs:
        return sgs[0]["GroupId"]
    sg = aws(["ec2", "create-security-group", "--group-name", NAME, "--vpc-id", vpc,
              "--description", "Larder benchmark runner: no inbound, outbound only",
              "--tag-specifications", json.dumps([{"ResourceType": "security-group", "Tags": TAGS}])],
             a.aws_profile, a.region)["GroupId"]
    return sg  # default SG rules: no inbound, all outbound


def trial_accounts(a) -> dict:
    key = {"basic": "BASIC", "slo-guided": "SLO"}[a.condition]
    return {"operator": a.operator_profile or cfg_value(f"LARDER_{key}_OPERATOR_PROFILE"),
            "agent": a.agent_profile or cfg_value(f"LARDER_{key}_AGENT_PROFILE"),
            "ingest_key_id": a.ingest_key_id or cfg_value(f"LARDER_{key}_INGEST_KEY_ID")}


def put_params(a, tag):
    base = f"/larder-bench/{tag}"
    key = {"basic": "BASIC", "slo-guided": "SLO"}[a.condition]
    acc = trial_accounts(a)
    profiles = [acc["operator"], acc["agent"]]
    env = (f"LARDER_{key}_OPERATOR_PROFILE={acc['operator']}\nLARDER_{key}_AGENT_PROFILE={acc['agent']}\n"
           f"LARDER_{key}_INGEST_KEY_ID={acc['ingest_key_id']}\nLARDER_WORKSPACE_ROOT=~/larder-workspaces\n")
    aws(["ssm", "put-parameter", "--name", f"{base}/accounts.env", "--type", "SecureString", "--overwrite", "--value", env],
        a.aws_profile, a.region, json_out=False)
    for prof in profiles:
        val = (Path.home() / ".cx" / "profiles" / f"{prof}.toml").read_text()
        aws(["ssm", "put-parameter", "--name", f"{base}/cx/{prof}", "--type", "SecureString", "--overwrite", "--value", val],
            a.aws_profile, a.region, json_out=False)
    oc = json.loads(OPENCODE_CONFIG.read_text())
    provider, model = a.model.split("/", 1)
    pconf = oc["provider"][provider]
    if os.environ.get("LARDER_MODEL_BASE_URL"):
        pconf.setdefault("options", {})["baseURL"] = os.environ["LARDER_MODEL_BASE_URL"].rstrip("/") + "/v1"
    pconf.setdefault("options", {})["apiKey"] = "{file:~/.config/opencode/model-api-key}"
    minimal = {"$schema": "https://opencode.ai/config.json", "model": a.model, "small_model": a.model,
               "provider": {provider: pconf}, "autoupdate": False, "share": "disabled", "mcp": {}}
    aws(["ssm", "put-parameter", "--name", f"{base}/opencode/opencode.json", "--type", "SecureString", "--overwrite",
         "--value", json.dumps(minimal)], a.aws_profile, a.region, json_out=False)
    keyfile = MODEL_KEY_FILE
    aws(["ssm", "put-parameter", "--name", f"{base}/opencode/model-api-key", "--type", "SecureString", "--overwrite",
         "--value", keyfile.read_text().strip()], a.aws_profile, a.region, json_out=False)
    return base


def bundle(a, s3):
    with tempfile.TemporaryDirectory() as td:
        tgz = Path(td) / "bundle.tgz"
        # .env stays out of the bundle; COPYFILE_DISABLE drops macOS ._ files
        subprocess.run(["tar", "-czf", str(tgz), "--exclude=./runs", "--exclude=./.git", "--exclude=*/.venv",
                        "--exclude=*/node_modules", "--exclude=*/__pycache__", "--exclude=./baseline/commerce/var",
                        "--exclude=./baseline/ops/data", "--exclude=./results", "--exclude=./.pytest_cache",
                        "--exclude=./.env", "--exclude=./key.txt", "--exclude=*.key", "--exclude=*.pem", "--exclude=._*", "."],
                       cwd=ROOT, check=True, env={**os.environ, "COPYFILE_DISABLE": "1"})
        aws(["s3", "cp", str(tgz), f"{s3}/bundle.tgz", "--only-show-errors"], a.aws_profile, a.region, json_out=False)


def render_user_data(a, s3, params, tag):
    script = (ROOT / "harness" / "ec2" / "bootstrap.sh").read_text()
    for k, v in {"S3_PREFIX": s3, "PARAM_PATH": params, "CONDITION": a.condition, "NOT_BEFORE": a.not_before,
                 "MAX_MIN": str(a.max_minutes), "AGENT_MODEL": a.model, "REGION": a.region,
                 "NODE_VERSION": a.node_version, "OPENCODE_VERSION": a.opencode_version, "TAG": tag,
                 "RPM": str(a.rpm), "PREPARE_ARGS": a.prepare_args or ""}.items():
        script = script.replace(f"@@{k}@@", v)
    assert "@@" not in script
    return script


def latest_ami(a):
    return aws(["ssm", "get-parameter", "--name", "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"],
               a.aws_profile, a.region)["Parameter"]["Value"]


def instance_tags(tag):
    return [{"ResourceType": "instance", "Tags": TAGS + [{"Key": "Name", "Value": f"{NAME}-{tag}"}, {"Key": "larder-tag", "Value": tag}]},
            {"ResourceType": "volume", "Tags": TAGS}]


SCHED_ROLE = "larder-bench-scheduler"


def ensure_scheduler_role(a):
    """Role EventBridge Scheduler assumes to call ec2:RunInstances with a trial's launch template."""
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "scheduler.amazonaws.com"},
                                                     "Action": "sts:AssumeRole"}]}
    r = aws(["iam", "get-role", "--role-name", SCHED_ROLE], a.aws_profile, a.region, check=False)
    if isinstance(r, subprocess.CompletedProcess) or not r:
        r = aws(["iam", "create-role", "--role-name", SCHED_ROLE, "--assume-role-policy-document", json.dumps(trust),
                 "--tags", json.dumps(TAGS)], a.aws_profile, a.region)
    account = aws(["sts", "get-caller-identity"], a.aws_profile, a.region)["Account"]
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["ec2:RunInstances", "ec2:CreateTags", "ec2:DescribeLaunchTemplates",
                                       "ec2:DescribeLaunchTemplateVersions"], "Resource": "*"},
        {"Effect": "Allow", "Action": "iam:PassRole", "Resource": f"arn:aws:iam::{account}:role/{NAME}"},
    ]}
    aws(["iam", "put-role-policy", "--role-name", SCHED_ROLE, "--policy-name", SCHED_ROLE,
         "--policy-document", json.dumps(policy)], a.aws_profile, a.region, json_out=False)
    return f"arn:aws:iam::{account}:role/{SCHED_ROLE}"


def cmd_schedule(a):
    """Like launch, but the instance is started later by EventBridge Scheduler (at --launch-at), so no operator
    machine or AWS session is needed at launch time. Creates SSM params + an EC2 launch template now."""
    tag = a.tag
    bucket, prefix = a.s3.removeprefix("s3://").split("/", 1)
    s3 = f"s3://{bucket}/{prefix}"
    print(f"[{tag}] IAM role + instance profile"); ensure_role(a, bucket, prefix)
    print(f"[{tag}] security group"); sg = ensure_sg(a)
    print(f"[{tag}] scheduler role"); role_arn = ensure_scheduler_role(a)
    if not a.skip_bundle:
        print(f"[{tag}] code bundle to {s3}/bundle.tgz"); bundle(a, s3)
    print(f"[{tag}] SSM parameters"); params = put_params(a, tag)
    import base64
    lt_name = f"larder-bench-{tag}"
    data = {"ImageId": latest_ami(a), "InstanceType": a.instance_type, "IamInstanceProfile": {"Name": NAME},
            "SecurityGroupIds": [sg], "InstanceInitiatedShutdownBehavior": "terminate",
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
            "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": a.disk_gb, "VolumeType": "gp3",
                                                                      "DeleteOnTermination": True, "Encrypted": True}}],
            "TagSpecifications": instance_tags(tag),
            "UserData": base64.b64encode(render_user_data(a, s3, params, tag).encode()).decode()}
    aws(["ec2", "delete-launch-template", "--launch-template-name", lt_name], a.aws_profile, a.region, check=False)
    aws(["ec2", "create-launch-template", "--launch-template-name", lt_name, "--launch-template-data", json.dumps(data),
         "--tag-specifications", json.dumps([{"ResourceType": "launch-template", "Tags": TAGS}])], a.aws_profile, a.region)
    print(f"[{tag}] schedule at {a.launch_at}")
    # ClientToken makes RunInstances idempotent, so scheduler retries can never start a second instance
    run_input = {"LaunchTemplate": {"LaunchTemplateName": lt_name}, "MinCount": 1, "MaxCount": 1, "ClientToken": tag[-64:]}
    target = {"Arn": "arn:aws:scheduler:::aws-sdk:ec2:runInstances", "RoleArn": role_arn, "Input": json.dumps(run_input),
              "RetryPolicy": {"MaximumRetryAttempts": 5, "MaximumEventAgeInSeconds": 1800}}
    aws(["scheduler", "delete-schedule", "--name", lt_name], a.aws_profile, a.region, check=False)
    import time
    for _ in range(12):  # a freshly created scheduler role takes a few seconds to become assumable
        r = aws(["scheduler", "create-schedule", "--name", lt_name, "--schedule-expression", f"at({a.launch_at.rstrip('Z')})",
                 "--schedule-expression-timezone", "UTC", "--flexible-time-window", '{"Mode": "OFF"}',
                 "--action-after-completion", "DELETE", "--target", json.dumps(target)], a.aws_profile, a.region, check=False)
        if not isinstance(r, subprocess.CompletedProcess):
            break
        if "assume the role" not in r.stderr:
            raise SystemExit(r.stderr[-600:])
        time.sleep(10)
    else:
        raise SystemExit("scheduler role never became assumable")
    result = {"tag": tag, "instance_id": None, "scheduled_launch": a.launch_at, "launch_template": lt_name,
              "condition": a.condition, **trial_accounts(a)}
    print(json.dumps({"tag": tag, "launch_at": a.launch_at, "not_before": a.not_before, "max_minutes": a.max_minutes}, indent=2))
    return result


def cmd_launch(a):
    tag = a.tag or f"{a.condition}-{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M}"
    bucket, prefix = a.s3.removeprefix("s3://").split("/", 1)
    s3 = f"s3://{bucket}/{prefix}"
    print(f"[{tag}] IAM role + instance profile"); ensure_role(a, bucket, prefix)
    print(f"[{tag}] security group"); sg = ensure_sg(a)
    print(f"[{tag}] SSM parameters"); params = put_params(a, tag)
    if not a.skip_bundle:
        print(f"[{tag}] code bundle to {s3}/bundle.tgz"); bundle(a, s3)
    script = render_user_data(a, s3, params, tag)
    ami = latest_ami(a)
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(script)
        ud = f.name
    import time
    for attempt in range(6):  # the new instance profile takes a few seconds to become usable
        r = aws(["ec2", "run-instances", "--image-id", ami, "--instance-type", a.instance_type, "--count", "1",
                 "--iam-instance-profile", f"Name={NAME}", "--security-group-ids", sg,
                 "--instance-initiated-shutdown-behavior", "terminate",
                 "--metadata-options", "HttpTokens=required,HttpEndpoint=enabled",
                 "--block-device-mappings", json.dumps([{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": a.disk_gb, "VolumeType": "gp3",
                                                                                          "DeleteOnTermination": True, "Encrypted": True}}]),
                 "--tag-specifications", json.dumps(instance_tags(tag)),
                 "--user-data", f"file://{ud}"], a.aws_profile, a.region, check=False)
        if not isinstance(r, subprocess.CompletedProcess):
            break
        if "Invalid IAM Instance Profile" not in r.stderr:
            raise SystemExit(r.stderr[-600:])
        time.sleep(10)
    else:
        raise SystemExit("instance profile never became usable")
    Path(ud).unlink()
    iid = r["Instances"][0]["InstanceId"]
    result = {"tag": tag, "instance_id": iid, "condition": a.condition, **trial_accounts(a)}
    print(json.dumps({"tag": tag, "instance_id": iid, "instance_type": a.instance_type, "not_before": a.not_before,
                      "max_minutes": a.max_minutes, "results": f"{s3}/{tag}/"}, indent=2))
    return result


def cmd_status(a):
    r = aws(["ec2", "describe-instances", "--filters", f"Name=tag:larder-tag,Values={a.tag}"], a.aws_profile, a.region)
    for res in r["Reservations"]:
        for i in res["Instances"]:
            print(i["InstanceId"], i["State"]["Name"], i.get("LaunchTime"))
    bucket, prefix = a.s3.removeprefix("s3://").split("/", 1)
    p = aws(["s3", "ls", f"s3://{bucket}/{prefix}/{a.tag}/"], a.aws_profile, a.region, json_out=False, check=False)
    print(p.stdout or "(no results uploaded yet)")


def cmd_fetch(a):
    bucket, prefix = a.s3.removeprefix("s3://").split("/", 1)
    dest = ROOT / "runs" / f"ec2-{a.tag}"
    dest.mkdir(parents=True, exist_ok=True)
    aws(["s3", "cp", f"s3://{bucket}/{prefix}/{a.tag}/", str(dest), "--recursive", "--only-show-errors"],
        a.aws_profile, a.region, json_out=False)
    for t in dest.glob("*.tgz"):
        subprocess.run(["tar", "-xzf", str(t), "-C", str(ROOT)], check=True)
    print(f"downloaded to {dest}; extracted runs/ and results/ from {[t.name for t in dest.glob('*.tgz')]}")


def cmd_cleanup(a):
    sgs = aws(["ec2", "describe-security-groups", "--filters", f"Name=group-name,Values={NAME}"], a.aws_profile, a.region)["SecurityGroups"]
    for sg in sgs:
        aws(["ec2", "delete-security-group", "--group-id", sg["GroupId"]], a.aws_profile, a.region, json_out=False, check=False)
    aws(["iam", "remove-role-from-instance-profile", "--instance-profile-name", NAME, "--role-name", NAME], a.aws_profile, a.region, json_out=False, check=False)
    aws(["iam", "delete-instance-profile", "--instance-profile-name", NAME], a.aws_profile, a.region, json_out=False, check=False)
    aws(["iam", "delete-role-policy", "--role-name", NAME, "--policy-name", "larder-bench-runner"], a.aws_profile, a.region, json_out=False, check=False)
    aws(["iam", "detach-role-policy", "--role-name", NAME, "--policy-arn", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"], a.aws_profile, a.region, json_out=False, check=False)
    aws(["iam", "delete-role", "--role-name", NAME], a.aws_profile, a.region, json_out=False, check=False)
    for sc in aws(["scheduler", "list-schedules", "--name-prefix", "larder-bench-"], a.aws_profile, a.region)["Schedules"]:
        aws(["scheduler", "delete-schedule", "--name", sc["Name"]], a.aws_profile, a.region, json_out=False, check=False)
    lts = aws(["ec2", "describe-launch-templates", "--filters", "Name=launch-template-name,Values=larder-bench-*"],
              a.aws_profile, a.region)["LaunchTemplates"]
    for lt in lts:
        aws(["ec2", "delete-launch-template", "--launch-template-id", lt["LaunchTemplateId"]], a.aws_profile, a.region, check=False)
    aws(["iam", "delete-role-policy", "--role-name", SCHED_ROLE, "--policy-name", SCHED_ROLE], a.aws_profile, a.region, json_out=False, check=False)
    aws(["iam", "delete-role", "--role-name", SCHED_ROLE], a.aws_profile, a.region, json_out=False, check=False)
    params = aws(["ssm", "get-parameters-by-path", "--path", "/larder-bench", "--recursive"], a.aws_profile, a.region)["Parameters"]
    for p in params:
        aws(["ssm", "delete-parameter", "--name", p["Name"]], a.aws_profile, a.region, json_out=False, check=False)
    print(f"removed security group(s) {len(sgs)}, role/instance profile {NAME}, {len(params)} parameters")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.ec2.launch")
    ap.add_argument("action", nargs="?", default="launch", choices=["launch", "schedule", "status", "fetch", "cleanup"])
    ap.add_argument("--aws-profile", default=os.environ.get("AWS_PROFILE"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION") or "eu-west-1")
    ap.add_argument("--s3", default=os.environ.get("LARDER_RESULTS_S3"), help="s3://<bucket>/<prefix> for the code bundle and results")
    ap.add_argument("--condition", choices=["basic", "slo-guided"], default="basic")
    ap.add_argument("--model", default=os.environ.get("LARDER_MODEL"), help="<provider>/<model> as named in opencode.json")
    ap.add_argument("--instance-type", default="c7i.2xlarge")
    ap.add_argument("--disk-gb", type=int, default=30)
    ap.add_argument("--not-before", default=None, help="UTC ISO time before which the instance must not touch the Coralogix account")
    ap.add_argument("--max-minutes", type=int, default=210, help="dead-man switch")
    ap.add_argument("--node-version", default="26.7.0")
    ap.add_argument("--opencode-version", default="1.18.35")
    ap.add_argument("--tag")
    ap.add_argument("--rpm", type=float, default=22, help="per-agent model request budget (LLM governor)")
    ap.add_argument("--prepare-args", default="", help='extra bench prepare args, for example "--run-min 180"')
    ap.add_argument("--operator-profile", help="override accounts.env for this trial")
    ap.add_argument("--agent-profile")
    ap.add_argument("--ingest-key-id")
    ap.add_argument("--skip-bundle", action="store_true", help="reuse the bundle already in S3 (batch launches)")
    ap.add_argument("--launch-at", help="schedule: UTC ISO time at which EventBridge Scheduler starts the instance")
    a = ap.parse_args(argv)
    if a.action in ("launch", "schedule") and not a.model:
        raise SystemExit("set LARDER_MODEL or pass --model <provider>/<model>")
    if a.action != "cleanup" and not a.s3:
        raise SystemExit("set LARDER_RESULTS_S3 or pass --s3 s3://<bucket>/<prefix>")
    if a.action == "schedule":
        return cmd_schedule(a)
    if a.action == "launch":
        a.not_before = a.not_before or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return cmd_launch(a)
    elif a.action == "status":
        cmd_status(a)
    elif a.action == "fetch":
        cmd_fetch(a)
    else:
        cmd_cleanup(a)


if __name__ == "__main__":
    main()
