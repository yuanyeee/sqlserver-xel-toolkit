#!/usr/bin/env bash
set -euo pipefail

DOTNET="/usr/local/share/dotnet/dotnet"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$REPO_ROOT/reports"
SLOW_THRESHOLD="3"
START_JST=""
END_JST=""

usage() {
  cat <<'EOF'
Usage:
  ./run.sh <xel path|glob> [more xel ...] [-o OUT_DIR] [--slow-threshold SEC] [--start "YYYY-mm-dd HH:MM"] [--end "YYYY-mm-dd HH:MM"]

Examples:
  ./run.sh ~/Downloads/deadlock*.xel
  ./run.sh ~/Downloads/*.xel -o ./reports
EOF
}

# Parse args (very simple)
inputs=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage; exit 0;;
    -o|--out)
      OUT_DIR="$2"; shift 2;;
    --slow-threshold)
      SLOW_THRESHOLD="$2"; shift 2;;
    --start)
      START_JST="$2"; shift 2;;
    --end)
      END_JST="$2"; shift 2;;
    *)
      inputs+=("$1"); shift;;
  esac
done

if [[ ${#inputs[@]} -eq 0 ]]; then
  usage; exit 2
fi

mkdir -p "$OUT_DIR" "$OUT_DIR/tmp"

# Expand globs (support simple wildcards)
xel_files=()
for i in "${inputs[@]}"; do
  if compgen -G "$i" > /dev/null; then
    for f in $i; do
      xel_files+=("$f")
    done
  else
    xel_files+=("$i")
  fi
done

if [[ ${#xel_files[@]} -eq 0 ]]; then
  echo "No .xel files matched." >&2
  exit 2
fi

run_one() {
  local xel="$1"
  local ext
  ext="${xel##*.}"
  local base
  base="$(basename "$xel")"
  base="${base%.*}"

  # Per-input subfolder (safe name)
  local safe_base
  safe_base="$(echo "$base" | sed -E 's/[^A-Za-z0-9._-]+/_/g; s/^_+//; s/_+$//')"
  if [[ -z "$safe_base" ]]; then safe_base="input"; fi

  local FILE_OUT
  FILE_OUT="$OUT_DIR/$safe_base"
  mkdir -p "$FILE_OUT"

  local ts
  ts="$(date +%Y%m%d_%H%M%S)"
  local prefix="${base}_${ts}"

  echo "==> Processing: $xel"

  # Branch by input type
  if [[ "$ext" == "csv" || "$ext" == "CSV" || "$ext" == "xlsx" || "$ext" == "xls" || "$ext" == "XLSX" || "$ext" == "XLS" ]]; then
    # CSV/Excel -> per-row MD (unified under workspace inputMD if provided)
    local RUN_TAG
    RUN_TAG="$(date +%Y%m%d_%H%M%S)"

    local WS
    WS="${XEL_TOOLKIT_WORKSPACE:-}"

    if [[ -n "$WS" ]]; then
      local INPUTMD
      INPUTMD="$WS/inputMD/$safe_base"
      mkdir -p "$INPUTMD"
      python3 "$REPO_ROOT/py/csv_excel_to_md.py" --in "$xel" --out "$INPUTMD" --run-tag "$RUN_TAG" ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
    else
      mkdir -p "$FILE_OUT/md"
      python3 "$REPO_ROOT/py/csv_excel_to_md.py" --in "$xel" --out "$FILE_OUT/md" --run-tag "$RUN_TAG" ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
    fi

    echo "   Done (csv/excel->md): $base"
    return
  fi

  # XEL: Always generate summary (per input folder)
  "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" -o "$FILE_OUT" --max 2000 >/dev/null

  # Deadlock
  if [[ "$base" == *deadlock* ]]; then
    local KEY
    KEY="$(echo "$prefix" | shasum -a 256 | awk '{print substr($1,1,8)}')"
    "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$OUT_DIR/tmp/${prefix}_deadlock.jsonl" --filter xml_deadlock_report >/dev/null

    # Generate per-event MD into inputMD (workspace-aware)
    local WS
    WS="${XEL_TOOLKIT_WORKSPACE:-}"
    if [[ -n "$WS" ]]; then
      python3 "$REPO_ROOT/py/xel_to_md.py" --jsonl "$OUT_DIR/tmp/${prefix}_deadlock.jsonl" --out "$WS/inputMD/$safe_base" --key "$KEY" --event xml_deadlock_report --source "$xel" ${START_JST:+--start "$START_JST"} ${END_JST:+--end "$END_JST"} ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
    fi

    python3 "$REPO_ROOT/py/generate_reports.py" \
      --deadlock-jsonl "$OUT_DIR/tmp/${prefix}_deadlock.jsonl" \
      --source-xel "$xel" \
      --prefix "$prefix" \
      --out "$FILE_OUT" \
      ${START_JST:+--start "$START_JST"} \
      ${END_JST:+--end "$END_JST"} \
      ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
  fi

  # Slow queries
  if [[ "$base" == *Slow_Queries* || "$base" == *slow* ]]; then
    local KEY
    KEY="$(echo "$prefix" | shasum -a 256 | awk '{print substr($1,1,8)}')"
    "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$OUT_DIR/tmp/${prefix}_slow.jsonl" >/dev/null

    # Generate per-event MD into inputMD (workspace-aware)
    local WS
    WS="${XEL_TOOLKIT_WORKSPACE:-}"
    if [[ -n "$WS" ]]; then
      # includes both rpc_completed and sql_batch_completed
      python3 "$REPO_ROOT/py/xel_to_md.py" --jsonl "$OUT_DIR/tmp/${prefix}_slow.jsonl" --out "$WS/inputMD/$safe_base" --key "$KEY" --event rpc_completed --source "$xel" ${START_JST:+--start "$START_JST"} ${END_JST:+--end "$END_JST"} ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
      python3 "$REPO_ROOT/py/xel_to_md.py" --jsonl "$OUT_DIR/tmp/${prefix}_slow.jsonl" --out "$WS/inputMD/$safe_base" --key "$KEY" --event sql_batch_completed --source "$xel" ${START_JST:+--start "$START_JST"} ${END_JST:+--end "$END_JST"} ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
    fi

    python3 "$REPO_ROOT/py/generate_reports.py" \
      --slowquery-jsonl "$OUT_DIR/tmp/${prefix}_slow.jsonl" \
      --slow-threshold "$SLOW_THRESHOLD" \
      --source-xel "$xel" \
      --prefix "$prefix" \
      --out "$FILE_OUT" \
      ${START_JST:+--start "$START_JST"} \
      ${END_JST:+--end "$END_JST"} \
      ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
  fi

  # Blocking
  if [[ "$base" == *blocking* ]]; then
    local KEY
    KEY="$(echo "$prefix" | shasum -a 256 | awk '{print substr($1,1,8)}')"
    "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$OUT_DIR/tmp/${prefix}_blocking.jsonl" --filter blocked_process_report >/dev/null

    # Generate per-event MD into inputMD (workspace-aware)
    local WS
    WS="${XEL_TOOLKIT_WORKSPACE:-}"
    if [[ -n "$WS" ]]; then
      python3 "$REPO_ROOT/py/xel_to_md.py" --jsonl "$OUT_DIR/tmp/${prefix}_blocking.jsonl" --out "$WS/inputMD/$safe_base" --key "$KEY" --event blocked_process_report --source "$xel" ${START_JST:+--start "$START_JST"} ${END_JST:+--end "$END_JST"} ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
    fi

    python3 "$REPO_ROOT/py/generate_reports.py" \
      --blocking-jsonl "$OUT_DIR/tmp/${prefix}_blocking.jsonl" \
      --source-xel "$xel" \
      --prefix "$prefix" \
      --out "$FILE_OUT" \
      ${START_JST:+--start "$START_JST"} \
      ${END_JST:+--end "$END_JST"} \
      ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
  fi

  echo "   Done: $prefix"
}

for xel in "${xel_files[@]}"; do
  run_one "$xel"
done

echo "All done. Reports in: $OUT_DIR"
