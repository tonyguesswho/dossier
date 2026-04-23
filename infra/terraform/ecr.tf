# ----------------------------------------------------------------------------
# ECR — single repo for the backend Lambda container image. force_delete=true
# lets `terraform destroy` clean up images without manual intervention; fine
# for demo, never for prod.
# ----------------------------------------------------------------------------

resource "aws_ecr_repository" "backend" {
  name                 = "${var.project_name}-backend"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}
