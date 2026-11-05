#!/bin/bash
# Launch one g5.2xlarge (one NVIDIA A10G) for the foundation-model run, tagged Project=$PROJECT_TAG so teardown.sh
# finds everything. It powers off and terminates on its own after two hours (user-data.sh).
# Writes infra/instance.env (instance id, group id, key name, public IP) and infra/$KEY_NAME.pem.
# Env: AWS_REGION, GPU_AMI_ID, MY_IP, PROJECT_TAG, KEY_NAME (AWS_PROFILE if you use profiles).
set -eu
cd "$(dirname "$0")"
: "${AWS_REGION:?}" "${GPU_AMI_ID:?}" "${MY_IP:?}" "${PROJECT_TAG:?}" "${KEY_NAME:?}"
TAGS="Key=Project,Value=$PROJECT_TAG"
aws ec2 create-key-pair --key-name "$KEY_NAME" --tag-specifications "ResourceType=key-pair,Tags=[{$TAGS}]" \
  --query KeyMaterial --output text > "$KEY_NAME.pem"
chmod 600 "$KEY_NAME.pem"
SG=$(aws ec2 create-security-group --group-name "$PROJECT_TAG" --description "$PROJECT_TAG ssh" \
  --tag-specifications "ResourceType=security-group,Tags=[{$TAGS}]" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MY_IP/32" >/dev/null
ID=$(aws ec2 run-instances --image-id "$GPU_AMI_ID" --instance-type g5.2xlarge --key-name "$KEY_NAME" \
  --security-group-ids "$SG" --instance-initiated-shutdown-behavior terminate --user-data file://user-data.sh \
  --tag-specifications "ResourceType=instance,Tags=[{$TAGS}]" "ResourceType=volume,Tags=[{$TAGS}]" \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids "$ID"
IP=$(aws ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
printf 'INSTANCE_ID=%s\nGROUP_ID=%s\nKEY_NAME=%s\nINSTANCE_IP=%s\n' "$ID" "$SG" "$KEY_NAME" "$IP" > instance.env
echo "launched $ID at $IP"
