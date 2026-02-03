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

detect_nonempty_jsonl() {
  local f="$1"
  if [[ -f "$f" ]] && [[ -s "$f" ]]; then
    if grep -q "[^[:space:]]" "$f"; then
      return 0
    fi
  fi
  return 1
}

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

  # Use path-hash in output folder to avoid collisions when multiple files share the same name
  local file_hash
  file_hash="$(echo -n "$xel" | shasum -a 256 | awk '{print substr($1,1,8)}')"

  local FILE_OUT
  FILE_OUT="$OUT_DIR/${safe_base}_${file_hash}"
  mkdir -p "$FILE_OUT"

  local ts
  ts="$(date +%Y%m%d_%H%M%S)"
  local prefix="${base}_${file_hash}_${ts}"

  echo "==> Processing: $xel"

  # Branch by input type
  if [[ "$ext" == "csv" || "$ext" == "CSV" || "$ext" == "xlsx" || "$ext" == "xls" || "$ext" == "XLSX" || "$ext" == "XLS" ]]; then
    local RUN_TAG
    RUN_TAG="$(date +%Y%m%d_%H%M%S)"

    local WS
    WS="${XEL_TOOLKIT_WORKSPACE:-}"

    if [[ -n "$WS" ]]; then
      local inputmd_mode
      inputmd_mode="${XEL_TOOLKIT_INPUTMD_MODE:-overwrite}"
      local file_hash
      file_hash="$(echo -n "$xel" | shasum -a 256 | awk '{print substr($1,1,8)}')"
      local INPUTMD
      INPUTMD="$WS/inputMD/${safe_base}_${file_hash}"
      if [[ "$inputmd_mode" == "overwrite" && -d "$INPUTMD" ]]; then
        rm -rf "$INPUTMD" || true
      fi
      mkdir -p "$INPUTMD"
      # NOTE: inputMD is unfiltered; ranges-json is ignored for csv/excel export
      XEL_TOOLKIT_RANGES_JSON= python3 "$REPO_ROOT/py/csv_excel_to_md.py" --in "$xel" --out "$INPUTMD" --run-tag "$RUN_TAG" >/dev/null
    else
      mkdir -p "$FILE_OUT/md"
      python3 "$REPO_ROOT/py/csv_excel_to_md.py" --in "$xel" --out "$FILE_OUT/md" --run-tag "$RUN_TAG" ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
    fi

    echo "   Done (csv/excel->md): $base"
    return
  fi

  # XEL: Always generate summary (per input folder)
  "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" -o "$FILE_OUT" --max 2000 >/dev/null

  # Content-based detection (do not rely on filename)
  local WS
  WS="${XEL_TOOLKIT_WORKSPACE:-}"

  # Deadlock
  local deadlock_jsonl
  deadlock_jsonl="$OUT_DIR/tmp/${prefix}_deadlock.jsonl"
  "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$deadlock_jsonl" --filter xml_deadlock_report >/dev/null || true

  if detect_nonempty_jsonl "$deadlock_jsonl"; then
    local KEY
    KEY="$(echo "$prefix" | shasum -a 256 | awk '{print substr($1,1,8)}')"

    if [[ -n "$WS" ]]; then
      # NOTE: inputMD is unfiltered (always export all events to markdown)
      # inputMD dir is path-based to avoid collisions
      local inputmd_mode
      inputmd_mode="${XEL_TOOLKIT_INPUTMD_MODE:-overwrite}"
      local file_hash
      file_hash="$(echo -n "$xel" | shasum -a 256 | awk '{print substr($1,1,8)}')"
      local inputmd_dir
      inputmd_dir="$WS/inputMD/${safe_base}_${file_hash}"
      if [[ "$inputmd_mode" == "overwrite" && -d "$inputmd_dir" ]]; then
        rm -rf "$inputmd_dir" || true
      fi
      XEL_TOOLKIT_RANGES_JSON= python3 "$REPO_ROOT/py/xel_to_md.py" --jsonl "$deadlock_jsonl" --out "$inputmd_dir" --key "$KEY" --event xml_deadlock_report --source "$xel" >/dev/null
    fi

    python3 "$REPO_ROOT/py/generate_reports.py" \
      --deadlock-jsonl "$deadlock_jsonl" \
      --source-xel "$xel" \
      --prefix "$prefix" \
      --out "$FILE_OUT" \
      ${START_JST:+--start "$START_JST"} \
      ${END_JST:+--end "$END_JST"} \
      ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
  fi

  # Blocking
  local blocking_jsonl
  blocking_jsonl="$OUT_DIR/tmp/${prefix}_blocking.jsonl"
  "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$blocking_jsonl" --filter blocked_process_report >/dev/null || true

  if detect_nonempty_jsonl "$blocking_jsonl"; then
    local KEY
    KEY="$(echo "$prefix" | shasum -a 256 | awk '{print substr($1,1,8)}')"

    if [[ -n "$WS" ]]; then
      # NOTE: inputMD is unfiltered (always export all events to markdown)
      # inputMD dir is path-based to avoid collisions
      local inputmd_mode
      inputmd_mode="${XEL_TOOLKIT_INPUTMD_MODE:-overwrite}"
      local file_hash
      file_hash="$(echo -n "$xel" | shasum -a 256 | awk '{print substr($1,1,8)}')"
      local inputmd_dir
      inputmd_dir="$WS/inputMD/${safe_base}_${file_hash}"
      if [[ "$inputmd_mode" == "overwrite" && -d "$inputmd_dir" ]]; then
        rm -rf "$inputmd_dir" || true
      fi
      XEL_TOOLKIT_RANGES_JSON= python3 "$REPO_ROOT/py/xel_to_md.py" --jsonl "$blocking_jsonl" --out "$inputmd_dir" --key "$KEY" --event blocked_process_report --source "$xel" >/dev/null
    fi

    python3 "$REPO_ROOT/py/generate_reports.py" \
      --blocking-jsonl "$blocking_jsonl" \
      --source-xel "$xel" \
      --prefix "$prefix" \
      --out "$FILE_OUT" \
      ${START_JST:+--start "$START_JST"} \
      ${END_JST:+--end "$END_JST"} \
      ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
  fi

  # SlowQuery (rpc_completed + sql_batch_completed)
  local slow_jsonl
  slow_jsonl="$OUT_DIR/tmp/${prefix}_slow.jsonl"
  rm -f "$slow_jsonl" || true

  local slow_rpc
  slow_rpc="$OUT_DIR/tmp/${prefix}_slow_rpc.jsonl"
  "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$slow_rpc" --filter rpc_completed >/dev/null || true

  local slow_batch
  slow_batch="$OUT_DIR/tmp/${prefix}_slow_batch.jsonl"
  "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$slow_batch" --filter sql_batch_completed >/dev/null || true

  if [[ -f "$slow_rpc" ]]; then cat "$slow_rpc" >> "$slow_jsonl"; fi
  if [[ -f "$slow_batch" ]]; then cat "$slow_batch" >> "$slow_jsonl"; fi

  if detect_nonempty_jsonl "$slow_jsonl"; then
    local KEY
    KEY="$(echo "$prefix" | shasum -a 256 | awk '{print substr($1,1,8)}')"

    if [[ -n "$WS" ]]; then
      # NOTE: inputMD is unfiltered (always export all events to markdown)
      # inputMD dir is path-based to avoid collisions
      local inputmd_mode
      inputmd_mode="${XEL_TOOLKIT_INPUTMD_MODE:-overwrite}"
      local file_hash
      file_hash="$(echo -n "$xel" | shasum -a 256 | awk '{print substr($1,1,8)}')"
      local inputmd_dir
      inputmd_dir="$WS/inputMD/${safe_base}_${file_hash}"
      if [[ "$inputmd_mode" == "overwrite" && -d "$inputmd_dir" ]]; then
        rm -rf "$inputmd_dir" || true
      fi
      XEL_TOOLKIT_RANGES_JSON= python3 "$REPO_ROOT/py/xel_to_md.py" --jsonl "$slow_jsonl" --out "$inputmd_dir" --key "$KEY" --event rpc_completed --source "$xel" >/dev/null
      XEL_TOOLKIT_RANGES_JSON= python3 "$REPO_ROOT/py/xel_to_md.py" --jsonl "$slow_jsonl" --out "$inputmd_dir" --key "$KEY" --event sql_batch_completed --source "$xel" >/dev/null
    fi

    python3 "$REPO_ROOT/py/generate_reports.py" \
      --slowquery-jsonl "$slow_jsonl" \
      --slow-threshold "$SLOW_THRESHOLD" \
      --source-xel "$xel" \
      --prefix "$prefix" \
      --out "$FILE_OUT" \
      ${START_JST:+--start "$START_JST"} \
      ${END_JST:+--end "$END_JST"} \
      ${XEL_TOOLKIT_RANGES_JSON:+--ranges-json "$XEL_TOOLKIT_RANGES_JSON"} >/dev/null
  fi

  rm -f "$slow_rpc" "$slow_batch" || true

  echo "   Done: $prefix"
}

for xel in "${xel_files[@]}"; do
  run_one "$xel"
done

echo "All done. Reports in: $OUT_DIR"
