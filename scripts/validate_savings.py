import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

# Ensure repository root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db_utils import scan_all
from src.aws_clients import get_table, REGION

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("validate_savings")

AUDIT_TABLE_NAME = os.getenv("AUDIT_TABLE", "OptimizationAudit")


def validate_savings():
    logger.info("=" * 50)
    logger.info("📊 Cost Attribution Validation")
    logger.info("=" * 50)

    # Pull all actioned audit records (paginated)
    try:
        audit_table = get_table(AUDIT_TABLE_NAME)
        items = scan_all(audit_table, Attr("status").eq("actioned"))
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            logger.error(f"Table '{AUDIT_TABLE_NAME}' not found. Run 'python scripts/setup_aws.py' first.")
            return
        logger.error(f"Error accessing '{AUDIT_TABLE_NAME}': {e}")
        return

    if not items:
        logger.info("No actioned records found — run optimization_engine first")
        return

    # System estimated savings
    system_estimate = sum(float(i.get("estimated_saving_usd", 0)) for i in items)
    logger.info(f"System estimated savings:  ${system_estimate:.4f}/hr")
    logger.info(f"Projected daily:           ${system_estimate * 24:.4f}/day")
    logger.info(f"Projected monthly:         ${system_estimate * 24 * 30:.2f}/month")

    # Try to pull real Cost Explorer delta if activated
    try:
        ce = boto3.client('ce', region_name='us-east-1')
        now = datetime.now(timezone.utc)

        # Compare this week vs last week
        this_week_end   = now.strftime("%Y-%m-%d")
        this_week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        last_week_start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
        last_week_end   = this_week_start

        this_week = ce.get_cost_and_usage(
            TimePeriod={"Start": this_week_start, "End": this_week_end},
            Granularity="WEEKLY",
            Metrics=["BlendedCost"]
        )
        last_week = ce.get_cost_and_usage(
            TimePeriod={"Start": last_week_start, "End": last_week_end},
            Granularity="WEEKLY",
            Metrics=["BlendedCost"]
        )

        this_cost = float(this_week["ResultsByTime"][0]["Total"]["BlendedCost"]["Amount"])
        last_cost = float(last_week["ResultsByTime"][0]["Total"]["BlendedCost"]["Amount"])
        real_delta = last_cost - this_cost

        logger.info("Real AWS Cost Explorer delta:")
        logger.info(f"  Last week cost:   ${last_cost:.4f}")
        logger.info(f"  This week cost:   ${this_cost:.4f}")
        logger.info(f"  Actual saving:    ${real_delta:.4f}")

        if real_delta > 0:
            accuracy = min(100, (real_delta / max(system_estimate * 24 * 7, 0.0001)) * 100)
            logger.info(f"Attribution accuracy: {accuracy:.1f}%")
        else:
            logger.warning("No measurable real-world saving yet — need more collector runtime")

    except Exception as e:
        if "OptInRequired" in str(e) or "SubscriptionRequired" in str(e):
            logger.warning("Cost Explorer not activated yet.")
            logger.info("Go to: AWS Console → Billing → Cost Explorer → Enable")
            logger.info("Re-run this validator tomorrow once it activates.")
        else:
            logger.warning(f"Cost Explorer check: {e}")

    logger.info("=" * 50)
    logger.info("Action Breakdown:")
    by_action = {}
    for item in items:
        action = item.get("action_taken", "unknown")
        saving = float(item.get("estimated_saving_usd", 0))
        by_action[action] = by_action.get(action, 0) + saving

    for action, saving in by_action.items():
        logger.info(f"  {action:<35} ${saving:.4f}/hr")

    logger.info("=" * 50)


if __name__ == "__main__":
    validate_savings()
