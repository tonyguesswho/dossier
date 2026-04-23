variable "project_name" {
  description = "Project name used as a prefix for all AWS resources."
  type        = string
  default     = "dossier"
}

variable "aws_region" {
  description = "AWS region for all resources. Keep consistent with Vercel's edge region for lower RDS latency."
  type        = string
  default     = "us-east-1"
}

variable "db_password" {
  description = "Optional Postgres master password. If unset, a random_password is generated."
  type        = string
  default     = null
  sensitive   = true
}

variable "container_image_uri" {
  description = <<-EOT
    Full ECR image URI for the Lambda container (e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com/dossier-backend:latest).

    First-time bootstrap: leave as the sentinel "public.ecr.aws/lambda/python:3.12" so the Lambda
    resource can be created before any real image is pushed. After `docker push`, re-apply with the
    real ECR URI via -var="container_image_uri=...".
  EOT
  type        = string
  default     = "public.ecr.aws/lambda/python:3.12"
}

variable "env_vars" {
  description = <<-EOT
    Secrets and runtime config injected into the Lambda environment. DATABASE_URL and
    LAMBDA_FUNCTION_NAME are composed by Terraform and merged on top of this map — do NOT
    set them here.

    Required keys (see terraform.tfvars.example):
      OPENROUTER_API_KEY, OPENAI_API_KEY,
      LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST,
      EXA_API_KEY, GITHUB_TOKEN, FIRECRAWL_API_KEY,
      NEWSAPI_API_KEY, CRUNCHBASE_API_KEY,
      CLERK_JWKS_URL
  EOT
  type        = map(string)
  default     = {}
  sensitive   = true
}
