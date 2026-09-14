def scan_all(target_table, filter_expr=None):
    """Paginated DynamoDB scan that returns all items beyond the 1MB limit."""
    kwargs = {"FilterExpression": filter_expr} if filter_expr else {}
    res = target_table.scan(**kwargs)
    items = res.get("Items", [])
    while "LastEvaluatedKey" in res:
        kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]
        res = target_table.scan(**kwargs)
        items.extend(res.get("Items", []))
    return items
