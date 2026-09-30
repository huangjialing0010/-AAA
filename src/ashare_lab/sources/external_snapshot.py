"""外部行情连接器的最小快照契约；不负责网络访问或写入 canonical。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


REQUIRED_RULE_FIELDS = frozenset(
    {
        "trade_date",
        "code",
        "tradestatus",
        "upper_limit",
        "lower_limit",
        "buyable",
        "sellable",
        "source",
    }
)
REQUIRED_REPLAY_FIELDS = frozenset(
    {"open", "evidence_id", "execution_evidence_id", "minimum_buy",
     "buy_increment", "sell_policy", "tradable"}
)


@dataclass(frozen=True)
class ExternalSnapshotMetadata:
    source_name: str
    connector_version: str
    queried_at: datetime
    raw_directory: Path
    manifest_sha256: str
    status: str = "COLLECTED_PENDING_INDEPENDENT_VALIDATION"

    def __post_init__(self) -> None:
        if not self.source_name.strip() or not self.connector_version.strip():
            raise ValueError("来源和连接器版本不能为空")
        if self.queried_at.tzinfo is None:
            raise ValueError("查询时间必须带时区")
        if not self.raw_directory.is_absolute():
            raise ValueError("原始目录必须使用绝对路径")
        if len(self.manifest_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.manifest_sha256.lower()
        ):
            raise ValueError("manifest_sha256 必须是64位十六进制摘要")
        if self.status not in {
            "COLLECTED_PENDING_INDEPENDENT_VALIDATION",
            "SEALED",
            "BLOCKED",
        }:
            raise ValueError("快照状态不在允许集合内")


def validate_rule_fields(fields: set[str] | frozenset[str]) -> tuple[str, ...]:
    """返回缺失字段；不从其他字段推断规则字段。"""
    return tuple(sorted(REQUIRED_RULE_FIELDS - set(fields)))


def validate_replay_fields(fields: set[str] | frozenset[str]) -> tuple[str, ...]:
    """返回 paper replay 所需的执行字段缺口；不填充默认值。"""
    return tuple(sorted(REQUIRED_REPLAY_FIELDS - set(fields)))
