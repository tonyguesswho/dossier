output "aws_region" {
  description = "AWS region everything was deployed to."
  value       = var.aws_region
}

output "lambda_function_url" {
  description = "Public HTTPS URL for the backend Lambda. Point the Vercel frontend at this."
  value       = aws_lambda_function_url.backend.function_url
}

output "lambda_function_name" {
  description = "Lambda function name — matches LAMBDA_FUNCTION_NAME env var."
  value       = aws_lambda_function.backend.function_name
}

output "ecr_repository_url" {
  description = "Push the backend container image here."
  value       = aws_ecr_repository.backend.repository_url
}

output "db_endpoint" {
  description = "RDS Postgres endpoint (host:port)."
  value       = aws_db_instance.main.endpoint
  sensitive   = true
}

output "db_connection_string" {
  description = "Full postgresql+psycopg:// URL — plug straight into psql or DATABASE_URL."
  value = format(
    "postgresql+psycopg://%s:%s@%s/%s?sslmode=require",
    aws_db_instance.main.username,
    coalesce(var.db_password, random_password.db.result),
    aws_db_instance.main.endpoint,
    aws_db_instance.main.db_name,
  )
  sensitive = true
}
