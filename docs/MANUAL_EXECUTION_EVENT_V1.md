# 首笔订单执行事件契约

适用于已登记的首笔模拟买入，不连接实盘。沿用前向启动契约，不改变信号、预算或风险规则。

## 目录与时点

`data/imports/manual_quote_<UTC时间戳>/` 保存东方财富原始响应 `quote.json` 和含来源URL、抓取时间、SHA256的manifest；SEALED仅表示文件已封存，不表示允许成交。规范化输出不是原始快照，不改写来源。

`reports/manual_execution_<UTC时间戳>/` 保存检查事件、订单与来源哈希。只有执行日当天、收盘后、当日完整数据可验证时，才可按事先确定的开盘加滑点模型模拟成交。报告必须称“模拟成交”，不声称真实开盘成交。执行日以后不补成交；过期单留下EXPIRED记录。首次订单没有持仓，公司行动仍须核验而不是默认无事件。

`reports/manual_execution.lock` 是持久化的Windows进程互斥标记，不是账户状态；进程退出释放系统锁，不删除标记。执行审阅的source_hashes必须使用正斜线相对路径并绑定本次quote.json；information_cutoff只允许执行日09:25以前的信息，避免用盘后新消息决定是否模拟早盘成交。

仅声明information_cutoff不足以证明事前决策。执行审阅还必须引用 `eligibility_path`：执行日09:25前由 `tools/freeze_manual_eligibility.py` 用实际系统时钟封存的资格JSON及manifest，目录为 `data/imports/manual_eligibility_<UTC时间戳>/`。该文件是本项目衍生审阅快照，不冒充外部原文；来源文件、哈希和审阅理由必须保留。non_st、no_material_risk、corporate_actions_verified三项须与盘前冻结结论一致。盘后只能补核验成交行情，不得补写盘前资格；错过盘前冻结，不允许该订单按早盘价格模拟成交。

## 数据与核验

价格字段映射：东方财富f46今开、f44最高、f45最低、f43最新、f51涨停、f52跌停、f60昨收，按f59的小数位还原；f57证券代码、f86行情Unix时间。字段映射须与供应商页面或第二来源交叉验证；未核验不得准入。

2026-09-30已核验封存vendor.js：枚举118（交易状态）使用f292并调用状态字典；5为已收盘、6为停牌、7退市、8暂停上市、9未上市、10未开盘、14盘中停牌、15非交易代码、16波动性中断。日终首笔执行仅在整数5时继续其他检查，明确停牌等状态拒绝，未知或未完成状态等待。已收盘只表明供应商当前状态，不单独证明开盘可成交或买卖权限；不得将其自动等同buyable=true。

供应商字段定义审计快照放 `data/imports/manual_quote_fields_<UTC时间戳>/`，原名vendor.js与manifest保留。仅归档为语义核验来源，不执行、不作为本项目实现依赖，也不用于绕过交易状态核验。`tools/fetch_manual_quote_field_source.py` 校验开盘/涨停/跌停字段对应关系；其他字段和价格缩放仍需独立检查。

交易所规则原文保存于 `data/imports/manual_exchange_rules_<UTC时间戳>/`，保留原页面文件名及manifest。仅存证适用规则，不将规则推算价替代行情供应商的真实涨跌停字段；例外事项须另行核验。

执行审阅证据应逐项记录核验结论与来源文件哈希：当日非ST、非停牌、可买规则、公告风险、公司行动、价格映射。各项都须明确true且审阅证券/日期匹配。未知不能转为true。审阅来源应包含官方公告和规则证据，不得仅用一份自写声明。普通主板整手依据上交所2026交易规则3.3.8；真实涨跌停价仍取外部源。来源：https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml 。

首笔研究费用沿用万三佣金、最低5元；买入模型不收印花税，使用0.05%不利滑点。这是研究费用假设，不代表用户券商实际费率。逐笔对账独立复算，事件文件保留现金、整数股数、费用、拒绝原因、T+1可卖日期。重复执行同一订单不得重复入账。

## 风险和未覆盖范围

日线撮合无法证明开盘深度和实际可成交性；报价接口无稳定性保证，字段变更即拒绝。必须披露模型成交而非交易所成交。首笔执行入口不宣称已实现持续买卖、分红处理和收益归因；这些仍需在后续账户循环接入既有模块。

## 操作入口

Python运行时：`C:/Users/Jayron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`，加 `-X utf8`。在项目根运行：

1. `tools/fetch_manual_quote.py` 只读抓取并封存行情；网络不可用时不得生成替代数据。
2. 审查执行日公告、证券状态、公司行动及价格字段映射，将原始来源封存，另建执行审阅JSON，不修改已有信号。
3. 审阅JSON含 `code`、`day`、带时区的 `reviewed_at`、`information_cutoff`，以及 `non_st`、`not_suspended`、`buyable`、`no_material_risk`、`corporate_actions_verified`、`price_mapping_verified` 六项明确布尔值。未知用null。`source_hashes` 为项目相对路径到SHA256的映射，必须包括本次quote.json及实际审阅来源；源字段与结论的语义关联仍由审阅人核验，哈希本身不证明结论正确。
4. `tools/check_manual_execution.py --quote data/imports/<本次行情快照> --review reports/<本次审阅文件.json>`。不传审阅可进行等待检查，但不能成交。重复终态返回原记录；文件损坏停止，不自动重置。
5. 查阅新事件的status、fills、cash、positions、reconciliation；测试数据不得混入正式事件。每日估值与持续卖出循环仍须单独完成，不把该首笔入口用于已有持仓账户。

2026-09-29已创建并更新应用定时检查ID `a`，每天Asia/Shanghai 09:00盘前资格核验、20:00执行检查与报告，均回到当前任务；调度状态以应用查询为准。该任务是带审阅的继续执行，不是无条件成交程序。首轮运行结果仍须验收。官方运行前提见 https://learn.chatgpt.com/docs/automations ：本地项目需要电脑开机且应用运行。
