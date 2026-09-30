"""腾讯与新浪公开ETF响应的安全解析器。"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from decimal import Decimal


class PublicEtfSourceError(RuntimeError):
    """公开ETF响应不符合冻结结构。"""


def price_text(value: str | int | float | Decimal) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.000001")), "f")


def parse_tencent_unadjusted(raw: bytes) -> list[dict[str, str]]:
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("code") != 0:
        raise PublicEtfSourceError(f"腾讯接口报错: {payload.get('code')} {payload.get('msg')}")
    source = payload.get("data", {}).get("sh510300", {}).get("day") or []
    rows = []
    for values in source:
        if len(values) < 6:
            raise PublicEtfSourceError(f"腾讯日K字段不足: {values}")
        volume_lots = Decimal(str(values[5]))
        volume_shares = volume_lots * Decimal("100")
        if volume_shares != volume_shares.to_integral_value():
            raise PublicEtfSourceError(f"腾讯成交量无法整手换算: {values}")
        rows.append({
            "date": date.fromisoformat(values[0]).isoformat(),
            "code": "sh.510300",
            "open": price_text(values[1]),
            "close": price_text(values[2]),
            "high": price_text(values[3]),
            "low": price_text(values[4]),
            "volume": str(int(volume_shares)),
            "volume_lots_source": format(volume_lots, "f"),
            "row_source": "tencent_unadjusted",
        })
    dates = [row["date"] for row in rows]
    if not rows or dates != sorted(set(dates)):
        raise PublicEtfSourceError("腾讯日K为空、重复或未升序")
    return rows


def parse_sina_daily(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8")
    match = re.search(r"var\s+_data=\((\[.*\])\);\s*$", text, flags=re.S)
    if not match:
        raise PublicEtfSourceError("新浪日K响应结构异常")
    rows = []
    for row in json.loads(match.group(1)):
        rows.append({
            "date": date.fromisoformat(row["day"]).isoformat(),
            "code": "sh.510300",
            "open": price_text(row["open"]),
            "high": price_text(row["high"]),
            "low": price_text(row["low"]),
            "close": price_text(row["close"]),
            "volume": str(int(row["volume"])),
        })
    dates = [row["date"] for row in rows]
    if not rows or dates != sorted(set(dates)):
        raise PublicEtfSourceError("新浪日K为空、重复或未升序")
    return rows


def parse_sina_factor_events(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8")
    match = re.search(r"=\s*(\{.*?\})\s*/\*", text, flags=re.S)
    if not match:
        raise PublicEtfSourceError("新浪复权因子响应结构异常")
    payload = json.loads(match.group(1))
    source = sorted(
        (row for row in payload.get("data", []) if row.get("d") != "1900-01-01"),
        key=lambda row: row["d"],
    )
    previous = Decimal("0")
    events = []
    for row in source:
        current = Decimal(str(row["u"]))
        cash = current - previous
        previous = current
        if cash <= 0 or Decimal(str(row["s"])) != Decimal("1"):
            raise PublicEtfSourceError(f"新浪分红累计值或份额因子异常: {row}")
        events.append({
            "ex_date": date.fromisoformat(row["d"]).isoformat(),
            "cash_per_share": format(cash, "f"),
            "cumulative_cash": format(current, "f"),
        })
    return events


def parse_sina_dividend_table(raw: bytes, minimum_rows: int = 10) -> list[dict[str, str]]:
    text = raw.decode("gb18030", errors="strict")
    start = text.find("历史分红")
    if start < 0:
        raise PublicEtfSourceError("新浪历史分红表不存在")
    parsed = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", text[start:], flags=re.S | re.I):
        cells = []
        for value in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.S | re.I):
            cells.append(html.unescape(re.sub(r"<[^>]+>", "", value)).strip())
        if len(cells) < 3 or not re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", cells[0]):
            continue
        if not re.fullmatch(r"\d+(?:\.\d+)?", cells[2]) or Decimal(cells[2]) <= 0:
            continue
        parsed.append({
            "record_date": datetime.strptime(cells[0], "%Y/%m/%d").date().isoformat(),
            "payment_date": datetime.strptime(cells[1], "%Y/%m/%d").date().isoformat(),
            "cash_per_share": format(Decimal(cells[2]), "f"),
        })
    if len(parsed) < minimum_rows:
        raise PublicEtfSourceError(f"新浪历史分红表有效记录过少: {len(parsed)}")
    return sorted(parsed, key=lambda row: row["record_date"])


def combine_dividends(
    factor_events: list[dict[str, str]], table_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    unused = list(table_rows)
    output = []
    for event in factor_events:
        amount = Decimal(event["cash_per_share"])
        matches = [
            row for row in unused
            if Decimal(row["cash_per_share"]) == amount
            and row["record_date"] <= event["ex_date"] <= row["payment_date"]
        ]
        if len(matches) != 1:
            raise PublicEtfSourceError(f"新浪两组分红无法唯一匹配: {event} {matches}")
        row = matches[0]
        unused.remove(row)
        output.append({
            "record_date": row["record_date"],
            "ex_date": event["ex_date"],
            "payment_date": row["payment_date"],
            "cash_per_share": event["cash_per_share"],
            "cumulative_cash": event["cumulative_cash"],
        })
    if unused:
        raise PublicEtfSourceError(f"新浪历史表存在未匹配分红: {unused}")
    return output
