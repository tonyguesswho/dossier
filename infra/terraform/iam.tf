# ----------------------------------------------------------------------------
# IAM — minimal execution role for the Lambda container.
#
# Scope:
#   - AWSLambdaBasicExecutionRole for CloudWatch log group/stream access.
#   - Inline policy granting lambda:InvokeFunction on THIS function's own ARN,
#     which powers the self-invoke dispatch swap in Plan 03-11 (POST
#     /investigations fires-and-forgets a second invocation of itself with
#     InvocationType=Event so the HTTP request can return 202 immediately).
# ----------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project_name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Self-invoke permission. The function ARN is stable once created, so we
# compose it from the known name rather than creating a Terraform cycle with
# aws_lambda_function.backend.arn.
data "aws_caller_identity" "current" {}

resource "aws_iam_role_policy" "lambda_self_invoke" {
  name = "${var.project_name}-lambda-self-invoke"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-backend"
      },
    ]
  })
}
