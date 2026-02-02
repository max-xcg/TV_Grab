# -*- coding: utf-8 -*-
import json, argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--preset", required=True)
    ap.add_argument("--input_json", required=True)  # 直接传 candidates JSON 字符串
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    candidates = json.loads(args.input_json) if args.input_json else []
    ranked = []  # TODO: 接入你的评分逻辑

    out = {
        "request_id": "dev",
        "version": "tv-agent-cli/0.1",
        "ok": True,
        "data": {
            "scene": args.scene,
            "preset_id": args.preset,
            "ranked": ranked[:args.top]
        }
    }
    print(json.dumps(out, ensure_ascii=False))
if __name__ == "__main__":
    main()
