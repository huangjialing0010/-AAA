# 交易规则证据数据契约

## 目标

为有限窗口的真实股数 shadow replay 提供逐日、逐证券的可审计交易规则。未满足本契约时，订单必须拒绝，不得用价格推算替代。

## 必需字段

| 字段 | 语义 | 验收要求 |
|---|---|---|
| `trade_date` | 交易日 | 与行情日期一致 |
| `code` | 证券代码 | 六位代码，与订单一致 |
| `tradestatus` | 是否交易 | 必须有来源值，不得空值代 1 |
| `upper_limit` | 当日涨停价 | 必须标明计算/来源口径 |
| `lower_limit` | 当日跌停价 | 必须标明计算/来源口径 |
| `buyable` | 是否可买 | 需有来源或明确规则证据 |
| `sellable` | 是否可卖 | 需有来源或明确规则证据 |
| `suspend_reason` | 停牌/不可交易原因 | 无停牌也要保留空值语义 |
| `source` | 来源 | 记录数据源和版本 |

进入 paper replay 还必须有：`open`、`evidence_id`、`execution_evidence_id`、`minimum_buy`、`buy_increment`、`sell_policy`、`tradable`。这些字段不能由加载器自动补默认值。

费用另行按账户规则版本绑定，不写进价格字段；至少要记录佣金、印花税、生效日和适用方向。

## 外部规则与推算规则的对照

外部来源的 `upper_limit/lower_limit` 是规则证据；本地根据前收和板块规则计算的价格只能作为独立校验。两者比较允许最多 `0.01` 元的单边舍入差异，但必须保留原始外部值、推算值和差异记录。超过该范围时，订单规则证据进入 `REVIEW`，不得静默采用任一方。

板块识别必须覆盖 `300/301/302` 创业板代码、`688/689` 科创板代码及北交所代码；新增代码段必须先通过外部重叠样本核验。

## 禁止事项

- 不得只根据 `pct_chg` 反推涨跌停；
- 不得把 `tradestatus=1` 等同于一定可买可卖；
- 不得用当前规则倒灌历史规则；
- 不得在未封存 manifest、SHA256 和独立校验前进入回放。

## 当前状态

现有 `data/canonical/stock_selection_v1/prices/` 和已封存 BaoStock 历史日线均缺少上述规则字段，当前 shadow replay 保持阻塞。

2026-09-02 已生成 `data/canonical/zzshare_rule_audit_20260902/` 审计专用快照（873 行）。该目录只保留外部涨跌停、停牌/ST 字段及本地推算对照；`buyable`、`sellable`、`suspend_reason` 明确为空并在 manifest 中标记限制，因此不得被加载器当作 paper replay 规则。
