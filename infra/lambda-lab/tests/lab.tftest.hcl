mock_provider "aws" {}

variables {
  aws_profile = "offline-test-only"
  account_id  = "123456789012"
}

run "bounded_lab_plan" {
  command = plan

  assert {
    condition     = aws_lambda_function.workload.memory_size == 128 && aws_lambda_function.workload.timeout == 3
    error_message = "Lab memory and timeout must remain bounded."
  }

  assert {
    condition     = aws_cloudwatch_log_group.workload.retention_in_days == 1
    error_message = "Lab logs must expire after one day."
  }

  assert {
    condition     = aws_lambda_function.workload.tags["AutoOptimize"] == "false"
    error_message = "Lab must not opt itself into autonomous mutations."
  }

  assert {
    condition     = aws_lambda_function.workload.function_name == "costintel-lab-workload"
    error_message = "Function name must agree with runner and IAM policy."
  }

  assert {
    condition     = jsondecode(aws_iam_role.workload.assume_role_policy).Statement[0].Principal.Service == "lambda.amazonaws.com"
    error_message = "Execution role must trust Lambda."
  }
}
