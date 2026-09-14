def scan_all(table, filter_expr=None):
    """Scan all items from a DynamoDB table, automatically handling pagination."""
    items = []
    kwargs = {"FilterExpression": filter_expr} if filter_expr else {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def safe_float(val, default=0.0):
    """Safely cast a value to float, handling None, empty string, and exceptions."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default=0):
    """Safely cast a value to int, handling None, empty string, and exceptions."""
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default
