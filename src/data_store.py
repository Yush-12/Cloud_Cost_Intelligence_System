"""
src/data_store.py — High-fidelity In-Memory DynamoDB and AWS state store.

Provides:
  - InMemoryTable: Mimics boto3 DynamoDB Table interface (put_item, scan, update_item, get_item, delete_item, load)
  - Evaluates boto3.dynamodb.conditions (Attr, Equals, And, Or, etc.)
  - SimulatedEC2Client, SimulatedLambdaClient, SimulatedCloudWatchClient, SimulatedS3Client, SimulatedSTSClient
  - Thread-safe singleton storage across pipeline and API runs
"""

import copy
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("data_store")


# ─────────────────────────────────────────────────────────────────────────────
# Condition Evaluator for Boto3 DynamoDB Filter Expressions
# ─────────────────────────────────────────────────────────────────────────────
def _evaluate_condition(item: Dict[str, Any], condition: Any) -> bool:
    """Recursively evaluates a boto3 condition expression against an item."""
    if condition is None:
        return True

    # If it's a boto3 condition object
    if hasattr(condition, "get_expression"):
        expr = condition.get_expression()
        op = expr.get("operator", "").upper()
        values = expr.get("values", ())

        if op == "=":
            attr_name = values[0].name if hasattr(values[0], "name") else str(values[0])
            target_val = values[1] if len(values) > 1 else None
            item_val = item.get(attr_name)
            return str(item_val) == str(target_val) if item_val is not None else target_val is None

        elif op in ("<>", "!="):
            attr_name = values[0].name if hasattr(values[0], "name") else str(values[0])
            target_val = values[1] if len(values) > 1 else None
            item_val = item.get(attr_name)
            return str(item_val) != str(target_val)

        elif op == ">":
            attr_name = values[0].name if hasattr(values[0], "name") else str(values[0])
            target_val = values[1]
            try:
                return float(item.get(attr_name, 0)) > float(target_val)
            except (ValueError, TypeError):
                return False

        elif op == ">=":
            attr_name = values[0].name if hasattr(values[0], "name") else str(values[0])
            target_val = values[1]
            try:
                return float(item.get(attr_name, 0)) >= float(target_val)
            except (ValueError, TypeError):
                return False

        elif op == "<":
            attr_name = values[0].name if hasattr(values[0], "name") else str(values[0])
            target_val = values[1]
            try:
                return float(item.get(attr_name, 0)) < float(target_val)
            except (ValueError, TypeError):
                return False

        elif op == "<=":
            attr_name = values[0].name if hasattr(values[0], "name") else str(values[0])
            target_val = values[1]
            try:
                return float(item.get(attr_name, 0)) <= float(target_val)
            except (ValueError, TypeError):
                return False

        elif op == "AND":
            return all(_evaluate_condition(item, v) for v in values)

        elif op == "OR":
            return any(_evaluate_condition(item, v) for v in values)

        elif op == "NOT":
            return not _evaluate_condition(item, values[0])

        elif op == "IN":
            attr_name = values[0].name if hasattr(values[0], "name") else str(values[0])
            target_list = values[1:]
            return item.get(attr_name) in target_list

        elif op == "BEGINS_WITH":
            attr_name = values[0].name if hasattr(values[0], "name") else str(values[0])
            prefix = str(values[1])
            return str(item.get(attr_name, "")).startswith(prefix)

        elif op == "CONTAINS":
            attr_name = values[0].name if hasattr(values[0], "name") else str(values[0])
            sub = str(values[1])
            return sub in str(item.get(attr_name, ""))

    # Fallback for raw boolean / lambda
    if callable(condition):
        return bool(condition(item))

    return True


