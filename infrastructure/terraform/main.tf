# ============================================================================
# DEVOPS-COPILOT AWS INFRASTRUCTURE
# ============================================================================
# Deploy with: terraform init && terraform apply
# ============================================================================

terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "devops-copilot-terraform-state"
    key            = "prod/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "devops-copilot"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ============================================================================
# DATA SOURCES
# ============================================================================

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

# ============================================================================
# VARIABLES
# ============================================================================

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment (prod, staging, dev)"
  type        = string
  default     = "prod"
}

variable "domain_name" {
  description = "Domain name for the application"
  type        = string
  default     = "devops-copilot.com"
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Anthropic API key"
  type        = string
  sensitive   = true
}

variable "stripe_secret_key" {
  description = "Stripe secret key"
  type        = string
  sensitive   = true
  default     = ""
}

# ============================================================================
# VPC
# ============================================================================

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"

  name = "devops-copilot-${var.environment}"
  cidr = "10.0.0.0/16"

  azs             = slice(data.aws_availability_zones.available.names, 0, 3)
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway     = true
  single_nat_gateway     = var.environment != "prod"
  enable_dns_hostnames   = true
  enable_dns_support     = true

  tags = {
    Environment = var.environment
  }
}

# ============================================================================
# SECURITY GROUPS
# ============================================================================

resource "aws_security_group" "alb" {
  name        = "devops-copilot-alb-${var.environment}"
  description = "Security group for ALB"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ecs" {
  name        = "devops-copilot-ecs-${var.environment}"
  description = "Security group for ECS tasks"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  name        = "devops-copilot-rds-${var.environment}"
  description = "Security group for RDS"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "redis" {
  name        = "devops-copilot-redis-${var.environment}"
  description = "Security group for Redis"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ============================================================================
# RDS POSTGRESQL
# ============================================================================

resource "aws_db_subnet_group" "main" {
  name       = "devops-copilot-${var.environment}"
  subnet_ids = module.vpc.private_subnets

  tags = {
    Name = "DevOps-Copilot DB Subnet Group"
  }
}

resource "aws_db_instance" "main" {
  identifier = "devops-copilot-${var.environment}"

  engine               = "postgres"
  engine_version       = "15.4"
  instance_class       = var.environment == "prod" ? "db.r6g.large" : "db.t3.medium"
  allocated_storage    = 100
  max_allocated_storage = 500
  storage_encrypted    = true

  db_name  = "devcopilot"
  username = "devcopilot"
  password = var.db_password

  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name

  backup_retention_period = var.environment == "prod" ? 30 : 7
  backup_window          = "03:00-04:00"
  maintenance_window     = "Mon:04:00-Mon:05:00"

  multi_az               = var.environment == "prod"
  deletion_protection    = var.environment == "prod"
  skip_final_snapshot    = var.environment != "prod"
  final_snapshot_identifier = var.environment == "prod" ? "devops-copilot-final" : null

  performance_insights_enabled = true

  tags = {
    Name = "DevOps-Copilot Database"
  }
}

# ============================================================================
# ELASTICACHE REDIS
# ============================================================================

resource "aws_elasticache_subnet_group" "main" {
  name       = "devops-copilot-${var.environment}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "devops-copilot-${var.environment}"
  description          = "Redis cluster for DevOps-Copilot"

  node_type            = var.environment == "prod" ? "cache.r6g.large" : "cache.t3.medium"
  num_cache_clusters   = var.environment == "prod" ? 2 : 1
  port                 = 6379

  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  automatic_failover_enabled = var.environment == "prod"

  snapshot_retention_limit = var.environment == "prod" ? 7 : 1
  snapshot_window         = "02:00-03:00"

  tags = {
    Name = "DevOps-Copilot Redis"
  }
}

# ============================================================================
# ECR REPOSITORY
# ============================================================================

resource "aws_ecr_repository" "api" {
  name                 = "devops-copilot-api"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# ============================================================================
# ECS CLUSTER
# ============================================================================

resource "aws_ecs_cluster" "main" {
  name = "devops-copilot-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    base              = 1
    weight            = 100
    capacity_provider = "FARGATE"
  }
}

# ============================================================================
# IAM ROLES
# ============================================================================

resource "aws_iam_role" "ecs_task_execution" {
  name = "devops-copilot-ecs-execution-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name = "secrets-access"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
        ]
        Resource = [
          aws_secretsmanager_secret.app_secrets.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role" "ecs_task" {
  name = "devops-copilot-ecs-task-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task" {
  name = "task-permissions"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = [
          "${aws_s3_bucket.artifacts.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [
          "${aws_cloudwatch_log_group.ecs.arn}:*"
        ]
      }
    ]
  })
}

