#!/usr/bin/env python
"""生成标准数据层。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.canonical_data import build  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="从只读导入快照生成标准数据层")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    result = build(args.project_root.resolve())
    summary = {
        "status": result["quality"]["status"],
        "output_files": result["manifest"]["output"]["files"],
        "output_rows": result["manifest"]["output"]["rows"],
        "output_bytes": result["manifest"]["output"]["bytes"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
