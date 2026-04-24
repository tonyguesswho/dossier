# ----------------------------------------------------------------------------
# RDS Postgres 16 — free-tier eligible db.t3.micro, publicly accessible so
# Lambda (which has no VPC config on this demo stack) can reach it over the
# public internet. pgvector is available as an extension on Postgres 16
# default parameter group; migrations enable it.
#
# DEMO HYGIENE: publicly_accessible=true + 0.0.0.0/0 ingress is a deliberate
# trade-off for the 2-day panel. Tighten to the Lambda's egress IP range (or
# move to a VPC with RDS Proxy) post-demo — see PITFALLS.md / Plan 07.
# ----------------------------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  name        = "${var.project_name}-db-subnets"
  description = "Default-VPC subnets for ${var.project_name} RDS (demo)"
  subnet_ids  = data.aws_subnets.default.ids
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds"
  description = "Allow Postgres 5432 from anywhere (DEMO ONLY - tighten post-panel)"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Postgres 5432 from the public internet (demo trade-off)"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    # TODO(post-demo): restrict to Lambda egress IPs or move RDS into a VPC
    # with RDS Proxy. Tracked as a Phase 7 follow-up.
  }

  egress {
    description = "All egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# length=24 is plenty of entropy; special=false avoids URL-escaping landmines
# when composing DATABASE_URL inline.
resource "random_password" "db" {
  length  = 24
  special = false
}

resource "aws_db_instance" "main" {
  identifier = "${var.project_name}-db"

  engine         = "postgres"
  engine_version = "16.10"
  instance_class = "db.t3.micro"

  allocated_storage     = 20
  max_allocated_storage = 0 # no autoscaling for demo — 20GB is ample
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.project_name
  username = var.project_name
  password = coalesce(var.db_password, random_password.db.result)

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = true

  skip_final_snapshot     = true
  backup_retention_period = 0
  apply_immediately       = true
  deletion_protection     = false

  performance_insights_enabled = false
}
