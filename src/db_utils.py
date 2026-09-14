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
