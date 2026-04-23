# Dossier deploy Makefile.
#
# Self-documenting targets: every target has a `## description` on its
# declaration line; `make help` greps those into a pretty table.
#
# Design notes:
#   - Targets shell out to `cd` + command rather than setting a single
#     MAKEFLAGS cwd, because the apply/migrate steps cross repo sub-roots
#     (infra/terraform for state + outputs; backend for alembic).
#   - IMAGE_TAG defaults to the short git SHA so every build is traceable
#     back to a commit. ECR also gets a :latest tag, which is what
#     terraform.tfvars-example points Lambda at for the first boot.
#   - --platform linux/amd64 on build: M-series Macs default to arm64;
#     Lambda's base image (public.ecr.aws/lambda/python:3.12) is x86_64.
#     Cross-building locally is cheaper than a cold-start exec-format-error.

.PHONY: build push deploy migrate destroy logs help

AWS_REGION ?= us-east-1
IMAGE_TAG  ?= $(shell git rev-parse --short HEAD)
ECR_URI     = $(shell cd infra/terraform && terraform output -raw ecr_repository_url 2>/dev/null)
FUNC_NAME   = $(shell cd infra/terraform && terraform output -raw lambda_function_name 2>/dev/null)

# First non-empty line wins; `make` without a target prints help.
.DEFAULT_GOAL := help

build:  ## Build the Lambda container image (IMAGE_TAG defaults to git short SHA)
	docker build --platform linux/amd64 -t dossier-backend:$(IMAGE_TAG) backend/

push: build  ## Build then push to ECR (requires `terraform apply -target=aws_ecr_repository.backend` first)
	@test -n "$(ECR_URI)" || { \
	  echo "ECR_URI empty — run 'cd infra/terraform && terraform apply -target=aws_ecr_repository.backend' first"; \
	  exit 1; \
	}
	aws ecr get-login-password --region $(AWS_REGION) \
	  | docker login --username AWS --password-stdin $(ECR_URI)
	docker tag dossier-backend:$(IMAGE_TAG) $(ECR_URI):$(IMAGE_TAG)
	docker tag dossier-backend:$(IMAGE_TAG) $(ECR_URI):latest
	docker push $(ECR_URI):$(IMAGE_TAG)
	docker push $(ECR_URI):latest

deploy: push  ## Push image AND `terraform apply` with the new image URI
	cd infra/terraform && terraform apply -var="container_image_uri=$(ECR_URI):$(IMAGE_TAG)"

migrate:  ## Run `alembic upgrade head` against the deployed RDS instance
	@cd infra/terraform && \
	  DB_URL=$$(terraform output -raw db_connection_string) && \
	  cd ../../backend && \
	  DATABASE_URL="$$DB_URL" uv run alembic upgrade head

logs:  ## Tail CloudWatch logs for the Lambda function (Ctrl-C to stop)
	@test -n "$(FUNC_NAME)" || { echo "FUNC_NAME empty — terraform state missing?"; exit 1; }
	aws logs tail /aws/lambda/$(FUNC_NAME) --follow --region $(AWS_REGION)

destroy:  ## Tear down the entire AWS stack (ECR + RDS + Lambda + IAM)
	cd infra/terraform && terraform destroy

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' \
	  | sort
