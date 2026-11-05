terraform {
  required_version = ">= 1.10"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 6.0" } }
}

# ------------------------------------------------------------------ inputs
# Set these through TF_VAR_* in .env (see .env.example).
variable "region" { type = string }
variable "aws_profile" {
  type    = string
  default = ""
}
# "test" = a small end-to-end rehearsal on small boxes; "real" = the week run.
variable "scale" {
  type    = string
  default = "test"
}
# "1" mounts an existing corpus from the bucket and skips the ingest. The
# ClickHouse TimeSeries server and its query client are not created on reuse:
# that engine has no stored corpus and is loaded fresh on every run.
variable "reuse" {
  type    = string
  default = "0"
}
# Your public IP in CIDR form, for direct access to the boxes.
variable "operator_cidr" { type = string }
# An arm64 Amazon Linux 2023 AMI in the region.
variable "ami_id" { type = string }
variable "vpc_id" { type = string }
variable "subnet_id" { type = string }
# First three octets of the private addresses to pin inside the subnet,
# e.g. "10.0.4". The subnet must contain <prefix>.11 to <prefix>.32.
variable "private_ip_prefix" { type = string }
# One bucket for code, coordination markers, the reusable corpus and Mimir's blocks.
variable "bucket" { type = string }
# Secrets Manager secret with the Coralogix {"send_key", "api_key", "domain"}.
variable "coralogix_secret_id" { type = string }
# Value of the Run tag on every instance; the coordinator terminates
# every running instance with this tag when the sweep is done.
variable "run_tag" {
  type    = string
  default = "hcm-bench"
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile != "" ? var.aws_profile : null
  default_tags {
    tags = {
      Project   = "high-cardinality-metrics-benchmark"
      ManagedBy = "terraform"
      Run       = var.run_tag
    }
  }
}

data "aws_caller_identity" "me" {}

locals {
  is_real = var.scale == "real"

  mimir_ips = [for i in range(11, 20) : "${var.private_ip_prefix}.${i}"]
  prom_ip   = "${var.private_ip_prefix}.21"
  ch_ip     = "${var.private_ip_prefix}.31"
  ts_ip     = "${var.private_ip_prefix}.32"

  mimir_count = local.is_real ? 9 : 1
  mimir_rf    = local.is_real ? 3 : 1
  cx_count    = local.is_real ? 4 : 1
  # ClickHouse TimeSeries is loaded fresh on every run, so a reuse run leaves it out.
  ts_count      = var.reuse == "1" ? 0 : 1
  harness_count = var.reuse == "1" ? 4 : 5

  ch_loader_type   = local.is_real ? "m7g.16xlarge" : "m7g.xlarge"
  pm_loader_type   = local.is_real ? "m7g.16xlarge" : "m7g.xlarge"
  ch_server_type   = local.is_real ? "m7g.8xlarge" : "m7g.large"
  prom_server_type = local.is_real ? "m7g.8xlarge" : "m7g.large"
  ts_server_type   = local.is_real ? "m7g.8xlarge" : "m7g.large"
  mimir_type       = local.is_real ? "m7g.xlarge" : "m7g.large"
  harness_type     = local.is_real ? "m7g.4xlarge" : "m7g.large"
  cx_type          = local.is_real ? "m7g.xlarge" : "m7g.large"

  ch_server_disk = local.is_real ? 600 : 60
  ch_loader_disk = local.is_real ? 600 : 60
  prom_disk      = local.is_real ? 300 : 40
  mimir_disk     = local.is_real ? 50 : 30

  join_members = join(",", [for ip in slice(local.mimir_ips, 0, local.mimir_count) : "${ip}:7946"])

  # Written to /etc/bench.env on every box before its bootstrap runs; stage.sh
  # reads the same file.
  bench_env = <<-EOT
    #!/bin/bash
    cat > /etc/bench.env <<'ENV'
    BUCKET=${var.bucket}
    REGION=${var.region}
    RUN_TAG=${var.run_tag}
    CX_SECRET_ID=${var.coralogix_secret_id}
    MIMIR_IPS="${join(" ", local.mimir_ips)}"
    PROM_IP=${local.prom_ip}
    CH_IP=${local.ch_ip}
    TS_IP=${local.ts_ip}
    ENV
    set -a; . /etc/bench.env; set +a
  EOT

  role_script = {
    ch   = file("${path.module}/user-data-ch.sh")
    ts   = file("${path.module}/user-data-ch-timeseries.sh")
    prom = file("${path.module}/user-data-prom.sh")
    work = file("${path.module}/user-data-worker.sh")
  }
}