# ─────────────────────────────────────────────────────────────────────────────
# In-Memory DynamoDB Table
# ─────────────────────────────────────────────────────────────────────────────
class InMemoryTable:
    """Thread-safe in-memory replacement for boto3 DynamoDB Table."""

    def __init__(self, name: str):
        self.name = name
        self._lock = threading.RLock()
        self._items: List[Dict[str, Any]] = []

    def put_item(self, Item: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Store or overwrite an item."""
        with self._lock:
            new_item = copy.deepcopy(Item)
            # Check for existing item with matching primary/sort keys if known
            key_attrs = [
                ("anomaly_id", "timestamp"),
                ("action_id", "timestamp"),
                ("resource_id", "timestamp"),
            ]
            replaced = False
            for pkey, skey in key_attrs:
                if pkey in new_item and skey in new_item:
                    for idx, existing in enumerate(self._items):
                        if existing.get(pkey) == new_item.get(pkey) and existing.get(skey) == new_item.get(skey):
                            self._items[idx] = new_item
                            replaced = True
                            break
                    if replaced:
                        break

            if not replaced:
                self._items.append(new_item)

            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def scan(self, FilterExpression: Any = None, ExclusiveStartKey: Any = None, Limit: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        """Scan items, filtering by condition expression."""
        with self._lock:
            results = []
            for it in self._items:
                if FilterExpression is None or _evaluate_condition(it, FilterExpression):
                    results.append(copy.deepcopy(it))

            if Limit and len(results) > Limit:
                results = results[:Limit]

            return {
                "Items": results,
                "Count": len(results),
                "ScannedCount": len(self._items),
                "LastEvaluatedKey": None  # All items returned in one scan
            }

    def update_item(self, Key: Dict[str, Any], UpdateExpression: str = "",
                    ExpressionAttributeNames: Optional[Dict[str, str]] = None,
                    ExpressionAttributeValues: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Update fields on an existing item matching Key."""
        attr_names = ExpressionAttributeNames or {}
        attr_values = ExpressionAttributeValues or {}

        with self._lock:
            for item in self._items:
                match = all(item.get(k) == v for k, v in Key.items())
                if match:
                    # Parse basic SET expressions: SET #s = :s, action_taken = :a
                    if UpdateExpression.strip().startswith("SET"):
                        set_clause = UpdateExpression.strip()[3:].strip()
                        assignments = [a.strip() for a in set_clause.split(",") if a.strip()]
                        for assignment in assignments:
                            if "=" in assignment:
                                lhs, rhs = assignment.split("=", 1)
                                lhs = lhs.strip()
                                rhs = rhs.strip()
                                target_field = attr_names.get(lhs, lhs)
                                target_value = attr_values.get(rhs, rhs)
                                item[target_field] = target_value
                    return {"Attributes": copy.deepcopy(item), "ResponseMetadata": {"HTTPStatusCode": 200}}

            # If not found, create a new item with Key + updates
            new_item = copy.deepcopy(Key)
            if UpdateExpression.strip().startswith("SET"):
                set_clause = UpdateExpression.strip()[3:].strip()
                assignments = [a.strip() for a in set_clause.split(",") if a.strip()]
                for assignment in assignments:
                    if "=" in assignment:
                        lhs, rhs = assignment.split("=", 1)
                        lhs = lhs.strip()
                        rhs = rhs.strip()
                        target_field = attr_names.get(lhs, lhs)
                        target_value = attr_values.get(rhs, rhs)
                        new_item[target_field] = target_value
            self._items.append(new_item)
            return {"Attributes": copy.deepcopy(new_item), "ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_item(self, Key: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Retrieve a single item by key."""
        with self._lock:
            for item in self._items:
                if all(item.get(k) == v for k, v in Key.items()):
                    return {"Item": copy.deepcopy(item)}
            return {}

    def delete_item(self, Key: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Delete an item by key."""
        with self._lock:
            self._items = [
                it for it in self._items
                if not all(it.get(k) == v for k, v in Key.items())
            ]
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def load(self) -> None:
        """No-op matching boto3 Table.load()."""
        pass

    def wait_until_exists(self) -> None:
        """No-op matching boto3 Table.wait_until_exists()."""
        pass

    def clear(self) -> None:
        """Helper to reset table items."""
        with self._lock:
            self._items.clear()

    @property
    def item_count(self) -> int:
        with self._lock:
            return len(self._items)


# ─────────────────────────────────────────────────────────────────────────────
# Global In-Memory Table Registry
# ─────────────────────────────────────────────────────────────────────────────
_TABLE_REGISTRY: Dict[str, InMemoryTable] = {}
_REGISTRY_LOCK = threading.Lock()


def get_in_memory_table(table_name: str) -> InMemoryTable:
    """Get or create a named InMemoryTable singleton."""
    with _REGISTRY_LOCK:
        if table_name not in _TABLE_REGISTRY:
            _TABLE_REGISTRY[table_name] = InMemoryTable(table_name)
        return _TABLE_REGISTRY[table_name]


def reset_in_memory_store() -> None:
    """Clear all items from all tables (useful for tests)."""
    with _REGISTRY_LOCK:
        for tbl in _TABLE_REGISTRY.values():
            tbl.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Simulated AWS Clients
# ─────────────────────────────────────────────────────────────────────────────
class SimulatedDynamoDBClient:
    """Simulates low-level boto3 DynamoDB client for list_tables."""

    def list_tables(self, **kwargs) -> Dict[str, Any]:
        with _REGISTRY_LOCK:
            tables = list(_TABLE_REGISTRY.keys())
            if not tables:
                tables = ["CostTelemetry", "AnomalyEvents", "OptimizationAudit"]
            return {"TableNames": tables}


class SimulatedDynamoDBResource:
    """Simulates boto3 DynamoDB ServiceResource."""

    def Table(self, table_name: str) -> InMemoryTable:
        return get_in_memory_table(table_name)

    def create_table(self, TableName: str, **kwargs) -> InMemoryTable:
        return get_in_memory_table(TableName)
class SimulatedEC2Client:
    """Simulates AWS EC2 describe_instances, stop_instances, start_instances, and tagging."""

    def __init__(self):
        self._lock = threading.RLock()
        self._instances: List[Dict[str, Any]] = []
        self._initialized = False

    def _ensure_fleet(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    from src.synthetic_data import generate_ec2_fleet
                    self._instances = generate_ec2_fleet()
                    self._initialized = True

    def describe_instances(self, Filters: Optional[List[Dict[str, Any]]] = None,
                           InstanceIds: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
        self._ensure_fleet()
        with self._lock:
            results = []
            for inst in self._instances:
                # Filter by InstanceIds if provided
                if InstanceIds and inst["InstanceId"] not in InstanceIds:
                    continue

                # Filter by Filters if provided
                match = True
                if Filters:
                    for f in Filters:
                        name = f.get("Name")
                        values = f.get("Values", [])
                        if name == "instance-state-name":
                            if inst.get("State", {}).get("Name") not in values:
                                match = False
                                break
                        elif name == "tag-key":
                            inst_tag_keys = [t.get("Key") for t in inst.get("Tags", [])]
                            if not any(v in inst_tag_keys for v in values):
                                match = False
                                break
                if match:
                    results.append(copy.deepcopy(inst))

            return {"Reservations": [{"Instances": results}]}

    def stop_instances(self, InstanceIds: List[str], **kwargs) -> Dict[str, Any]:
        self._ensure_fleet()
        with self._lock:
            stopping = []
            for inst in self._instances:
                if inst["InstanceId"] in InstanceIds:
                    prev_state = inst.get("State", {}).get("Name", "running")
                    inst["State"] = {"Name": "stopped", "Code": 80}
                    stopping.append({
                        "InstanceId": inst["InstanceId"],
                        "CurrentState": {"Name": "stopped", "Code": 80},
                        "PreviousState": {"Name": prev_state, "Code": 16 if prev_state == "running" else 0}
                    })
            return {"StoppingInstances": stopping}

    def start_instances(self, InstanceIds: List[str], **kwargs) -> Dict[str, Any]:
        self._ensure_fleet()
        with self._lock:
            starting = []
            for inst in self._instances:
                if inst["InstanceId"] in InstanceIds:
                    prev_state = inst.get("State", {}).get("Name", "stopped")
                    inst["State"] = {"Name": "running", "Code": 16}
                    starting.append({
                        "InstanceId": inst["InstanceId"],
                        "CurrentState": {"Name": "running", "Code": 16},
                        "PreviousState": {"Name": prev_state, "Code": 80}
                    })
            return {"StartingInstances": starting}

    def create_tags(self, Resources: List[str], Tags: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        self._ensure_fleet()
        with self._lock:
            for inst in self._instances:
                if inst["InstanceId"] in Resources:
                    inst_tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                    for t in Tags:
                        inst_tags[t["Key"]] = t["Value"]
                    inst["Tags"] = [{"Key": k, "Value": v} for k, v in inst_tags.items()]
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def delete_tags(self, Resources: List[str], Tags: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        self._ensure_fleet()
        with self._lock:
            keys_to_remove = {t["Key"] for t in Tags}
            for inst in self._instances:
                if inst["InstanceId"] in Resources:
                    inst["Tags"] = [
                        t for t in inst.get("Tags", [])
                        if t.get("Key") not in keys_to_remove
                    ]
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}


class SimulatedLambdaClient:
    """Simulates AWS Lambda list_functions, get_account_settings, put_function_concurrency, delete_function_concurrency."""

    def __init__(self):
        self._lock = threading.RLock()
        self._concurrency_caps: Dict[str, int] = {}
        self._total_concurrency = 1000

    def list_functions(self, **kwargs) -> Dict[str, Any]:
        from src.synthetic_data import generate_lambda_functions
        funcs = generate_lambda_functions()
        with self._lock:
            for f in funcs:
                name = f["FunctionName"]
                if name in self._concurrency_caps:
                    f["ReservedConcurrentExecutions"] = self._concurrency_caps[name]
        return {"Functions": funcs}

    def get_account_settings(self, **kwargs) -> Dict[str, Any]:
        return {
            "AccountLimit": {
                "ConcurrentExecutions": self._total_concurrency,
                "UnreservedConcurrentExecution": self._total_concurrency - sum(self._concurrency_caps.values())
            },
            "AccountUsage": {
                "FunctionCount": 4,
                "TotalCodeSize": 15728640
            }
        }

    def put_function_concurrency(self, FunctionName: str, ReservedConcurrentExecutions: int, **kwargs) -> Dict[str, Any]:
        with self._lock:
            self._concurrency_caps[FunctionName] = ReservedConcurrentExecutions
            return {"ReservedConcurrentExecutions": ReservedConcurrentExecutions}

    def delete_function_concurrency(self, FunctionName: str, **kwargs) -> Dict[str, Any]:
        with self._lock:
            self._concurrency_caps.pop(FunctionName, None)
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}


class SimulatedCloudWatchClient:
    """Simulates AWS CloudWatch get_metric_statistics for EC2 CPU utilization."""

    def get_metric_statistics(self, Namespace: str = "AWS/EC2", MetricName: str = "CPUUtilization",
                              Dimensions: Optional[List[Dict[str, str]]] = None,
                              StartTime: Optional[datetime] = None, EndTime: Optional[datetime] = None,
                              Period: int = 300, Statistics: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
        instance_id = "unknown"
        if Dimensions:
            for d in Dimensions:
                if d.get("Name") == "InstanceId":
                    instance_id = d.get("Value", "unknown")

        from src.synthetic_data import generate_instance_cpu_datapoint
        cpu_val = generate_instance_cpu_datapoint(instance_id)

        now = datetime.now(timezone.utc)
        return {
            "Datapoints": [
                {
                    "Timestamp": now,
                    "Average": cpu_val,
                    "Unit": "Percent"
                }
            ],
            "Label": "CPUUtilization"
        }


class SimulatedS3Client:
    """Simulates AWS S3 list_buckets."""

    def list_buckets(self, **kwargs) -> Dict[str, Any]:
        from src.synthetic_data import generate_s3_buckets
        return {"Buckets": generate_s3_buckets()}


class SimulatedSTSClient:
    """Simulates AWS STS get_caller_identity."""

    def get_caller_identity(self, **kwargs) -> Dict[str, Any]:
        return {
            "UserId": "SIMULATION_MODE_USER",
            "Account": "000000000000",
            "Arn": "arn:aws:iam::000000000000:user/simulation-mode"
        }

# ─────────────────────────────────────────────────────────────────────────────
# Client Singletons
# ─────────────────────────────────────────────────────────────────────────────
_ec2_client = SimulatedEC2Client()
_lambda_client = SimulatedLambdaClient()
_cloudwatch_client = SimulatedCloudWatchClient()
_s3_client = SimulatedS3Client()
_sts_client = SimulatedSTSClient()
_dynamodb_client = SimulatedDynamoDBClient()
_dynamodb_resource = SimulatedDynamoDBResource()


def get_simulated_ec2_client() -> SimulatedEC2Client:
    return _ec2_client


def get_simulated_lambda_client() -> SimulatedLambdaClient:
    return _lambda_client


def get_simulated_cloudwatch_client() -> SimulatedCloudWatchClient:
    return _cloudwatch_client


def get_simulated_s3_client() -> SimulatedS3Client:
    return _s3_client


def get_simulated_sts_client() -> SimulatedSTSClient:
    return _sts_client


def get_simulated_dynamodb_client() -> SimulatedDynamoDBClient:
    return _dynamodb_client


def get_simulated_dynamodb_resource() -> SimulatedDynamoDBResource:
    return _dynamodb_resource

