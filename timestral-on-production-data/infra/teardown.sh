#!/bin/bash
# Delete everything tagged Project=$PROJECT_TAG (instance, volumes, security group) and the key pair,
# then list what is left: every line should be empty.
set -u
cd "$(dirname "$0")"
: "${AWS_REGION:?}" "${PROJECT_TAG:?}" "${KEY_NAME:?}"
F="Name=tag:Project,Values=$PROJECT_TAG"
LIVE="Name=instance-state-name,Values=pending,running,stopping,stopped"
ids=$(aws ec2 describe-instances --filters "$F" "$LIVE" --query 'Reservations[].Instances[].InstanceId' --output text)
[ -n "$ids" ] && aws ec2 terminate-instances --instance-ids $ids >/dev/null && echo "terminating $ids" && aws ec2 wait instance-terminated --instance-ids $ids && echo "terminated"
for v in $(aws ec2 describe-volumes --filters "$F" --query 'Volumes[].VolumeId' --output text); do aws ec2 delete-volume --volume-id "$v" && echo "deleted volume $v"; done
for sg in $(aws ec2 describe-security-groups --filters "$F" --query 'SecurityGroups[].GroupId' --output text); do aws ec2 delete-security-group --group-id "$sg" && echo "deleted sg $sg"; done
aws ec2 delete-key-pair --key-name "$KEY_NAME" >/dev/null && echo "deleted key pair"
rm -f "$KEY_NAME.pem" instance.env
echo "--- left over (all should be empty)"
echo "instances: $(aws ec2 describe-instances --filters "$F" "$LIVE" --query 'Reservations[].Instances[].InstanceId' --output text)"
echo "volumes:   $(aws ec2 describe-volumes --filters "$F" --query 'Volumes[].VolumeId' --output text)"
echo "sgs:       $(aws ec2 describe-security-groups --filters "$F" --query 'SecurityGroups[].GroupId' --output text)"
echo "keypair:   $(aws ec2 describe-key-pairs --filters Name=key-name,Values="$KEY_NAME" --query 'KeyPairs[].KeyName' --output text 2>/dev/null)"
