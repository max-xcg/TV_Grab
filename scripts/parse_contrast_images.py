# -*- coding: utf-8 -*-
import argparse
import yaml

from app.ingest.parsers.contrast import ContrastImageParser


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", required=True, help="原生对比度图片路径")
    ap.add_argument("--effective", required=True, help="有效对比度图片路径")
    ap.add_argument("--meta", default=None, help="meta 表格图片路径（可选）")
    ap.add_argument("--out", required=True, help="输出 YAML 路径")
    args = ap.parse_args()

    parser = ContrastImageParser()
    record = parser.parse(args.native, args.effective, args.meta)

    out_dict = record.to_yaml_dict()
    with open(args.out, "w", encoding="utf-8") as f:
        yaml.safe_dump(out_dict, f, allow_unicode=True, sort_keys=False)

    print(f"[OK] wrote: {args.out}")


if __name__ == "__main__":
    main()
