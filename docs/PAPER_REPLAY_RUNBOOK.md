# Paper Replay 运行顺序

## 1. 获取并封存

将外部连接器原始 CSV 放入新的 `data/imports/<source>_<timestamp>/` 目录，不覆盖既有快照。运行 manifest 生成器，确认字段、行数、文件大小和 SHA256 已记录。

## 2. 独立验收

先检查基础规则字段，再检查 paper replay 执行字段。验收失败时保持 `COLLECTED_PENDING_INDEPENDENT_VALIDATION`，不得运行回放。

## 3. 封存

只有人工/独立校验完成后，才允许将 manifest 状态改为 `SEALED`。原始 CSV 内容不得在此过程中修改。

## 4. 回放

运行 `tools/run_paper_replay.py`，输入研究订单和已封存规则 CSV。回放必须使用显式历史费用规则，逐笔保存成交或拒绝原因。

## 5. 结果解释

- `BOOKED` 只表示通过规则闸门并写入纸面账户；
- `REJECTED` 必须保留具体原因；
- 回放收益只有在完整公司行动、期初账户和估值规则均已验证后才可分析；
- 任何阶段都不连接券商或发送真实订单。

## 当前状态

合成规则快照已完成端到端演练；真实外部规则快照尚未接入，因此真实 paper replay 仍保持阻塞。
