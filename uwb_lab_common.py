#!/usr/bin/env python3

import csv
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path


LAB_DIR = Path(__file__).resolve().parent
QORVO_ROOT = LAB_DIR / "uwb-qorvo-tools"
RUN_FIRA_TWR = QORVO_ROOT / "scripts" / "fira" / "run_fira_twr" / "run_fira_twr.py"
RESET_DEVICE = QORVO_ROOT / "scripts" / "device" / "reset_device" / "reset_device.py"


def timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def process_env():
    env = os.environ.copy()
    repo_paths = [
        str(QORVO_ROOT),
        str(QORVO_ROOT / "lib" / "uwb-uci"),
        str(QORVO_ROOT / "lib" / "uqt-utils"),
    ]
    old_pythonpath = env.get("PYTHONPATH")
    if old_pythonpath:
        repo_paths.append(old_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(repo_paths)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def make_session_dir(out_root, kind, group_id, session_name=None):
    root = Path(out_root).expanduser().resolve()
    name = session_name or f"{kind}_group_{group_id}_{timestamp()}"
    session_dir = root / name
    (session_dir / "controller").mkdir(parents=True, exist_ok=True)
    (session_dir / "controlee").mkdir(parents=True, exist_ok=True)
    return session_dir


def compute_ranging_span_ms(fps, ranging_span):
    if ranging_span is not None:
        return int(ranging_span)
    return max(1, int(round(1000.0 / float(fps))))


def validate_timing(slot_span, slots_per_rr, ranging_span):
    slot_ms = float(slot_span) / 1200.0
    minimum_ms = slot_ms * int(slots_per_rr)
    if ranging_span < minimum_ms:
        raise ValueError(
            f"ranging span {ranging_span} ms is shorter than "
            f"{slots_per_rr} slots * {slot_ms:.3f} ms = {minimum_ms:.3f} ms"
        )


def twr_command(
    python_exe,
    port,
    group_id,
    duration_s,
    slot_span,
    slots_per_rr,
    ranging_span,
    controlee=False,
    stats=True,
):
    cmd = [
        python_exe,
        "-u",
        str(RUN_FIRA_TWR),
        "-p",
        str(port),
        "--preamble-idx",
        str(group_id),
        "--aoa-report",
        "all-disabled",
        "--slot-span",
        str(slot_span),
        "--slots-per-rr",
        str(slots_per_rr),
        "--ranging-span",
        str(ranging_span),
        "-t",
        str(int(duration_s)),
    ]
    if controlee:
        cmd.append("--controlee")
    if stats:
        cmd.append("--stats")
    return cmd


def write_metadata(session_dir, metadata):
    path = Path(session_dir) / "session_metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def start_logged_process(cmd, cwd, log_path):
    log_file = open(log_path, "w", buffering=1)
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=process_env(),
        start_new_session=True,
    )
    return proc, log_file


def run_logged_process(cmd, cwd, log_path, on_line=None):
    if on_line is None:
        with open(log_path, "w", buffering=1) as log_file:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                env=process_env(),
                start_new_session=True,
            )
            try:
                return proc.wait()
            except KeyboardInterrupt:
                stop_process(proc)
                raise

    with open(log_path, "w", buffering=1) as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=process_env(),
            start_new_session=True,
        )
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_file.write(line)
                on_line(line)
            return proc.wait()
        except KeyboardInterrupt:
            stop_process(proc)
            raise


def stop_process(proc, log_file=None):
    try:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
    finally:
        if log_file:
            log_file.close()


def compact_command_output(text, max_chars=20000):
    if len(text) <= max_chars:
        return text
    head_chars = max_chars // 4
    tail_chars = max_chars - head_chars
    return (
        text[:head_chars]
        + "\n\n... output truncated; stale ranging notifications were omitted ...\n\n"
        + text[-tail_chars:]
    )


def run_device_command(cmd, log_path):
    completed = subprocess.run(
        cmd,
        cwd=QORVO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=process_env(),
    )
    with open(log_path, "a") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        logged_output = compact_command_output(completed.stdout)
        log.write(logged_output)
        if not logged_output.endswith("\n"):
            log.write("\n")
        log.write(f"return_code={completed.returncode}\n\n")
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.splitlines()[-20:])
        raise RuntimeError(
            "device command failed\n"
            f"command: {' '.join(cmd)}\n"
            f"return_code: {completed.returncode}\n"
            f"last output:\n{tail}"
        )
    return completed.stdout


