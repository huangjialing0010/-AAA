"""Run one idempotent close-cycle orchestration.

The cycle never fabricates quotes and never sends real orders. Each child tool
keeps its own sealed evidence and terminal/idempotency checks.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run_tool(name, *args):
    command = [PYTHON, '-X', 'utf8', str(ROOT/'tools'/name), *args]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    return {'tool': name, 'returncode': result.returncode,
            'stdout': result.stdout[-4000:], 'stderr': result.stderr[-4000:]}


def main():
    started = datetime.now(timezone.utc).isoformat()
    steps = []
    steps.append(run_tool('fetch_manual_quote.py'))
    # Execution tool performs terminal and evidence gates; missing review remains
    # a recorded wait, never an implicit approval.
    steps.append(run_tool('check_manual_execution.py'))
    steps.append(run_tool('build_manual_account_report.py'))
    payload = {'kind': 'MANUAL_CLOSE_CYCLE_V1', 'started_at': started,
               'mode': 'SIMULATION_ONLY', 'steps': steps,
               'next': 'inspect sealed evidence and current report'}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if any(step['returncode'] != 0 for step in steps):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
