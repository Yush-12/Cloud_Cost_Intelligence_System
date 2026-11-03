"""SDK provisioning is disabled pending bounded deployment and verified cleanup.

Use the reviewed Terraform workflow in infra/lambda-lab instead.
"""

def provision_lab(session, account, region='ap-south-1'):
    raise ValueError('SDK provisioning disabled; use a reviewed Terraform plan')


def teardown_lab(session, account, region='ap-south-1'):
    raise ValueError('SDK teardown disabled; inspect Terraform state and review a destroy plan')