def reset_devices(python_exe, ports, log_path):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("")
    for port in ports:
        run_device_command([python_exe, str(RESET_DEVICE), "-p", str(port)], log_path)
        time.sleep(0.75)
    time.sleep(1.0)


class RangeLogParser:
    sequence_re = re.compile(r"sequence n:\s*(\d+)")
    interval_re = re.compile(r"ranging interval:\s*([0-9.]+)\s*ms")
    status_re = re.compile(r"status:\s*([A-Za-z0-9_]+)\s*\((0x[0-9a-fA-F]+)\)")
    distance_re = re.compile(r"distance:\s*([-+]?[0-9]*\.?[0-9]+)\s*cm")

    def __init__(self):
        self.sequence = None
        self.interval_ms = None
        self.status = None
        self.status_code = None

    def feed(self, line):
        match = self.sequence_re.search(line)
        if match:
            self.sequence = int(match.group(1))
            self.status = None
            self.status_code = None
            return None

        match = self.interval_re.search(line)
        if match:
            self.interval_ms = float(match.group(1))
            return None

        match = self.status_re.search(line)
        if match:
            self.status = match.group(1)
            self.status_code = match.group(2)
            return None

        match = self.distance_re.search(line)
        if match:
            sample = {
                "time_s": None,
                "sequence": self.sequence,
                "interval_ms": self.interval_ms,
                "status": self.status or "unknown",
                "status_code": self.status_code or "",
                "distance_cm": float(match.group(1)),
            }
            self.status = None
            self.status_code = None
            return sample

        return None


def parse_ranging_log(path):
    parser = RangeLogParser()
    samples = []
    path = Path(path)
    if not path.exists():
        return samples
    with open(path, errors="replace") as f:
        for line in f:
            sample = parser.feed(line)
            if sample:
                samples.append(sample)
    return samples


def write_ranging_csv(samples, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "time_s",
                "sequence",
                "interval_ms",
                "status",
                "status_code",
                "distance_cm",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(samples)


def read_ranging_csv(path):
    samples = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            samples.append(
                {
                    "time_s": float(row["time_s"]) if row.get("time_s") else None,
                    "sequence": int(row["sequence"]) if row.get("sequence") else None,
                    "interval_ms": float(row["interval_ms"]) if row.get("interval_ms") else None,
                    "status": row.get("status", ""),
                    "status_code": row.get("status_code", ""),
                    "distance_cm": float(row["distance_cm"]),
                }
            )
    return samples


def summarize_ranging(samples):
    ok = [
        s["distance_cm"]
        for s in samples
        if s.get("status") == "Ok" and 0.0 < float(s.get("distance_cm", 0.0)) < 60000.0
    ]
    if not ok:
        return {
            "total_samples": len(samples),
            "ok_samples": 0,
            "mean_cm": None,
            "median_cm": None,
            "min_cm": None,
            "max_cm": None,
        }
    ordered = sorted(ok)
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])
    return {
        "total_samples": len(samples),
        "ok_samples": len(ok),
        "mean_cm": sum(ok) / len(ok),
        "median_cm": median,
        "min_cm": min(ok),
        "max_cm": max(ok),
    }


