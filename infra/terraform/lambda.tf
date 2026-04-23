# ----------------------------------------------------------------------------
# Lambda (container image) + Function URL.
#
#   - image_uri starts as the sentinel default in variables.tf so
#     `terraform apply` works before any real image has been pushed to ECR.
#     Flip to the real URI after `docker push` (see main.tf header block).
#   - timeout=900s is the Lambda max; investigations budget ≤4 min end-to-end
#     per PROJECT.md, so headroom is fine.
#   - memory_size=2048 keeps pgvector / LangGraph / openai SDK comfortable
#     while staying cheap on the free tier.
#   - DATABASE_URL is composed here (not left to the caller) so the one moving
#     part — the RDS password — never leaves Terraform state as plaintext in
#     anyone's shell history.
#   - Function URL: auth=NONE (Clerk JWT validation runs inside FastAPI),
#     CORS="*" for the demo. Tighten to the Vercel origin pre-panel if time
#     permits — TODO tracked below.
# ----------------------------------------------------------------------------

locals {
  composed_env_vars = merge(
    var.env_vars,
    {
      DATABASE_URL = format(
        "postgresql+psycopg://%s:%s@%s/%s?sslmode=require",
        aws_db_instance.main.username,
        coalesce(var.db_password, random_password.db.result),
        aws_db_instance.main.endpoint,
        aws_db_instance.main.db_name,
      )
      LAMBDA_FUNCTION_NAME = "${var.project_name}-backend"
    },
  )
}

resource "aws_lambda_function" "backend" {
  function_name = "${var.project_name}-backend"
  role          = aws_iam_role.lambda.arn

  package_type = "Image"
  image_uri    = var.container_image_uri

  timeout     = 900
  memory_size = 2048

  environment {
    variables = local.composed_env_vars
  }

  # Force resource ordering: the IAM policy for self-invoke and the ECR repo
  # must both exist before the Lambda is created. Terraform infers most edges
  # from `role`/`image_uri`, but the self-invoke policy and ECR repo have no
  # direct reference from here.
  depends_on = [
    aws_iam_role_policy.lambda_self_invoke,
    aws_iam_role_policy_attachment.lambda_basic_execution,
    aws_ecr_repository.backend,
  ]
}

resource "aws_lambda_function_url" "backend" {
  function_name      = aws_lambda_function.backend.function_name
  authorization_type = "NONE"

  cors {
    allow_credentials = true
    allow_origins     = ["*"]
    # TODO(pre-demo-panel): tighten allow_origins to the Vercel deployment URL
    # (e.g. ["https://dossier-<hash>.vercel.app"]). Wildcard is acceptable
    # because Clerk JWT verification inside FastAPI is the real auth gate.
    allow_methods  = ["*"]
    allow_headers  = ["*"]
    expose_headers = []
    max_age        = 3600
  }
}
