# 当前策略参考来源：固定版本与复用边界

2026-09-30只读复核。通过GitHub公开API查询提交及该提交根目录，读取可见许可证全文；未安装、执行或复制仓库业务代码。本记录是审阅摘要，不是原始响应封存或完整仓库审计。

| 来源 | 本次固定提交 | 许可证核对 | 本项目用途与边界 |
| --- | --- | --- | --- |
| haiyangchenbj/a-stock-screener-skill | a7fff66b52e4c5d7ab1502ed728b2b8e4709886e | 根目录LICENSE为MIT，版权haiyangchenbj 2026 | 借鉴硬过滤、分析卡和纪律结构；不照搬其评分、行业偏好或10股组合；复用代码须保留声明并逐项审查缺失数据语义 |
| Hankerlove/A-stock | d449fd9be264ad0ac194b15ac6ad8171f5eae427 | 该提交根目录未找到LICENSE/COPYING文件 | 授权仍未明确，不复制代码；未排除子目录或文件头另有声明。只能保留研究线索，不能称已获代码授权 |
| mementum/backtrader | b853d7c90b6721476eb5a5ea3135224e33db1f14 | 根目录LICENSE正文GNU GPL v3 | 借鉴订单生命周期和账户分层；目前不替换本地执行器。将来整合或分发前另审具体文件及适用义务 |
| akfamily/akshare | 0191689d57c667b7c7a198fd0cf97316837ef311 | 根目录LICENSE为MIT，版权Albert King 2019—2026 | 数据接口参考，不是策略；软件许可不等于上游行情/财务数据使用授权 |

## 固定来源入口

- https://github.com/haiyangchenbj/a-stock-screener-skill/blob/a7fff66b52e4c5d7ab1502ed728b2b8e4709886e/LICENSE
- https://github.com/Hankerlove/A-stock/tree/d449fd9be264ad0ac194b15ac6ad8171f5eae427
- https://github.com/mementum/backtrader/blob/b853d7c90b6721476eb5a5ea3135224e33db1f14/LICENSE
- https://github.com/akfamily/akshare/blob/0191689d57c667b7c7a198fd0cf97316837ef311/LICENSE

## 对当前策略的判断

现有L1/R1及质量、仓位、退出阈值是本项目研究假设，不应宣传为以上项目已验证的盈利策略。筛选器README的硬过滤和组合说明不是样本外收益证据；通用回测引擎也不能证明A股实际成交或公司行动正确。

下一步源码审阅应固定在以上提交，将可借鉴机制映射到本地实现与测试；左右侧具体参数的来源及有效性仍须分开核验。本次不改变现有规则、不引入外部代码、不把许可证审阅当策略验收。持续账户接入仍按待批准方案执行。
