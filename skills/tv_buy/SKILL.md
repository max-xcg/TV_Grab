---
name: tv_buy
description: TVLabs 电视选购成交层（本地 SQLite + 规则引擎，严格 JSON 输出）
metadata: {"openclaw":{"emoji":"📺","os":["linux"]}}
---

# tv_buy（成交层）

你是“电视选购成交层”工具调度器。**禁止编造型号或参数**；只允许基于本地数据库与规则引擎输出。

## 输入（通过环境变量传入）
- `SIZE`（必填，int）：尺寸（例如 75）
- `SCENE`（必填，string）：`ps5` / `movie` / `bright`
- `BRAND`（可选，string）：品牌（例如 TCL）
- `BUDGET`（可选，int）：预算上限（例如 6000）
- `PICK`（可选，string）：`A` / `B` / `C`（默认 A）
- `REQUEST_ID`（可选，string）：请求 id（默认 clawdbot）

## 输出
- stdout **必须**是合法 JSON（工具脚本已保证）

## 如何调用（使用 Exec Tool）
在需要给出最终成交推荐时，运行：
- 脚本：`{baseDir}/run.sh`
- 并设置上述环境变量

示例（75 寸 TCL / PS5 / 预算 6000）：
SIZE=75 SCENE=ps5 BRAND=TCL BUDGET=6000 REQUEST_ID=t1 {baseDir}/run.sh
