# -----------------------------------------------------------------------------
# AWS Amplify Hosting — frontend deploy parallel to the Vercel deploy.
#
#   - Provisions an Amplify app + main-branch resource that pulls from
#     github.com/<owner>/dossier via the GitHub OAuth token in
#     `var.github_oauth_token`. Amplify auto-builds on every push to `main`,
#     hosts SSR via Amplify-managed Lambda + CloudFront, and serves the
#     same Next.js app the Vercel deploy serves.
#   - Same NEXT_PUBLIC_* env vars as the Vercel project (separate map in
#     terraform.tfvars so a typo in one path can't accidentally edit the
#     Lambda backend's env_vars).
#
# Why parallel to Vercel rather than replacing it:
#   The capstone story benefits from showing the same Next.js build running
#   on two clouds. Also gives a fallback if either provider has an outage on
#   demo day. NEXT_PUBLIC_BACKEND_URL is the same on both — they share the
#   AWS Lambda backend.
#
# Disabled by default: leave var.github_oauth_token empty and all resources
# here use count = 0 — the rest of the stack applies as a no-op so backend-
# only accounts skip Amplify entirely.
# -----------------------------------------------------------------------------

locals {
  amplify_enabled = var.github_oauth_token != ""
}

resource "aws_amplify_app" "frontend" {
  count = local.amplify_enabled ? 1 : 0

  name        = "${var.project_name}-frontend"
  repository  = var.github_repo_url
  oauth_token = var.github_oauth_token

  # WEB_COMPUTE = SSR. Amplify provisions an internal Lambda + CloudFront for
  # the Next.js server-rendered routes. The default 'WEB' platform serves
  # build artifacts as static S3 — which 404s on every Next.js App Router
  # path because the .next/ output isn't a static export.
  platform = "WEB_COMPUTE"

  iam_service_role_arn = aws_iam_role.amplify[0].arn

  # Amplify's per-app YAML. Roots the build at frontend/ (monorepo: backend/
  # is also in this repo and Amplify must skip it). pnpm install + pnpm build
  # are the only commands needed; Amplify auto-detects Next.js SSR.
  # pnpm version pinned to 8.6.7 — matches the lockfileVersion 6.0 written
  # by local dev. `corepack prepare pnpm@latest` would install pnpm 10+
  # which rejects v6 lockfiles ("ERR_PNPM_LOCKFILE_BREAKING_CHANGE"). Bump
  # both sides together when you upgrade pnpm.
  build_spec = <<-EOT
    version: 1
    applications:
      - appRoot: frontend
        frontend:
          phases:
            preBuild:
              commands:
                - corepack enable
                - corepack prepare pnpm@8.6.7 --activate
                - pnpm install --frozen-lockfile
            build:
              commands:
                - pnpm build
          artifacts:
            baseDirectory: .next
            files:
              - '**/*'
          cache:
            paths:
              - node_modules/**/*
              - .next/cache/**/*
  EOT

  enable_branch_auto_build = true

  # NEXT_PUBLIC_* + Clerk vars injected at build time (NEXT_PUBLIC_* are
  # inlined into the JS bundle by the Next.js compiler).
  # Monorepo signal: AMPLIFY_MONOREPO_APP_ROOT tells WEB_COMPUTE platform
  # where to find package.json. Without it the build clones the repo, sees
  # backend/ + frontend/ + infra/ at root, can't read 'next' version, fails
  # with "CustomerError: Cannot read 'next' version in package.json".
  # Merged on top of user-provided vars so a typo upstream can't shadow it.
  environment_variables = merge(
    var.amplify_frontend_env_vars,
    { AMPLIFY_MONOREPO_APP_ROOT = "frontend" },
  )

  tags = {
    Project = var.project_name
    Env     = "demo"
  }
}

resource "aws_amplify_branch" "main" {
  count = local.amplify_enabled ? 1 : 0

  app_id      = aws_amplify_app.frontend[0].id
  branch_name = var.amplify_branch

  framework = "Next.js - SSR"
  stage     = "PRODUCTION"

  enable_auto_build = true
}

# IAM role Amplify assumes to manage build, deploy, and runtime resources.
resource "aws_iam_role" "amplify" {
  count = local.amplify_enabled ? 1 : 0
  name  = "${var.project_name}-amplify"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "amplify.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Project = var.project_name
  }
}

resource "aws_iam_role_policy_attachment" "amplify_admin" {
  count = local.amplify_enabled ? 1 : 0
  role  = aws_iam_role.amplify[0].name
  # AWS-managed policy that grants Amplify the cross-service permissions it
  # needs to provision its own SSR Lambda + S3 + CloudFront under the hood.
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess-Amplify"
}

output "amplify_app_id" {
  value       = local.amplify_enabled ? aws_amplify_app.frontend[0].id : null
  description = "Amplify app ID. Use with `aws amplify` CLI (e.g. start-job)."
}

output "amplify_default_domain" {
  value       = local.amplify_enabled ? aws_amplify_app.frontend[0].default_domain : null
  description = "Amplify-hosted domain of the form <app_id>.amplifyapp.com."
}

output "amplify_branch_url" {
  value       = local.amplify_enabled ? "https://${var.amplify_branch}.${aws_amplify_app.frontend[0].default_domain}" : null
  description = "Direct https URL for the production branch (after first build completes)."
}
