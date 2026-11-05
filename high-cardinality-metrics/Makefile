# Thin wrappers over the folder's scripts. Settings come from .env (copy .env.example).
-include .env
export

TF_VAR_region              = $(AWS_REGION)
TF_VAR_aws_profile         = $(AWS_PROFILE)
TF_VAR_bucket              = $(BENCH_BUCKET)
TF_VAR_coralogix_secret_id = $(CX_SECRET_ID)
TF_VAR_run_tag             = $(RUN_TAG)
TF_VAR_operator_cidr       = $(OPERATOR_CIDR)
TF_VAR_ami_id              = $(AMI_ID)
TF_VAR_vpc_id              = $(VPC_ID)
TF_VAR_subnet_id           = $(SUBNET_ID)
TF_VAR_private_ip_prefix   = $(PRIVATE_IP_PREFIX)

.PHONY: help setup run score clean

help: ## list the targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-6s %s\n", $$1, $$2}'

setup: ## create the bucket and secret, upload the code, terraform init
	scripts/setup.sh

run: ## provision, load and sweep every engine, download results (SCALE, REUSE)
	scripts/run.sh

score: ## per-cell summaries, results/page-data.json and results/figures.txt
	scripts/score.sh

clean: ## terraform destroy (keeps the bucket, corpus and secret)
	scripts/clean.sh
