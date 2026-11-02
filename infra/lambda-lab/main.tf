terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.7" }
  }
}

variable "aws_profile" {
  type        = string
  description = "Deployment profile; use an empty string for temporary environment credentials."
}

variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Provide the expected 12-digit AWS account ID."
  }
}

provider "aws" {
  region              = "ap-south-1"
  profile             = var.aws_profile == "" ? null : var.aws_profile
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "costintel-lab", Environment = "sandbox", ManagedBy = "terraform" }
  }
}

locals {
  name = "costintel-lab-workload"
}

data "archive_file" "workload" {
  type        = "zip"
  source_file = "${path.module}/workload.py"
  output_path = "${path.module}/workload.zip"
}

resource "aws_cloudwatch_log_group" "workload" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 1
}

resource "aws_iam_role" "workload" {
  name = "costintel-lab-execution"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "logs" {
  name = "costintel-lab-logs"
  role = aws_iam_role.workload.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.workload.arn}:*"
    }]
  })
}

resource "aws_lambda_function" "workload" {
  function_name    = local.name
  description      = "Manually invoked bounded cost intelligence sandbox"
  role             = aws_iam_role.workload.arn
  filename         = data.archive_file.workload.output_path
  source_code_hash = data.archive_file.workload.output_base64sha256
  handler          = "workload.handler"
  runtime          = "python3.12"
  architectures    = ["arm64"]
  memory_size      = 128
  timeout          = 3
  # Unreserved: a small positive reservation can fail in low-quota new accounts.
  # The runner is sequential; this is not an account-wide concurrency guarantee.
  reserved_concurrent_executions = -1
  tags                           = { CostIntelLab = "true", AutoOptimize = "false" }
  depends_on                     = [aws_iam_role_policy.logs]
}

output "function_arn" { value = aws_lambda_function.workload.arn }
output "function_name" { value = aws_lambda_function.workload.function_name }
output "region" { value = "ap-south-1" }
