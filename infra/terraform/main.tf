# ----------------------------------------------------------------------------
# Dossier — minimal AWS stack for the 2-day demo.
#
# Bootstrap order (first-time deploy only):
#   1. terraform init
#   2. terraform apply -target=aws_ecr_repository.backend
#      (creates the ECR repo so we can push an image into it)
#   3. aws ecr get-login-password --region $(terraform output -raw aws_region) \
#        | docker login --username AWS --password-stdin \
#            "$(terraform output -raw ecr_repository_url | cut -d/ -f1)"
#      docker build -t "$(terraform output -raw ecr_repository_url):latest" ../../backend
#      docker push "$(terraform output -raw ecr_repository_url):latest"
#      (Plan 03-11 ships a Makefile recipe for this)
#   4. terraform apply \
#        -var="container_image_uri=$(terraform output -raw ecr_repository_url):latest"
#      (full stack, Lambda now references the real image)
#
# Subsequent deploys: docker push a new tag, then
#   terraform apply -var="container_image_uri=...:<tag>"
#
# Explicitly NOT in scope (deferred post-demo): VPC, NAT, RDS Proxy, API
# Gateway, S3, second Lambda, Secrets Manager, remote state backend, Route 53.
# ----------------------------------------------------------------------------

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
      Env       = "demo"
    }
  }
}

# Default VPC + its subnets — the stack rides on whatever AWS pre-created in
# this region. Good enough for demo; a dedicated VPC is Phase 7 work.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}
