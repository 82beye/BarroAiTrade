#!/bin/bash
cd /Users/beye82/Workspace/BarroAiTrade
set -a; . ./.env.local; set +a
exec ./.venv/bin/python scripts/run_telegram_bot.py
