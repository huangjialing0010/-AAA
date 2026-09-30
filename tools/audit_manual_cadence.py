"""从已存在成交记录量化人工操作频率；不改变策略。"""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


def audit(path):
    daily = Counter()
    per_code = defaultdict(set)
    with path.open(encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle):
            day = date.fromisoformat(row['trade_date'])
            daily[day] += 1
            per_code[row['code']].add(day)
    gaps = [(b - a).days for days in per_code.values()
            for a, b in zip(sorted(days), sorted(days)[1:])]
    counts = sorted(daily.values())
    return {'source': str(path.resolve()),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'trade_days': len(daily), 'trade_rows': sum(daily.values()),
            'first_trade': str(min(daily)) if daily else None,
            'last_trade': str(max(daily)) if daily else None,
            'max_rows_per_trade_day': max(counts, default=0),
            'median_rows_per_trade_day': counts[len(counts)//2] if counts else None,
            'min_same_code_distinct_day_gap_calendar_days': min(gaps, default=None),
            'same_code_gaps_under_three_calendar_days': sum(g < 3 for g in gaps),
            'limitations': ['不同成交日之间的间隔；同日多笔未折算为一次操作',
                            '研究名义金额成交，不证明真实股数账户可执行',
                            '历史统计不保证未来频率，未计入未来风险处置']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trades', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.trades), ensure_ascii=False, indent=2))
