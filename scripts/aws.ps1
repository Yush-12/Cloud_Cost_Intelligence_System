# Prefer an installed CLI; fall back to the verified project-local extraction.
$awsCommand = Get-Command aws.exe -ErrorAction SilentlyContinue
$awsExecutable = if ($awsCommand) { $awsCommand.Source } else {
    Join-Path $PSScriptRoot '../.local-data/aws-cli/Amazon/AWSCLIV2/aws.exe'
}
if (-not (Test-Path -LiteralPath $awsExecutable)) {
    throw 'AWS CLI v2 was not found. Install it from https://aws.amazon.com/cli/.'
}
& $awsExecutable @args
exit $LASTEXITCODE
