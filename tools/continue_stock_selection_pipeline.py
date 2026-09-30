#!/usr/bin/env python
"""等待全量抓取完成后，按闸门顺序继续选股MVP流水线。"""

from __future__ import annotations

import argparse
import ctypes
import os
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = PROJECT_ROOT / "reports" / "stock_selection_pipeline_status.json"


def write_status(status: str, step: str, **details) -> None:
    payload = {
        "status": status,
        "step": step,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        **details,
    }
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def run_tool(*arguments: str) -> None:
    command = [sys.executable, *arguments]
    print("pipeline_run " + " ".join(arguments), flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def windows_process_running(pid: int) -> bool:
    """仅查询进程退出状态；不能使用Windows上的os.kill(pid, 0)。"""
    if os.name != "nt": raise RuntimeError("fetch-pid检查当前只支持Windows")
    from ctypes import wintypes
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        error=ctypes.get_last_error()
        if error in (87,1168): return False
        raise OSError(error,"无法只读核对下载进程")
    try:
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)): raise ctypes.WinError(ctypes.get_last_error())
        return code.value==259
    finally: kernel.CloseHandle(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="等待并继续历史沪深300选股MVP")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--timeout-hours", type=float, default=12)
    parser.add_argument("--fetch-pid",type=int,help="只读核对当前下载进程是否提前退出")
    args = parser.parse_args()
    snapshot = args.snapshot.resolve()
    imports_root = (PROJECT_ROOT / "data" / "imports").resolve()
    if snapshot.parent != imports_root or not snapshot.name.startswith("baostock_historical_daily_"):
        raise RuntimeError(f"快照目录无效: {snapshot}")

    deadline = datetime.now() + timedelta(hours=args.timeout_hours)
    last_reported_files = -1
    write_status("RUNNING", "WAITING_FOR_RAW_FETCH", snapshot=snapshot.name)
    while not (snapshot / "manifest.json").is_file():
        if args.fetch_pid is not None and not windows_process_running(args.fetch_pid):
            if (snapshot/"manifest.json").is_file(): break
            write_status("FAILED","WAITING_FOR_RAW_FETCH",snapshot=snapshot.name,reason="FETCH_PROCESS_EXITED",fetch_pid=args.fetch_pid)
            return 1
        status_path=PROJECT_ROOT/"reports/historical_fetch_status.json"
        if status_path.exists():
            state=json.loads(status_path.read_text(encoding="utf-8"))
            if state.get("snapshot")==snapshot.name and state.get("status") in {"FAILED","PAUSED"} and (args.fetch_pid is None or state.get("pid")==args.fetch_pid):
                write_status(state["status"],"WAITING_FOR_RAW_FETCH",snapshot=snapshot.name,fetch_state=state)
                return 1
        if datetime.now() >= deadline:
            write_status("FAILED", "WAITING_FOR_RAW_FETCH", reason="TIMEOUT", snapshot=snapshot.name)
            return 1
        files = sum(1 for _ in snapshot.rglob("*.csv"))
        if files != last_reported_files:
            state=json.loads((snapshot/"run_state.json").read_text(encoding="utf-8"))
            write_status("RUNNING", "WAITING_FOR_RAW_FETCH", snapshot=snapshot.name, csv_files=files,
                         expected_files=state.get("expected_data_files"),fetch_pid=args.fetch_pid)
            last_reported_files = files
        time.sleep(30)

    raw_manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if raw_manifest.get("status") == "SEALED_PENDING_INDEPENDENT_VALIDATION":
        write_status("RUNNING", "VALIDATING_RAW_SNAPSHOT", snapshot=snapshot.name)
        run_tool("tools/validate_baostock_historical_daily.py", "--snapshot", str(snapshot))
        raw_manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if raw_manifest.get("status") != "SEALED":
        write_status("FAILED", "VALIDATING_RAW_SNAPSHOT", snapshot=snapshot.name, raw_status=raw_manifest.get("status"))
        return 1

    canonical = PROJECT_ROOT / "data" / "canonical" / "stock_selection_v1"
    if not canonical.exists():
        write_status("RUNNING", "BUILDING_CANONICAL", snapshot=snapshot.name)
        run_tool("tools/build_stock_selection_canonical.py", "--snapshot", str(snapshot))
    canonical_manifest_path = canonical / "manifest.json"
    if not canonical_manifest_path.is_file():
        write_status("FAILED", "BUILDING_CANONICAL", reason="MISSING_MANIFEST")
        return 1
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    if canonical_manifest.get("status") == "SEALED_PENDING_INDEPENDENT_VALIDATION":
        write_status("RUNNING", "VALIDATING_CANONICAL")
        run_tool("tools/validate_stock_selection_canonical.py", "--canonical", str(canonical))
        canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    if canonical_manifest.get("status") != "SEALED":
        write_status("FAILED", "VALIDATING_CANONICAL", canonical_status=canonical_manifest.get("status"))
        return 1

    write_status("RUNNING", "RUNNING_RESEARCH_MVP")
    run_tool("tools/run_stock_selection_mvp.py")
    pending = []
    for path in (PROJECT_ROOT / "reports").glob("stock_selection_mvp_*"):
        manifest_path = path / "run_manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "SEALED_PENDING_INDEPENDENT_VALIDATION":
            pending.append(path)
    if not pending:
        write_status("FAILED", "VALIDATING_RESEARCH_MVP", reason="NO_PENDING_RUN")
        return 1
    run = sorted(pending)[-1]
    write_status("RUNNING", "VALIDATING_RESEARCH_MVP", run=run.name)
    run_tool("tools/validate_stock_selection_mvp_run.py", "--run", str(run))
    run_manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    if run_manifest.get("status") != "SEALED":
        write_status("FAILED", "VALIDATING_RESEARCH_MVP", run=run.name, run_status=run_manifest.get("status"))
        return 1
    write_status(
        "COMPLETED",
        "DONE",
        snapshot=snapshot.name,
        canonical=canonical.name,
        run=run.name,
        research_gate=run_manifest.get("research_gate"),
        evidence_class=run_manifest.get("evidence_class"),
        forward_validation_status=run_manifest.get("forward_validation_status"),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        write_status("FAILED", "UNHANDLED_EXCEPTION", error=f"{type(exc).__name__}: {exc}")
        raise
