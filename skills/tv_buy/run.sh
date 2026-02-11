#!/usr/bin/env bash
set -euo pipefail

# 固定到工程根目录，避免相对路径错
ROOT="/mnt/c/Users/admin/tvlabs_scraper/TVLabs/TV_Grab"
cd "$ROOT"

# 必填参数（来自 env）
: "${SIZE:?missing SIZE}"
: "${SCENE:?missing SCENE}"

BRAND_ARG=()
BUDGET_ARG=()
PICK_ARG=()

if [[ -n "${BRAND:-}" ]]; then BRAND_ARG=(--brand "$BRAND"); fi
if [[ -n "${BUDGET:-}" ]]; then BUDGET_ARG=(--budget "$BUDGET"); fi
if [[ -n "${PICK:-}" ]]; then PICK_ARG=(--pick "$PICK"); fi

REQ_ID="${REQUEST_ID:-clawdbot}"

exec python3 tv_buy_1_0/tools_cli/tv_pick.py \
  --size "$SIZE" \
  --scene "$SCENE" \
  "${BRAND_ARG[@]}" \
  "${BUDGET_ARG[@]}" \
  "${PICK_ARG[@]}" \
  --request_id "$REQ_ID"
