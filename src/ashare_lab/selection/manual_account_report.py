"""Read-only first-order journal reporting; no invented fills or stale valuation."""
from datetime import datetime, timedelta
from decimal import Decimal as D
from .manual_quote import SHANGHAI


def money(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not money')
    result = D(str(value))
    if not result.is_finite():
        raise ValueError('Nonfinite money')
    return result


def summarize(run, events, *, now):
    if now.tzinfo is None:
        raise ValueError('Timezone required')
    now = now.astimezone(SHANGHAI)
    generated = datetime.fromisoformat(run['generated_at'])
    if generated.tzinfo is None or generated > now:
        raise ValueError('Future launch')
    if len(run['orders']) != 1 or run['trades'] or run['positions']:
        raise ValueError('First-order journal only')
    order = run['orders'][0]
    ordered = sorted(events, key=lambda e:datetime.fromisoformat(e['checked_at']))
    terminal = None
    for event in ordered:
        stamp = datetime.fromisoformat(event['checked_at'])
        if stamp.tzinfo is None or not generated <= stamp <= now:
            raise ValueError('Invalid event time')
        if event['order_id'] != order['order_id'] or event['code'] != order['code']:
            raise ValueError('Event identity mismatch')
        if terminal is not None:
            raise ValueError('Event after terminal or duplicate terminal')
        if event['status'] in ('SIMULATED_BOOKED','REJECTED','EXPIRED'):
            terminal = event
        elif event['status'] not in ('WAITING','WAITING_EVIDENCE'):
            raise ValueError('Unknown event status')
    latest = ordered[-1] if ordered else None
    cash = initial = money(run['cash'])
    if initial <= 0:
        raise ValueError('Invalid initial capital')
    fills = terminal.get('fills',[]) if terminal else []
    holdings = []
    fees = D(0)
    equity = None
    valuation_day = None
    if terminal and terminal['status'] == 'SIMULATED_BOOKED':
        if len(fills) != 1 or len(terminal['positions']) != 1:
            raise ValueError('First buy requires one fill and one lot')
        fill, lot = fills[0], terminal['positions'][0]
        if (fill['fill_id'] != order['order_id'] or fill['code'] != order['code'] or fill['side'] != 'BUY'
                or str(fill['day']) != order['execution_day'] or lot['lot_id'] != fill['fill_id']
                or lot['code'] != fill['code'] or lot['shares'] != fill['shares']):
            raise ValueError('Fill or lot identity mismatch')
        if type(fill['shares']) is not int or fill['shares'] <= 0 or fill['shares'] % 100:
            raise ValueError('Invalid lot quantity')
        fees = money(fill['fees'])
        price = money(fill['price'])
        if fees < 0 or price <= 0:
            raise ValueError('Invalid execution cost')
        cash -= fill['shares']*price+fees
        if cash < 0 or cash != money(terminal['cash']):
            raise ValueError('Independent cash reconciliation failed')
        if fill['shares']*price+fees > money(order['budget']):
            raise ValueError('Order budget exceeded')
        holdings = terminal['positions']
        valuation_day = str(fill['day'])
        if valuation_day == now.date().isoformat() and terminal.get('valuation_price') is not None:
            mark = money(terminal['valuation_price'])
            if mark <= 0:
                raise ValueError('Invalid valuation')
            equity = cash+mark*fill['shares']
            if equity != money(terminal['equity']):
                raise ValueError('Equity reconciliation failed')
    else:
        for event in ordered:
            if event.get('fills') or event.get('positions') or money(event['cash']) != initial:
                raise ValueError('Non-booked event changes account')
        if latest and datetime.fromisoformat(latest['checked_at']).astimezone(SHANGHAI).date() == now.date():
            equity = cash
            valuation_day = now.date().isoformat()
    pnl = equity-initial if equity is not None else None
    account_return = pnl/initial if pnl is not None else None
    status = terminal['status'] if terminal else 'PENDING_EXECUTION'
    if terminal is None and order['execution_day'] < now.date().isoformat():
        status = 'OVERDUE_UNPROCESSED'
    start = generated.astimezone(SHANGHAI).date()
    period_starts = {'daily':now.date(), 'weekly':max(start,now.date()-timedelta(days=now.weekday())),
                     'monthly':max(start,now.date().replace(day=1))}
    period_counts = {}
    for period, beginning in period_starts.items():
        batch_in_period = beginning <= start <= now.date()
        rejection_in_period = (terminal is not None and terminal['status']=='REJECTED'
            and beginning <= datetime.fromisoformat(terminal['checked_at']).astimezone(SHANGHAI).date() <= now.date())
        period_counts[period] = dict(signals=len(run['signals']) if batch_in_period else 0,
            orders=int(batch_in_period),fills=sum(beginning.isoformat() <= str(f['day']) <= now.date().isoformat() for f in fills),
            order_rejections=int(rejection_in_period),
            admission_exclusions=len(run['rejections']) if batch_in_period else 0,cancellations=0)
    signal = next((s for s in run.get('signals', []) if s.get('code') == order['code']), {})
    if status == 'SIMULATED_BOOKED':
        action_status = '已进入模拟账户'
    elif status == 'PENDING_EXECUTION':
        action_status = '候选，等待执行核验'
    else:
        action_status = '不执行'
    opportunity = dict(recommendation_id=order['order_id'], order_id=order['order_id'],
                       code=order['code'], industry=order.get('industry'),
                       mechanism=order.get('mechanism'), action_status=action_status,
                       signal_day=order.get('signal_day'), market_cutoff=run.get('market_cutoff'),
                       budget=order.get('budget'), reasons=signal.get('reasons', []),
                       metrics=signal.get('metrics', {}), execution_day=order.get('execution_day'),
                       execution_requirements=order.get('execution_requirements', []),
                       manual_decision='系统提供候选和规则依据，是否实盘由用户决定',
                       binding_status='BOUND_TO_SIMULATION_ORDER')
    return dict(version='MANUAL_ACCOUNT_REPORT_V1', account_id=run['account_id'],
        generated_at=now.isoformat(), report_day=now.date().isoformat(), account_started=start.isoformat(),
        market_cutoff=run['market_cutoff'], valuation_day=valuation_day,
        ledger_cutoff=latest['checked_at'] if latest else run['generated_at'],
        order=order, order_status=status, opportunity=opportunity, cash=str(cash), positions=holdings, fills=fills, fees=str(fees),
        equity=str(equity) if equity is not None else None, net_pnl=str(pnl) if pnl is not None else None,
        account_return=str(account_return) if account_return is not None else None,
        cash_weight=str(cash/equity) if equity is not None and equity > 0 else None,
        counts=dict(signals=len(run['signals']),orders=1,fills=len(fills),
                    order_rejections=int(status=='REJECTED'),admission_exclusions=len(run['rejections']),cancellations=0),
        period_counts=period_counts,
        last_reason=latest['reason'] if latest else 'NO_EXECUTION_CHECK',
        daily_pnl=str(pnl) if start==now.date() and pnl is not None else None,
        max_drawdown=None,benchmark_return=None, strategy_assessment='INSUFFICIENT_TRADING_SAMPLE',
        weekly_start=max(start,now.date()-timedelta(days=now.weekday())).isoformat(),
        monthly_start=max(start,now.date().replace(day=1)).isoformat(),
        limitations=['首笔买入账本范围；不支持分红、外部资金流或多批次',
                     '缺完整逐日净值，不计算回撤、胜率和超额收益',
                     '未成交的信号涨跌不是账户收益', '真实候选与合成测试必须隔离'])


def render(report, period='daily'):
    label = {'daily':'日报','weekly':'周内报告','monthly':'月内报告'}[period]
    def value(key, suffix=''):
        return report[key]+suffix if report[key] is not None else '不可计算'
    start = report.get(period+'_start',report['report_day'])
    order = report['order']
    status_labels = {'PENDING_EXECUTION':'待执行','SIMULATED_BOOKED':'已模拟成交',
                     'REJECTED':'订单被拒绝','EXPIRED':'订单已过期','OVERDUE_UNPROCESSED':'已过执行日，尚未处理'}
    count_labels = {'signals':'信号','orders':'订单','fills':'成交','order_rejections':'订单拒绝',
                    'admission_exclusions':'准入排除','cancellations':'撤单'}
    account_pct = f'{D(report["account_return"])*100:.2f}%' if report['account_return'] is not None else '不可计算'
    cash_pct = f'{D(report["cash_weight"])*100:.2f}%' if report['cash_weight'] is not None else '不可计算'
    lines = [f'# 模拟盘{label}｜{report["report_day"]}', '',
        f'账户：{report["account_id"]}；覆盖区间：{start}至{report["report_day"]}。',
        '周/月报告为当前已覆盖区间，不代表完整周期。' if period!='daily' else '',
        '', '## 今日机会与行动建议','',
        f'- 股票：{report["opportunity"]["code"]}；方向：{report["opportunity"]["mechanism"] or "缺失"}；状态：{report["opportunity"]["action_status"]}。',
        f'- 推荐编号：{report["opportunity"]["recommendation_id"]}；绑定：{report["opportunity"]["binding_status"]}。',
        f'- 信号日：{report["opportunity"]["signal_day"] or "缺失"}；行情截止：{report["opportunity"]["market_cutoff"] or "缺失"}；拟执行日：{report["opportunity"]["execution_day"] or "缺失"}。',
        f'- 首仓含费预算：{report["opportunity"]["budget"] or "缺失"}元；执行前提：{"；".join(report["opportunity"]["execution_requirements"]) or "缺失"}。',
        '- 结论：候选不等于可买；只有盘前资格、当日交易状态、价格和公司行动证据齐全才可进入模拟账户。实盘是否执行由用户决定。',
        '', '## 事实','',
        f'- 订单：{order["code"]}，{order["mechanism"]}，含费预算{order["budget"]}元，拟执行日{order["execution_day"]}。',
        f'- 状态：{status_labels[report["order_status"]]}；最近检查原因：{report["last_reason"]}。',
        '- 本期记录：'+ '；'.join(f'{count_labels[k]}{v}笔' for k,v in report['period_counts'][period].items())+'。',
        '- 开户以来累计记录：'+ '；'.join(f'{count_labels[k]}{v}笔' for k,v in report['counts'].items())+'。',
        f'- 现金：{report["cash"]}元；持仓批次：{len(report["positions"])}；累计费用：{report["fees"]}元。',
        f'- 总资产：{value("equity","元")}；开户以来净损益：{value("net_pnl","元")}。',
        f'- 开户以来全账户收益率：{account_pct}；现金占比：{cash_pct}；当日损益：{value("daily_pnl","元")}。',
        f'- 信号行情截至：{report["market_cutoff"]}；账户估值日：{report["valuation_day"] or "缺失"}；账本截至：{report["ledger_cutoff"]}。',
        '', '## 判断','', '交易样本不足，不能评价策略盈利能力；全现金零损益不代表策略有效。',
        '', '## 后续事项与风险','',
        '核验执行日证据、按规则处理订单并对账；不因没有交易而放宽门槛。少持仓可能放大个股损失。',
        '本报告不产生订单、不修改策略、不接实盘。']
    lines.extend(['', '## 数据缺口与实现范围', ''])
    lines.extend('- '+limitation for limitation in report['limitations'])
    for fill in report['fills']:
        if start <= str(fill['day']) <= report['report_day']:
            lines.insert(lines.index('## 判断')-1,
                f'本期模拟成交：{fill["day"]} {fill["code"]} {fill["side"]} {fill["shares"]}股 × {fill["price"]}元，费用{fill["fees"]}元。')
    return '\n'.join(lines)+'\n'
