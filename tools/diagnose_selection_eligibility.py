"""只读逐层定位首日选股资格，不修改数据或策略。"""
import bisect
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from ashare_lab.selection.strategy import MembershipHistory, factor_observation


def read(path):
    with path.open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def main():
    root = ROOT/'data/canonical/stock_selection_v1'
    day = '2012-12-31'
    calendar = [r['trade_date'] for r in read(root/'trade_calendar.csv')]
    ci = bisect.bisect_left(calendar, day)
    members = MembershipHistory(read(root/'membership_weekly.csv')).as_of(day)
    counts = Counter(); examples = {}; halt_dates = Counter()
    for code in sorted(members):
        rows = read(root/'prices'/(code+'.csv'))
        dates = [r['trade_date'] for r in rows]
        i = bisect.bisect_left(dates, day)
        if i >= len(rows) or dates[i] != day:
            reason = 'NO_SIGNAL_DATE'
        elif i < 252:
            reason = 'SHORT_HISTORY'
        elif dates[i-252:i+1] != calendar[ci-252:ci+1]:
            reason = 'CALENDAR_MISMATCH'
            examples.setdefault(reason, dict(code=code, missing=sorted(set(calendar[ci-252:ci+1])-set(dates[i-252:i+1]))))
        elif any(r['tradestatus'] != '1' for r in rows[i-252:i+1]):
            reason = 'HALT_IN_WINDOW'
            halt_dates.update(r['trade_date'] for r in rows[i-252:i+1] if r['tradestatus'] != '1')
            examples.setdefault(reason, dict(code=code, rows=[r for r in rows[i-252:i+1] if r['tradestatus'] != '1'][:2]))
        elif factor_observation(rows, day, trading_dates=calendar) is None:
            reason = 'OTHER_FILTER'
        else:
            reason = 'ELIGIBLE'
        counts[reason] += 1
    print(json.dumps(dict(day=day, counts=dict(counts), examples=examples, common_halt_dates=halt_dates.most_common(8),
                         calendar_start=calendar[ci-252], calendar_end=calendar[ci]), indent=2))


if __name__ == '__main__':
    main()
