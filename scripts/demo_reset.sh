#!/usr/bin/env bash
# Put the machine into a known state for recording the demo.
#
# Two things it does, for two different reasons:
#
#   Clears the profile and the acknowledgement ledger, so onboarding and the
#   speak-then-go-quiet loop can both be shown from the start.
#
#   Leaves the page snapshots in place and warms them with one throwaway run.
#   A cold run spends ~15s reading three gov.uk pages, which on video is
#   fifteen seconds of nothing happening. Warm it is ~5s. The snapshots change
#   nothing about what the agent concludes — it still fetches every page and
#   still compares digests; only the extraction is reused.
#
# Usage:  bash scripts/demo_reset.sh
set -euo pipefail

PYTHON="${PYTHON:-.venv/Scripts/python.exe}"
DATA="${AFH_DATA_DIR:-data}"

echo "→ clearing profile and acknowledgements"
rm -f "$DATA/profile.json" "$DATA/acknowledged.json"

echo "→ warming the page cache (one throwaway run, output discarded)"
"$PYTHON" - >/dev/null 2>&1 <<'PYEOF'
from src.models import UserSituation
from src.pipeline import run
run(UserSituation(is_sole_trader=True, has_property_income=False,
                  prior_year_turnover_gbp=62_000, prior_year_label="2024 to 2025",
                  rolling_12m_turnover_gbp=71_000,
                  expects_to_exceed_vat_threshold_soon=False),
    acknowledged=frozenset())
PYEOF

echo
echo "✓ ready to record."
echo "  profile:         none — 'check' will run onboarding"
echo "  acknowledged:    none — the first check will speak"
echo "  snapshots:       warm — runs take ~5s instead of ~15s"
echo
echo "  Scene 4 (the rules moved) needs run_failure_modes.py, which writes its"
echo "  own doctored snapshot and restores nothing. Re-run this script before"
echo "  recording any later scene."