def filter_distances_cm(values, max_distance_cm=10000.0, mad_z=4.0):
    clean = [
        float(v)
        for v in values
        if math.isfinite(float(v)) and 0.0 < float(v) < float(max_distance_cm)
    ]
    if len(clean) < 4:
        return clean

    ordered = sorted(clean)
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])
    deviations = sorted(abs(v - median) for v in clean)
    mad = deviations[n // 2] if n % 2 else 0.5 * (deviations[n // 2 - 1] + deviations[n // 2])
    if mad > 0 and mad_z is not None:
        limit = float(mad_z) * 1.4826 * mad
        return [v for v in clean if abs(v - median) <= limit]

    q1 = ordered[n // 4]
    q3 = ordered[(3 * n) // 4]
    iqr = q3 - q1
    if iqr <= 0:
        return clean
    low = q1 - 1.5 * iqr
    high = q3 + 1.5 * iqr
    return [v for v in clean if low <= v <= high]


RANGE_FEATURE_NAMES = [
    "count",
    "mean_cm",
    "std_cm",
    "min_cm",
    "max_cm",
    "median_cm",
    "range_cm",
    "q25_cm",
    "q75_cm",
    "iqr_cm",
    "first_cm",
    "last_cm",
    "delta_cm",
    "abs_delta_cm",
    "mean_abs_step_cm",
    "max_abs_step_cm",
    "slope_cm_per_sample",
]


def range_feature_names(resample_points=20):
    return RANGE_FEATURE_NAMES + [f"shape_{i:02d}" for i in range(int(resample_points))]


def extract_range_features(samples, resample_points=20):
    import numpy as np

    values = [
        float(s["distance_cm"])
        for s in samples
        if s.get("status") == "Ok" and 0.0 < float(s.get("distance_cm", 0.0)) < 60000.0
    ]
    if len(values) < 2:
        return None

    arr = np.asarray(values, dtype=float)
    diffs = np.diff(arr)
    q25, q75 = np.percentile(arr, [25, 75])
    x = np.arange(len(arr), dtype=float)
    slope = float(np.polyfit(x, arr, deg=1)[0])

    features = [
        float(len(arr)),
        float(arr.mean()),
        float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        float(arr.min()),
        float(arr.max()),
        float(np.median(arr)),
        float(arr.max() - arr.min()),
        float(q25),
        float(q75),
        float(q75 - q25),
        float(arr[0]),
        float(arr[-1]),
        float(arr[-1] - arr[0]),
        float(abs(arr[-1] - arr[0])),
        float(np.mean(np.abs(diffs))) if len(diffs) else 0.0,
        float(np.max(np.abs(diffs))) if len(diffs) else 0.0,
        slope,
    ]

    resample_points = int(resample_points)
    if resample_points > 0:
        src_x = np.arange(len(arr), dtype=float)
        dst_x = np.linspace(0.0, float(len(arr) - 1), resample_points)
        shape = np.interp(dst_x, src_x, arr)
        shape = shape - shape[0]
        features.extend(float(v) for v in shape)

    return features

# This should align with the order of implementation in extract_range_features_proposal().
# You don't have to use all of these features
# but you should keep the order consistent with your implementation.
PROPOSAL_RANGE_FEATURE_NAMES = [
    "proposal_close_fraction",
    "proposal_end_minus_start_cm",
    "proposal_linear_r_squared",
    "proposal_third3_minus_third1_cm",
    "proposal_increasing_fraction",
    "proposal_total_variation_cm",
    "proposal_velocity_sign_changes",
    "proposal_number_of_valleys",
    "proposal_mean_valley_prominence_cm",
    "proposal_valley_interval_std_s",
]


def proposal_range_feature_names(resample_points=20):
    return range_feature_names(resample_points=resample_points) + list(PROPOSAL_RANGE_FEATURE_NAMES)


def _ok_distance_series(samples):
    values = []
    times = []
    for sample in samples:
        if sample.get("status") != "Ok":
            continue
        try:
            distance = float(sample.get("distance_cm", 0.0))
        except (TypeError, ValueError):
            continue
        if not (0.0 < distance < 60000.0 and math.isfinite(distance)):
            continue
        values.append(distance)
        time_s = sample.get("time_s")
        try:
            times.append(float(time_s) if time_s not in (None, "") else None)
        except (TypeError, ValueError):
            times.append(None)
    return values, times


def extract_range_features_proposal(
    samples,
    resample_points=20,
    fs=50.0,
    close_threshold_cm=15.0,
    min_samples=20,
):
    """Baseline features plus student-designed proposal features.

    The baseline features are already implemented in extract_range_features().
    Your task is to replace the placeholder proposal features below with
    low-cost features that help distinguish two-handed activities.
    """
    import numpy as np

    baseline_features = extract_range_features(samples, resample_points=resample_points)
    if baseline_features is None:
        return None

    values, times = _ok_distance_series(samples)
    if len(values) < int(min_samples):
        return None

    distances = np.asarray(values, dtype=float)
    if len(distances) < int(min_samples):
        return None
    if np.any(~np.isfinite(distances)):
        return None

    # TODO: replace these placeholder values with your proposed features.
    #
    # Keep the feature order consistent with PROPOSAL_RANGE_FEATURE_NAMES.
    # Good features should be cheap enough for real-time evaluation. Useful
    # directions to try:
    # - fraction of samples where the hands are closer than close_threshold_cm
    # - difference between the beginning and end of the window
    # - whether the signal has a strong increasing or decreasing trend
    # - total movement amount, such as sum(abs(diff(distance)))
    # - number of direction changes in the distance signal
    # - number, spacing, or depth of valleys when the hands come together
    #
    # You may use `fs` if you need a sampling rate, but avoid expensive feature
    # extraction that would slow down eval_realtime.py.
    _ = fs, close_threshold_cm, times
    proposal_features = [0.0 for _ in PROPOSAL_RANGE_FEATURE_NAMES]

    return [float(value) for value in baseline_features + proposal_features]


def find_side_log(session_dir, side):
    session_dir = Path(session_dir)
    direct = session_dir / side / f"{side}_terminal_log.txt"
    if direct.exists():
        return direct
    matches = sorted(session_dir.rglob(f"{side}_terminal_log.txt"))
    return matches[0] if matches else None


def find_ranging_samples(session_dir, side):
    session_dir = Path(session_dir)
    csv_path = session_dir / side / "ranging_samples.csv"
    if csv_path.exists():
        return read_ranging_csv(csv_path)
    log_path = find_side_log(session_dir, side)
    if not log_path:
        return []
    return parse_ranging_log(log_path)


class LiveRangePlot:
    def __init__(self, window=500, update_interval_s=0.1):
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtWidgets

        self.pg = pg
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.window = int(window)
        self.update_interval_s = float(update_interval_s)
        self.x = deque(maxlen=self.window)
        self.y = deque(maxlen=self.window)
        self.ok_count = 0
        self.total_count = 0
        self.last_update = 0.0

        self.widget = pg.PlotWidget(title="Live UWB Ranging")
        self.widget.setLabel("bottom", "Sequence")
        self.widget.setLabel("left", "Distance", units="cm")
        self.widget.showGrid(x=True, y=True, alpha=0.25)
        self.curve = self.widget.plot([], [], pen=pg.mkPen("#00a6d6", width=2))
        self.widget.resize(900, 420)
        self.widget.show()
        self.app.processEvents()

    def add_sample(self, sample, force=False):
        self.total_count += 1
        if sample["status"] == "Ok" and sample["distance_cm"] < 60000:
            self.ok_count += 1
            self.x.append(sample["sequence"] if sample["sequence"] is not None else self.total_count)
            self.y.append(sample["distance_cm"])

        now = time.monotonic()
        if not force and now - self.last_update < self.update_interval_s:
            return
        self.last_update = now
        if self.x:
            x_values = list(self.x)
            y_values = list(self.y)
            self.curve.setData(x_values, y_values)
            x_min, x_max = min(self.x), max(self.x)
            if x_min == x_max:
                x_min -= 1
                x_max += 1
            self.widget.setXRange(x_min, x_max, padding=0.02)
            y_min, y_max = min(self.y), max(self.y)
            pad = max(5.0, 0.1 * (y_max - y_min or 1.0))
            self.widget.setYRange(y_min - pad, y_max + pad, padding=0.0)
            self.widget.setTitle(
                f"Live UWB Ranging: {self.ok_count}/{self.total_count} Ok, "
                f"latest {self.y[-1]:.1f} cm"
            )
        else:
            self.widget.setTitle(f"Live UWB Ranging: 0/{self.total_count} Ok")
        self.app.processEvents()

    def finish(self, keep_open=False):
        if self.x:
            self.add_sample(
                {
                    "status": "NoUpdate",
                    "distance_cm": 65535.0,
                    "sequence": self.x[-1],
                },
                force=True,
            )
        if keep_open:
            self.app.exec()


def print_missing_plot_dependency(exc):
    print(
        "Plotting needs numpy, matplotlib, and pyqtgraph. Install the UWB Qorvo Tools "
        "environment first, then rerun this script.",
        file=sys.stderr,
    )
    print(f"Import error: {exc}", file=sys.stderr)
