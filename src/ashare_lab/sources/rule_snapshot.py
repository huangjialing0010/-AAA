"""将已校验的交易规则CSV加载为 paper replay 映射。"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from .external_snapshot import validate_replay_fields, validate_rule_fields


def load_rule_snapshot(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing = validate_rule_fields(fields)
        if missing:
            raise ValueError("规则快照缺少字段: " + ",".join(missing))
        replay_missing = validate_replay_fields(fields)
        if replay_missing:
            raise ValueError("回放字段缺少: " + ",".join(replay_missing))
        result: dict[tuple[str, str], dict[str, str]] = {}
        for row_number, row in enumerate(reader, start=2):
            key = (row["code"], row["trade_date"])
            if key in result:
                raise ValueError(f"规则快照重复: row={row_number} key={key}")
            try:
                date.fromisoformat(row["trade_date"])
            except ValueError:
                raise ValueError(f"交易日期无效: row={row_number}") from None
            if len(row["code"]) != 6 or not row["code"].isdigit():
                raise ValueError(f"证券代码无效: row={row_number}")
            for field in ("upper_limit", "lower_limit"):
                if Decimal(row[field]) <= 0:
                    raise ValueError(f"规则价格无效: row={row_number} field={field}")
            if row["tradable"] not in {"0", "1"}:
                raise ValueError(f"可交易标记无效: row={row_number}")
            for field in ("minimum_buy", "buy_increment"):
                if int(row[field]) <= 0:
                    raise ValueError(f"数量规则无效: row={row_number} field={field}")
            result[key] = dict(row)
        return result
