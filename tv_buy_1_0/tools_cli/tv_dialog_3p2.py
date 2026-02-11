# -*- coding: utf-8 -*-
"""
tv_buy_1_0/tools_cli/tv_dialog_3p2.py

运行：
  /c/software/Anaconda3/python.exe -m tv_buy_1_0.tools_cli.tv_dialog_3p2
"""

from __future__ import annotations

import json
from typing import Dict, Any

from tv_buy_1_0.agent.dialogue_3p2 import Dialogue3p2


def _pp_state(d: Dict[str, Any]) -> str:
    return json.dumps(d, ensure_ascii=False, indent=2)


def main() -> None:
    dlg = Dialogue3p2()
    state: Dict[str, Any] = dlg.reset_state().to_dict()

    print("=== TV Dialog 3+2（固定4问版｜无score｜新机型优先） ===")
    print("输入 exit 退出；输入 reset 重置。\n")

    print("助手：")
    print("Q1/4：你要多大尺寸？（例：75 / 65 / 85）")
    print("\n--- state ---")
    print(_pp_state(state))
    print("=============\n")

    while True:
        try:
            user = input("你：").strip()
        except KeyboardInterrupt:
            print("\nbye")
            return

        out = dlg.chat(user_text=user, state_dict=state)
        state = out.get("state") or state
        reply = out.get("reply", "")

        print("\n助手：")
        print(reply)
        print("\n--- state ---")
        print(_pp_state(state))
        print("=============\n")

        if out.get("done") and user.lower() == "exit":
            return


if __name__ == "__main__":
    main()