# ---------------------------------------------------------------- IAM
resource "aws_iam_role" "node" {
  name = "${var.run_tag}-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "node" {
  name = "${var.run_tag}-node"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${var.bucket}"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "arn:aws:s3:::${var.bucket}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.me.account_id}:secret:${var.coralogix_secret_id}*"
      },
      {
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*"
      },
      {
        Effect    = "Allow"
        Action    = ["ec2:TerminateInstances"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:ResourceTag/Run" = var.run_tag } }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "node" {
  name = "${var.run_tag}-node"
  role = aws_iam_role.node.name
}

resource "aws_security_group" "bench" {
  name        = var.run_tag
  description = "metrics benchmark: loader and serving fleet"
  vpc_id      = var.vpc_id
  ingress {
    description = "intra-fleet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }
  ingress {
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    cidr_blocks = [var.operator_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --------------------------------------------------------------------- Mimir
resource "aws_instance" "mimir" {
  count                  = local.mimir_count
  ami                    = var.ami_id
  instance_type          = local.mimir_type
  subnet_id              = var.subnet_id
  private_ip             = local.mimir_ips[count.index]
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = local.mimir_disk
  }
  user_data = join("\n", [local.bench_env, templatefile("${path.module}/user-data-mimir.sh.tftpl", {
    bucket       = var.bucket
    region       = var.region
    join_members = local.join_members
    rf           = local.mimir_rf
    node_index   = count.index
    scale        = var.scale
    reuse        = var.reuse
    run_id       = replace(var.run_tag, "/[^0-9A-Za-z]/", "")
  })])
  tags = {
    Name  = "${var.run_tag}-mimir-${count.index}"
    Stack = "mimir"
  }
}

# --------------------------------------------------------------- ClickHouse
resource "aws_instance" "ch_server" {
  ami                    = var.ami_id
  instance_type          = local.ch_server_type
  subnet_id              = var.subnet_id
  private_ip             = local.ch_ip
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = local.ch_server_disk
  }
  user_data = join("\n", [local.bench_env, replace(replace(replace(replace(local.role_script.ch,
  "__ROLE__", "ch-server"), "__IDX__", "0"), "__SCALE__", var.scale), "__REUSE__", var.reuse)])
  tags = {
    Name  = "${var.run_tag}-ch-server"
    Stack = "clickhouse"
  }
}

resource "aws_instance" "ch_loader" {
  ami                    = var.ami_id
  instance_type          = local.ch_loader_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = local.ch_loader_disk
    throughput  = 500
    iops        = 8000
  }
  user_data = join("\n", [local.bench_env, replace(replace(replace(replace(local.role_script.ch,
  "__ROLE__", "ch-loader"), "__IDX__", "0"), "__SCALE__", var.scale), "__REUSE__", var.reuse)])
  tags = {
    Name  = "${var.run_tag}-ch-loader"
    Stack = "clickhouse-loader"
  }
}

# ---------------------------------------------------- ClickHouse TimeSeries
# One serving node, the same size as the MergeTree server. It loads its own
# week with metricgen tsinsert, then serves PromQL on :8123/prometheus/api/v1.
resource "aws_instance" "ts_server" {
  count                  = local.ts_count
  ami                    = var.ami_id
  instance_type          = local.ts_server_type
  subnet_id              = var.subnet_id
  private_ip             = local.ts_ip
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = local.ch_server_disk
  }
  user_data = join("\n", [local.bench_env, replace(replace(replace(replace(local.role_script.ts,
  "__ROLE__", "ts-server"), "__IDX__", "0"), "__SCALE__", var.scale), "__REUSE__", var.reuse)])
  tags = {
    Name  = "${var.run_tag}-ts-server"
    Stack = "clickhouse-timeseries"
  }
}

# --------------------------------------------------------------- Prometheus
resource "aws_instance" "prom_server" {
  ami                    = var.ami_id
  instance_type          = local.prom_server_type
  subnet_id              = var.subnet_id
  private_ip             = local.prom_ip
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = local.prom_disk
  }
  user_data = join("\n", [local.bench_env, replace(replace(replace(replace(local.role_script.prom,
  "__ROLE__", "prom-server"), "__IDX__", "0"), "__SCALE__", var.scale), "__REUSE__", var.reuse)])
  tags = {
    Name  = "${var.run_tag}-prom-server"
    Stack = "prometheus"
  }
}

