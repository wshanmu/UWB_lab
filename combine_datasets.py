#!/usr/bin/env python3

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from uwb_lab_common import LAB_DIR, timestamp


FIELDNAMES = [
    "dataset_name",
    "source_dataset",
    "collector",
    "gesture",
    "input_type",
    "trial_index",
    "attempt_index",
    "trial_duration_s",
    "fps",
    "ranging_span_ms",
    "slot_span",
    "slots_per_rr",
    "session_dir",
    "return_code",
    "controller_samples",
    "controller_ok_samples",
    "sequence_start",
    "sequence_end",
    "started_at",
    "finished_at",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Combine trials.csv files from multiple UWB gesture datasets."
    )
    parser.add_argument(
        "datasets",
        nargs="+",
        help="Dataset folders created by collect_dataset.py.",
    )
    parser.add_argument(
        "--output",
        default=str(LAB_DIR / "datasets" / f"combined_gesture_dataset_{timestamp()}"),
        help="Output dataset folder. Default: datasets/combined_gesture_dataset_<timestamp>",
    )
    parser.add_argument(
        "--collector",
        action="append",
        help="Keep only this collector. Can be repeated.",
    )
    parser.add_argument(
        "--gesture",
        action="append",
        help="Keep only this gesture. Can be repeated.",
    )
    parser.add_argument(
        "--allow-missing-session",
        action="store_true",
        help="Keep rows even if the referenced session_dir does not exist.",
    )
    return parser.parse_args()


def read_rows(dataset_dir):
    manifest = dataset_dir / "trials.csv"
    if not manifest.exists():
        raise FileNotFoundError(f"missing trials.csv: {manifest}")
    with open(manifest, newline="") as f:
        return list(csv.DictReader(f))


def normalize_filter(values):
    if not values:
        return None
    normalized = set()
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                normalized.add(part)
    return normalized


def source_dataset_name(dataset_dir):
    metadata = dataset_dir / "dataset_metadata.json"
    if metadata.exists():
        try:
            return json.loads(metadata.read_text()).get("dataset_name") or dataset_dir.name
        except json.JSONDecodeError:
            pass
    return dataset_dir.name


def resolve_session_dir(dataset_dir, row):
    session_dir = Path(row.get("session_dir", ""))
    if session_dir.is_absolute():
        return session_dir
    return (dataset_dir / session_dir).resolve()


def main():
    args = parse_args()
    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_manifest = output_dir / "trials.csv"

    allowed_collectors = normalize_filter(args.collector)
    allowed_gestures = normalize_filter(args.gesture)

    combined = []
    skipped = []
    source_dirs = [Path(item).expanduser().resolve() for item in args.datasets]

    for dataset_dir in source_dirs:
        try:
            rows = read_rows(dataset_dir)
        except FileNotFoundError as exc:
            skipped.append({"dataset": str(dataset_dir), "reason": str(exc)})
            continue
        source_name = source_dataset_name(dataset_dir)
        for row in rows:
            if allowed_collectors and row.get("collector") not in allowed_collectors:
                continue
            if allowed_gestures and row.get("gesture") not in allowed_gestures:
                continue

            session_dir = resolve_session_dir(dataset_dir, row)
            if not session_dir.exists() and not args.allow_missing_session:
                skipped.append(
                    {
                        "dataset": str(dataset_dir),
                        "session_dir": str(session_dir),
                        "reason": "missing session_dir",
                    }
                )
                continue

            merged_row = {name: row.get(name, "") for name in FIELDNAMES}
            merged_row["source_dataset"] = source_name
            merged_row["session_dir"] = str(session_dir)
            combined.append(merged_row)

    with open(output_manifest, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(combined)

    metadata = {
        "dataset_name": output_dir.name,
        "combined_from": [str(path) for path in source_dirs],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "row_count": len(combined),
        "input_type": "range",
        "collector_filter": sorted(allowed_collectors) if allowed_collectors else None,
        "gesture_filter": sorted(allowed_gestures) if allowed_gestures else None,
        "skipped": skipped,
    }
    (output_dir / "dataset_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )

    print(json.dumps(metadata, indent=2, sort_keys=True))
    if not combined:
        print("No rows were combined.", file=sys.stderr)
        return 1
    print(f"Combined manifest: {output_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
