"""命令行入口：``python -m library_access_equity --data data``。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import build_report
from .input_contract import dataset_presence, load_bundle
from .summaries import parse_date


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="library-access-equity",
        description="生成乡镇服务证据报告与流动资源建议（只读原始资料）。",
    )
    parser.add_argument("--data", required=True, type=Path, help="资料包目录")
    parser.add_argument(
        "--as-of",
        default=None,
        help="报告截止日期（ISO，含当日）；此前记录才计入当时成绩",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是文本")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.as_of is not None and parse_date(args.as_of) is None:
        print(f"无效的 --as-of 日期：{args.as_of}（需要 ISO 日期，例如 2026-10-01）", file=sys.stderr)
        return 2
    try:
        bundle = load_bundle(args.data)
    except (FileNotFoundError, ValueError) as error:
        print(f"读取资料失败：{error}", file=sys.stderr)
        return 2

    report = build_report(
        bundle,
        as_of=args.as_of,
        dataset_presence=dataset_presence(bundle),
    )
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.render_text(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
