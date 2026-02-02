# -*- coding: utf-8 -*-
import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser(description="TV search (MVP)")
    ap.add_argument("--size", type=int, required=True, help="screen size in inch")
    ap.add_argument("--budget_max", type=int, required=True, help="max budget")
    ap.add_argument("--region", default="CN", help="region code")
    args = ap.parse_args()

    # TODO: 替换为真实逻辑：从 SQLite / YAML 索引读取候选
    data = {
        "filters": {
            "size": args.size,
            "budget_max": args.budget_max,
            "region": args.region,
        },
        "candidates": [],
        "count": 0,
    }

    out = {
        "request_id": "dev",
        "version": "tv-agent-cli/0.1",
        "ok": True,
        "data": data,
    }

    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