# ============================================================================
# CLOUDWATCH LOG GROUP
# ============================================================================

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/devops-copilot-${var.environment}"
  retention_in_days = 30
}

# ============================================================================
# ECS TASK DEFINITION
# ============================================================================

resource "aws_ecs_task_definition" "api" {
  family                   = "devops-copilot-api-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.environment == "prod" ? "1024" : "512"
  memory                   = var.environment == "prod" ? "2048" : "1024"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "api"
      image = "${aws_ecr_repository.api.repository_url}:latest"

      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        }
      ]

      # Cleartext only. Anything with a credential in it goes in `secrets` below:
      # this array is returned verbatim by ecs:DescribeTaskDefinition and shows up
      # in the console, the CLI and CloudTrail.
      environment = [
        { name = "ENVIRONMENT", value = var.environment },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "COPILOT_REDIS_URL", value = "redis://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0" },
        # Local state dir (matches the image); durable state is RDS/ElastiCache.
        { name = "COPILOT_STATE_DIR", value = "/app/data" },
        { name = "S3_BUCKET", value = aws_s3_bucket.artifacts.bucket },
        { name = "SERVICE_NAME", value = "devops-copilot" },
        { name = "SERVICE_VERSION", value = "1.0.0" },
      ]

      secrets = [
        {
          # Was in `environment` with the RDS master password inline.
          name      = "COPILOT_DATABASE_URL"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:COPILOT_DATABASE_URL::"
        },
        {
          name      = "ANTHROPIC_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:ANTHROPIC_API_KEY::"
        },
        {
          name      = "STRIPE_SECRET_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:STRIPE_SECRET_KEY::"
        },
        {
          # The app reads COPILOT_SECRET_KEY (and refuses to start in production
          # without it); the Secrets Manager JSON key stays SECRET_KEY.
          name      = "COPILOT_SECRET_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:SECRET_KEY::"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "api"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])
}

# ============================================================================
# ECS SERVICE
# ============================================================================

resource "aws_ecs_service" "api" {
  name            = "devops-copilot-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.environment == "prod" ? 3 : 1

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 100
    base              = 1
  }

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.https]
}

# ============================================================================
# APPLICATION LOAD BALANCER
# ============================================================================

resource "aws_lb" "main" {
  name               = "devops-copilot-${var.environment}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = module.vpc.public_subnets

  enable_deletion_protection = var.environment == "prod"
}

resource "aws_lb_target_group" "api" {
  name        = "devops-copilot-api-${var.environment}"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = module.vpc.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    path                = "/health"
    matcher             = "200"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.main.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# ============================================================================
# SSL CERTIFICATE
# ============================================================================

resource "aws_acm_certificate" "main" {
  domain_name               = "api.${var.domain_name}"
  subject_alternative_names = ["*.${var.domain_name}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# ============================================================================
# S3 BUCKETS
# ============================================================================

resource "aws_s3_bucket" "artifacts" {
  bucket = "devops-copilot-artifacts-${var.environment}-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ============================================================================
# SECRETS MANAGER
# ============================================================================

resource "aws_secretsmanager_secret" "app_secrets" {
  name = "devops-copilot/${var.environment}/app-secrets"
}

resource "aws_secretsmanager_secret_version" "app_secrets" {
  secret_id = aws_secretsmanager_secret.app_secrets.id
  secret_string = jsonencode({
    ANTHROPIC_API_KEY = var.anthropic_api_key
    STRIPE_SECRET_KEY = var.stripe_secret_key
    # COPILOT_SECRET_KEY keys the Fernet secret vault, which requires exactly 32
    # base64-encoded bytes — the raw 64-char random_password below is rejected at
    # runtime. Derived deterministically from it, so applying this does not
    # rotate the key (rotation would make stored secrets unreadable).
    SECRET_KEY = base64encode(substr(random_password.secret_key.result, 0, 32))
    # Embeds the RDS master password, so it belongs here and is injected through
    # the task definition's `secrets` block — never `environment`, which every
    # principal with ecs:DescribeTaskDefinition can read in cleartext.
    COPILOT_DATABASE_URL = "postgresql://${aws_db_instance.main.username}:${var.db_password}@${aws_db_instance.main.endpoint}/${aws_db_instance.main.db_name}"
  })
}

resource "random_password" "secret_key" {
  length  = 64
  special = true
}

# ============================================================================
# OUTPUTS
# ============================================================================

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "rds_endpoint" {
  value     = aws_db_instance.main.endpoint
  sensitive = true
}

output "redis_endpoint" {
  value     = aws_elasticache_replication_group.main.primary_endpoint_address
  sensitive = true
}
