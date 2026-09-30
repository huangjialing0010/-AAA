"""证券特殊事件的准入校验；不负责推断或生成处置价。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation


EVENT_TYPES = frozenset({"DELISTING", "MERGER", "CODE_CHANGE", "DATA_INTERRUPTION"})
PRICE_BASES = frozenset({"AUCTION", "SETTLEMENT", "LAST_TRADE", "UNKNOWN"})


def validate_event_evidence(event: dict[str, str]) -> None:
    """校验证据快照的结构；允许未知结算字段，但不代表可结算。"""
    required = ("code", "event_type", "effective_date", "last_tradable_date", "settlement_date", "settlement_price", "price_basis", "source", "source_as_of", "sha256")
    missing = [name for name in required if not event.get(name)]
    if missing:
        raise ValueError(f"事件证据缺少字段: {','.join(missing)}")
    if event["event_type"] not in EVENT_TYPES:
        raise ValueError(f"未知事件类型: {event['event_type']}")
    if event["price_basis"] not in PRICE_BASES:
        raise ValueError(f"未知处置价口径: {event['price_basis']}")
    try:
        effective = date.fromisoformat(event["effective_date"])
        last_trade = date.fromisoformat(event["last_tradable_date"])
        if event["settlement_date"] != "UNKNOWN":
            date.fromisoformat(event["settlement_date"])
        Decimal(event["settlement_price"])
    except (ValueError, InvalidOperation):
        raise ValueError("事件证据日期或处置价格式无效") from None
    if last_trade > effective:
        raise ValueError("事件证据日期顺序无效")
    if event["price_basis"] == "UNKNOWN" and event["settlement_price"] != "0":
        raise ValueError("未知处置价口径必须使用0占位")


def validate_corporate_action_evidence(event: dict) -> None:
    """校验换股/分立证据快照；只验证映射完整性，不生成结算成交。"""
    required = ("source_code", "event_type", "effective_date", "successors", "cash_option", "settlement_price", "price_basis", "source", "source_as_of")
    missing = [name for name in required if not event.get(name)]
    if missing:
        raise ValueError(f"公司行动证据缺少字段: {','.join(missing)}")
    if event["event_type"] not in {"MERGER_AND_TERMINATION", "DEMERGER_AND_TERMINATION"}:
        raise ValueError(f"未知公司行动类型: {event['event_type']}")
    try:
        date.fromisoformat(event["effective_date"])
        Decimal(event["settlement_price"])
    except (ValueError, InvalidOperation):
        raise ValueError("公司行动日期或结算价格式无效") from None
    if not isinstance(event["successors"], list) or not event["successors"]:
        raise ValueError("公司行动必须有承继证券")
    for successor in event["successors"]:
        if not successor.get("code") or not successor.get("share_ratio"):
            raise ValueError("承继证券缺少代码或换股比例")
        if len(str(successor["code"])) != 6 or not str(successor["code"]).isdigit():
            raise ValueError("承继证券代码格式无效")
        try:
            if Decimal(successor["share_ratio"]) <= 0:
                raise ValueError("换股比例必须为正")
        except InvalidOperation:
            raise ValueError("换股比例格式无效") from None
    if event["price_basis"] == "UNKNOWN" and event["settlement_price"] != "0":
        raise ValueError("未知结算口径必须使用0占位")


def validate_delisting_event(event: dict[str, str]) -> None:
    required = ("code", "event_type", "effective_date", "last_tradable_date", "settlement_date", "settlement_price", "price_basis", "source", "source_as_of", "sha256")
    missing = [name for name in required if not event.get(name)]
    if missing:
        raise ValueError(f"事件缺少字段: {','.join(missing)}")
    if event["event_type"] not in EVENT_TYPES:
        raise ValueError(f"未知事件类型: {event['event_type']}")
    if event["price_basis"] not in PRICE_BASES:
        raise ValueError(f"未知处置价口径: {event['price_basis']}")
    try:
        effective = date.fromisoformat(event["effective_date"])
        last_trade = date.fromisoformat(event["last_tradable_date"])
        settlement = date.fromisoformat(event["settlement_date"])
        Decimal(event["settlement_price"])
    except (ValueError, InvalidOperation):
        raise ValueError("事件日期或处置价格式无效") from None
    if last_trade > effective or settlement < last_trade:
        raise ValueError("事件日期顺序无效")
    price = Decimal(event["settlement_price"])
    if price <= 0 and event["price_basis"] != "UNKNOWN":
        raise ValueError("已指定处置价口径时价格必须为正")
