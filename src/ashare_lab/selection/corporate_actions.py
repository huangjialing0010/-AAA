"""公司行动持仓转换；不负责估算现金补偿或生成成交。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def convert_position(shares: Decimal, successors: list[dict[str, str]]) -> dict[str, object]:
    """按证据快照转换持仓，保留零碎股，不静默四舍五入。"""
    if shares < 0:
        raise ValueError("持仓数量不能为负")
    if not successors:
        raise ValueError("缺少承继证券")
    converted: dict[str, Decimal] = {}
    for successor in successors:
        code = successor.get("code", "")
        if len(code) != 6 or not code.isdigit():
            raise ValueError("承继证券代码格式无效")
        try:
            ratio = Decimal(successor["share_ratio"])
        except (KeyError, InvalidOperation):
            raise ValueError("换股比例格式无效") from None
        if ratio <= 0:
            raise ValueError("换股比例必须为正")
        converted[code] = converted.get(code, Decimal("0")) + shares * ratio
    return {
        "converted_shares": converted,
        "fractional_shares": {code: value % 1 for code, value in converted.items() if value % 1},
        "cash_settlement_required": any(value % 1 for value in converted.values()),
        "status": "REQUIRES_CASH_SETTLEMENT_REVIEW" if any(value % 1 for value in converted.values()) else "MAPPED",
    }


def apply_corporate_action(positions: dict[str, Decimal], event: dict) -> dict[str, object]:
    """应用公司行动映射并返回新持仓与审计事件；不处理现金补偿。"""
    source = event.get("source_code", "")
    if source not in positions:
        return {"positions": dict(positions), "status": "NO_SOURCE_POSITION", "audit": []}
    conversion = convert_position(positions[source], event.get("successors", []))
    updated = dict(positions)
    del updated[source]
    for code, shares in conversion["converted_shares"].items():
        updated[code] = updated.get(code, Decimal("0")) + shares
    audit = [{"event_type": event["event_type"], "effective_date": event["effective_date"], "source_code": source, "successor_code": code, "shares": str(shares)} for code, shares in conversion["converted_shares"].items()]
    return {
        "positions": updated,
        "status": conversion["status"],
        "fractional_shares": conversion["fractional_shares"],
        "cash_settlement_required": conversion["cash_settlement_required"],
        "audit": audit,
    }