resource "aws_instance" "pm_loader" {
  ami                    = var.ami_id
  instance_type          = local.pm_loader_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = local.prom_disk
    throughput  = 500
    iops        = 8000
  }
  user_data = join("\n", [local.bench_env, replace(replace(replace(replace(local.role_script.prom,
  "__ROLE__", "pm-loader"), "__IDX__", "0"), "__SCALE__", var.scale), "__REUSE__", var.reuse)])
  tags = {
    Name  = "${var.run_tag}-pm-loader"
    Stack = "prometheus-loader"
  }
}

# ----------------------------------------------------------- Coralogix sender
resource "aws_instance" "cx_sender" {
  count                  = local.cx_count
  ami                    = var.ami_id
  instance_type          = local.cx_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 30
  }
  user_data = join("\n", [local.bench_env, replace(replace(replace(replace(local.role_script.work,
  "__ROLE__", "cx-sender"), "__IDX__", tostring(count.index)), "__SCALE__", var.scale), "__REUSE__", var.reuse)])
  tags = {
    Name  = "${var.run_tag}-cx-sender-${count.index}"
    Stack = "coralogix"
  }
}

# --------------------------------------------------------------- Harness x5
# One query client per platform: 0 Coralogix, 1 Mimir, 2 Prometheus,
# 3 ClickHouse MergeTree, 4 ClickHouse TimeSeries. All in the same subnet as
# the self-hosted nodes.
resource "aws_instance" "harness" {
  count                  = local.harness_count
  ami                    = var.ami_id
  instance_type          = local.harness_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 30
  }
  user_data = join("\n", [local.bench_env, replace(replace(replace(replace(local.role_script.work,
  "__ROLE__", "harness"), "__IDX__", tostring(count.index)), "__SCALE__", var.scale), "__REUSE__", var.reuse)])
  tags = {
    Name  = "${var.run_tag}-harness-${count.index}"
    Stack = "harness"
  }
}

# ------------------------------------------------------------- Coordinator
resource "aws_instance" "coordinator" {
  ami                    = var.ami_id
  instance_type          = "t4g.small"
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.bench.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 20
  }
  user_data = join("\n", [local.bench_env, replace(replace(replace(replace(local.role_script.work,
  "__ROLE__", "coordinator"), "__IDX__", "0"), "__SCALE__", var.scale), "__REUSE__", var.reuse)])
  tags = {
    Name  = "${var.run_tag}-coordinator"
    Stack = "coordinator"
  }
}

output "ch_server" { value = local.ch_ip }
output "ts_server" { value = local.ts_count > 0 ? local.ts_ip : "" }
output "prom_server" { value = local.prom_ip }
output "mimir0" { value = local.mimir_ips[0] }
output "scale" { value = var.scale }
