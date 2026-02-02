# -*- coding: utf-8 -*-
import json, argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--scene", default="movie")
    args = ap.parse_args()

    out = {
        "request_id": "dev",
        "version": "tv-agent-cli/0.1",
        "ok": True,
        "data": {
            "a": {"device_id": args.a},
            "b": {"device_id": args.b},
            "diffs": [],
            "summary": {"recommendation": "", "confidence": 0.5}
        }
    }
    print(json.dumps(out, ensure_ascii=False))
if __name__ == "__main__":
    main()
