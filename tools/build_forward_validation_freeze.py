"""固化前向验证起点，防止后续用新数据回改策略参数。"""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def digest(path: Path):
    h=hashlib.sha256();
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def main():
    files=[ROOT/"src/ashare_lab/selection/engine.py",ROOT/"src/ashare_lab/selection/strategy.py",ROOT/"src/ashare_lab/sources/inferred_rule_audit.py",ROOT/"docs/DECISION_20260907_RESEARCH_VALIDATION.md",ROOT/"data/canonical/zzshare_rule_audit_20260902/manifest.json"]
    result={"status":"FROZEN_WAITING_FOR_FORWARD_SIGNAL","freeze_time":datetime.now(timezone.utc).isoformat(),"historical_cutoff":"2026-09-01","expected_first_forward_signal":"2026-09-30","strategy_parameters":{"halt_policy":"short5","execution_policy":"baseline","portfolio_size":30,"target_exposure":0.95,"signal_frequency":"month_end","cost_model":"HISTORICAL_RESEARCH_V1"},"files":{str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in files if p.exists()},"external_rule_snapshot":"data/canonical/zzshare_rule_audit_20260902","constraints":["新数据不得反向调整参数","不完整交易日不得进入正式前向指标","buyable/sellable/suspend_reason 缺失时不得升级 paper replay","实盘接口保持关闭"],"next_action":"等待完整月末信号及其后 T+1 成交窗口"}
    out=ROOT/"reports/FORWARD_VALIDATION_FREEZE_20260928.json"; out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
