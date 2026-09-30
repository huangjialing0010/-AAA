"""用真实封存证据登记首批前向模拟订单；不补记成交。"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ashare_lab.selection.manual_live import prepare_launch, sha


def main():
    for prior in sorted((ROOT/'reports').glob('manual_forward_*/manifest.json')):
        m=json.loads(prior.read_text(encoding='utf-8'))
        if m.get('account_id')=='manual_research_100k_v1':
            for name,h in m['files'].items():
                if sha(prior.parent/name)!=h:
                    raise ValueError('已有运行文件改变，禁止重建')
            print('ALREADY_LAUNCHED',prior.parent)
            return 0
    now=datetime.now(timezone(timedelta(hours=8)))
    opening=ROOT/'reports/manual_paper_20260929T055058319048Z/opening.json'
    opening_manifest=json.loads((opening.parent/'manifest.json').read_text())
    if sha(opening)!=opening_manifest['entries']['opening.json']:
        raise ValueError('开户哈希不符')
    result=prepare_launch(ROOT,snapshot=ROOT/'data/imports/baostock_current_quality_valuation_20260929T055313Z',
                          reference=ROOT/'data/imports/manual_reference_20260929T062017Z',
                          announcements=ROOT/'data/imports/manual_announcements_600886_20260929T062234Z',now=now)
    result['sources'][str(opening.relative_to(ROOT))]=sha(opening)
    output=ROOT/'reports'/now.astimezone(timezone.utc).strftime('manual_forward_%Y%m%dT%H%M%S%fZ')
    output.mkdir(exist_ok=False)
    with (output/'run.json').open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    lines=['# 真实候选模拟盘首日记录','',f'生成时刻：{now.isoformat()}；行情截至：{result["market_cutoff"]}。',
           '',f'状态：{result["status"]}。研究本金10万元，全现金；成交0、持仓0；策略收益不可计算。','',
           '## 待执行模拟订单','']
    lines += [f'- {o["code"]} {o["mechanism"]}，含费预算{o["budget"]}元；执行日{o["execution_day"]}；股数待执行价格确定。' for o in result['orders']]
    lines += ['', '## 未准入原因','']+[f'- {r["code"]}：{r.get("reasons",r.get("reason"))}' for r in result['rejections']]
    lines += ['', '风险：国投电力半年报利润和现金流同比下降；电价、燃料、来水及融资风险仍在。待执行不是已成交；必须刷新交易条件。自动调度尚未开启。', '']
    with (output/'README.md').open('x',encoding='utf-8') as f:f.write('\n'.join(lines))
    manifest={'account_id':result['account_id'],'status':result['status'],'files':{n:sha(output/n) for n in ('run.json','README.md')},
              'implementation':{p:sha(ROOT/p) for p in ('tools/launch_manual_candidate_paper.py','src/ashare_lab/selection/manual_live.py','src/ashare_lab/selection/manual_rules.py','src/ashare_lab/selection/manual_portfolio.py')}}
    with (output/'manifest.json').open('x',encoding='utf-8') as f:json.dump(manifest,f,indent=2)
    print(output)
    print(json.dumps({'status':result['status'],'orders':result['orders'],'rejections':result['rejections']},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':main()
