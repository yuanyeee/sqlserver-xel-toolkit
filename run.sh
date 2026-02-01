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
  local base
  base="$(basename "$xel")"
  base="${base%.xel}"

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

  # Always generate summary (per input folder)
  "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" -o "$FILE_OUT" --max 2000 >/dev/null

  # Deadlock
  if [[ "$base" == *deadlock* ]]; then
    "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$OUT_DIR/tmp/${prefix}_deadlock.jsonl" --filter xml_deadlock_report >/dev/null
    python3 "$REPO_ROOT/py/generate_reports.py" \
      --deadlock-jsonl "$OUT_DIR/tmp/${prefix}_deadlock.jsonl" \
      --source-xel "$xel" \
      --prefix "$prefix" \
      --out "$FILE_OUT" \
      ${START_JST:+--start "$START_JST"} \
      ${END_JST:+--end "$END_JST"} >/dev/null
  fi

  # Slow queries
  if [[ "$base" == *Slow_Queries* || "$base" == *slow* ]]; then
    "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$OUT_DIR/tmp/${prefix}_slow.jsonl" >/dev/null
    python3 "$REPO_ROOT/py/generate_reports.py" \
      --slowquery-jsonl "$OUT_DIR/tmp/${prefix}_slow.jsonl" \
      --slow-threshold "$SLOW_THRESHOLD" \
      --source-xel "$xel" \
      --prefix "$prefix" \
      --out "$FILE_OUT" \
      ${START_JST:+--start "$START_JST"} \
      ${END_JST:+--end "$END_JST"} >/dev/null
  fi

  # Blocking
  if [[ "$base" == *blocking* ]]; then
    "$DOTNET" run --project "$REPO_ROOT/src/XelDump" -- "$xel" --export-jsonl "$OUT_DIR/tmp/${prefix}_blocking.jsonl" --filter blocked_process_report >/dev/null
    python3 "$REPO_ROOT/py/generate_reports.py" \
      --blocking-jsonl "$OUT_DIR/tmp/${prefix}_blocking.jsonl" \
      --source-xel "$xel" \
      --prefix "$prefix" \
      --out "$FILE_OUT" \
      ${START_JST:+--start "$START_JST"} \
      ${END_JST:+--end "$END_JST"} >/dev/null
  fi

  echo "   Done: $prefix"
}

for xel in "${xel_files[@]}"; do
  run_one "$xel"
done

echo "All done. Reports in: $OUT_DIR"
