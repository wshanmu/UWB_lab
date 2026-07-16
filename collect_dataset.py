#!/usr/bin/env python3

import argparse
import csv
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from uwb_lab_common import (
    LAB_DIR,
    RangeLogParser,
    compute_ranging_span_ms,
    process_env,
    reset_devices,
    start_logged_process,
    stop_process,
    summarize_ranging,
    timestamp,
    twr_command,
    validate_timing,
    write_metadata,
    write_ranging_csv,
)


FIELDNAMES = [
    "dataset_name",
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
        description="Collect labeled UWB range gesture trials from one continuous TWR session."
    )
    parser.add_argument("--controller-port", required=True, help="Controller serial port.")
    parser.add_argument("--controlee-port", required=True, help="Controlee serial port.")
    parser.add_argument("--group-id", required=True, type=int, help="Group ID / preamble index.")
    parser.add_argument(
        "--gesture",
        action="append",
        required=True,
        help="Gesture label. Can be repeated or comma separated, e.g. --gesture push,pull,still.",
    )
    parser.add_argument("--collector", required=True, help="Name or ID of the person collecting.")
    parser.add_argument("--trials", "--trails", type=int, default=5, help="Accepted trials per gesture.")
    parser.add_argument(
        "--trial-duration",
        "--trail-length",
        type=float,
        default=5.0,
        help="Length of each accepted capture window in seconds.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=3.0,
        help="Countdown/settle time before each capture window starts.",
    )
    parser.add_argument("--countdown", type=float, help="Optional countdown override.")
    parser.add_argument("--fps", type=float, default=50.0, help="Target update rate.")
    parser.add_argument("--ranging-span", type=int, help="Override ranging interval in ms.")
    parser.add_argument("--slot-span", type=int, default=2400)
    parser.add_argument("--slots-per-rr", type=int, default=6)
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=3.0,
        help="Seconds to wait after starting the controlee before starting the controller.",
    )
    parser.add_argument("--controlee-extra", type=int, default=5)
    parser.add_argument(
        "--session-duration",
        type=int,
        default=3600,
        help="Maximum continuous TWR session length in seconds.",
    )
    parser.add_argument("--dataset-name", default=f"gesture_dataset_{timestamp()}")
    parser.add_argument("--out-root", default=str(LAB_DIR / "datasets"))
    parser.add_argument("--python", default=sys.executable, help="Python executable to use.")
    parser.add_argument(
        "--cue",
        choices=["both", "visual", "audio", "none"],
        default="both",
        help="Cue shown before and after each trial.",
    )
    parser.add_argument("--auto-accept", action="store_true", help="Do not ask Y/N after each trial.")
    parser.add_argument("--skip-device-reset", action="store_true")
    parser.add_argument("--skip-final-reset", action="store_true")
    parser.add_argument(
        "--stream-idle-timeout",
        type=float,
        default=2.0,
        help="Restart the TWR subprocesses if no controller samples arrive for this many seconds.",
    )
    parser.add_argument(
        "--min-ok-samples",
        type=int,
        default=1,
        help="Minimum Ok range samples required before a trial can be accepted.",
    )
    parser.add_argument(
        "--no-restart-on-empty",
        action="store_true",
        help="Do not restart the TWR subprocesses after an empty or stalled trial.",
    )
    return parser.parse_args()


def normalize_gestures(gesture_args):
    gestures = []
    for item in gesture_args:
        for part in item.split(","):
            label = part.strip()
            if label:
                gestures.append(label)
    if not gestures:
        raise SystemExit("At least one gesture label is required.")
    return gestures


def safe_label(value):
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return label.strip("_") or "unknown"


def beep(enabled=True):
    if enabled:
        print("\a", end="", flush=True)


def show_cue(message, cue, countdown=0):
    audio = cue in ("both", "audio")
    visual = cue in ("both", "visual")
    if visual:
        line = "=" * max(40, len(message) + 8)
        print(f"\n{line}\n  {message}\n{line}", flush=True)
    beep(audio)
    for remaining in range(int(round(countdown)), 0, -1):
        if visual:
            print(f"Starting in {remaining}...", flush=True)
        beep(audio)
        time.sleep(1)


def prompt_keep_trial():
    while True:
        answer = input("Keep this trial? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no", "r", "redo"):
            return False
        print("Please answer Y or N.")


def append_manifest(manifest_path, row):
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    exists = manifest_path.exists()
    with open(manifest_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not exists:
            writer.writeheader()
        writer.writerow({name: row.get(name, "") for name in FIELDNAMES})


def start_streamed_controller(cmd, cwd, log_path, on_line):
    log_file = open(log_path, "w", buffering=1)
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

    def reader():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_file.write(line)
                on_line(line)
        finally:
            log_file.close()

    thread = threading.Thread(target=reader, name="controller-log-reader", daemon=True)
    thread.start()
    return proc, thread


def start_continuous_session(args, dataset_dir, ranging_span, chunk_index):
    continuous_dir = dataset_dir / "continuous_session" / f"chunk_{chunk_index:03d}"
    controller_dir = continuous_dir / "controller"
    controlee_dir = continuous_dir / "controlee"
    controller_dir.mkdir(parents=True, exist_ok=True)
    controlee_dir.mkdir(parents=True, exist_ok=True)

    controlee_duration = args.session_duration + args.controlee_extra + int(args.startup_delay)
    controller_cmd = twr_command(
        args.python,
        args.controller_port,
        args.group_id,
        args.session_duration,
        args.slot_span,
        args.slots_per_rr,
        ranging_span,
        controlee=False,
        stats=True,
    )
    controlee_cmd = twr_command(
        args.python,
        args.controlee_port,
        args.group_id,
        controlee_duration,
        args.slot_span,
        args.slots_per_rr,
        ranging_span,
        controlee=True,
        stats=True,
    )
    return {
        "continuous_dir": continuous_dir,
        "controller_dir": controller_dir,
        "controlee_dir": controlee_dir,
        "controller_log": controller_dir / "controller_terminal_log.txt",
        "controlee_log": controlee_dir / "controlee_terminal_log.txt",
        "controller_cmd": controller_cmd,
        "controlee_cmd": controlee_cmd,
        "controlee_duration": controlee_duration,
    }


def sample_sequence_range(samples):
    sequences = [item.get("sequence") for item in samples if item.get("sequence") is not None]
    if not sequences:
        return "", ""
    return min(sequences), max(sequences)


def save_trial(dataset_dir, args, gesture, trial_index, attempt_index, capture, started_at, finished_at):
    session_name = (
        f"range_{safe_label(args.collector)}_"
        f"{safe_label(gesture)}_trial_{trial_index:03d}"
    )
    session_dir = dataset_dir / "sessions" / session_name
    controller_dir = session_dir / "controller"
    controlee_dir = session_dir / "controlee"
    controller_dir.mkdir(parents=True, exist_ok=True)
    controlee_dir.mkdir(parents=True, exist_ok=True)

    range_samples = capture["range_samples"]
    write_ranging_csv(range_samples, controller_dir / "ranging_samples.csv")

    sequence_start, sequence_end = sample_sequence_range(range_samples)
    summary = summarize_ranging(range_samples)
    trial_metadata = {
        "dataset_name": args.dataset_name,
        "collector": args.collector,
        "gesture": gesture,
        "input_type": "range",
        "trial_index": trial_index,
        "attempt_index": attempt_index,
        "trial_duration_s": args.trial_duration,
        "fps": args.fps,
        "ranging_span_ms": args.ranging_span_ms,
        "slot_span": args.slot_span,
        "slots_per_rr": args.slots_per_rr,
        "session_dir": str(session_dir),
        "return_code": 0,
        "controller_samples": len(range_samples),
        "controller_ok_samples": summary["ok_samples"],
        "sequence_start": sequence_start,
        "sequence_end": sequence_end,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    write_metadata(session_dir, trial_metadata)
    (session_dir / "trial_metadata.json").write_text(
        json.dumps(trial_metadata, indent=2, sort_keys=True) + "\n"
    )
    return trial_metadata


def print_trial_stats(capture):
    summary = summarize_ranging(capture["range_samples"])
    print(
        f"Captured {summary['ok_samples']} Ok range samples / "
        f"{summary['total_samples']} total",
        flush=True,
    )


def capture_is_usable(args, capture):
    summary = summarize_ranging(capture["range_samples"])
    if summary["ok_samples"] < args.min_ok_samples:
        return False, f"only {summary['ok_samples']} Ok range samples"
    return True, "ok"


def main():
    args = parse_args()
    gestures = normalize_gestures(args.gesture)
    ranging_span = compute_ranging_span_ms(args.fps, args.ranging_span)
    validate_timing(args.slot_span, args.slots_per_rr, ranging_span)
    args.ranging_span_ms = ranging_span

    dataset_dir = Path(args.out_root).expanduser().resolve() / args.dataset_name
    manifest_path = dataset_dir / "trials.csv"
    reset_log = dataset_dir / "device_reset_log.txt"
    final_reset_log = dataset_dir / "final_device_reset_log.txt"
    dataset_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "dataset_name": args.dataset_name,
        "collector": args.collector,
        "gestures": gestures,
        "input_type": "range",
        "collection_mode": "continuous_session_segmented_trials",
        "trials_per_gesture": args.trials,
        "trial_duration_s": args.trial_duration,
        "pause_s": args.pause,
        "fps": args.fps,
        "ranging_span_ms": ranging_span,
        "slot_span": args.slot_span,
        "slots_per_rr": args.slots_per_rr,
        "session_duration_s": args.session_duration,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (dataset_dir / "dataset_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )

    print(f"Dataset folder: {dataset_dir}")
    print(f"Manifest: {manifest_path}")
    print(f"Continuous session target: {1000.0 / ranging_span:.2f} FPS")

    if not args.skip_device_reset:
        print("Resetting devices once before the continuous session...")
        try:
            reset_devices(args.python, [args.controller_port, args.controlee_port], reset_log)
        except RuntimeError as exc:
            print("Device reset failed. Check that no other terminal is using the boards.")
            print(f"Reset log: {reset_log}")
            print(exc)
            return 2

    continuous_root = dataset_dir / "continuous_session"
    aggregate_controller_dir = continuous_root / "controller"
    aggregate_controller_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "capture": None,
        "all_range_samples": [],
        "last_sample_monotonic": None,
        "stream_restarts": 0,
    }
    lock = threading.Lock()
    range_parser = None
    session_started = time.monotonic()
    session = None
    chunks = []

    def on_controller_line(line):
        nonlocal session_started
        now = time.monotonic()
        elapsed = now - session_started
        sample = range_parser.feed(line)
        if sample:
            sample = dict(sample)
            sample["time_s"] = elapsed

        with lock:
            capture = state["capture"]
            if sample:
                state["last_sample_monotonic"] = now
                state["all_range_samples"].append(sample)
                if capture and capture["start_monotonic"] <= now <= capture["end_monotonic"]:
                    capture["range_samples"].append(sample)

    controlee_proc = None
    controlee_file = None
    controller_proc = None
    controller_thread = None
    interrupted = False
    chunk_index = 0

    def stop_current_stream():
        nonlocal controller_proc, controller_thread, controlee_proc, controlee_file
        if controller_proc is not None:
            stop_process(controller_proc)
        if controller_thread is not None:
            controller_thread.join(timeout=3)
        if controlee_proc is not None:
            stop_process(controlee_proc, controlee_file)
        controller_proc = None
        controller_thread = None
        controlee_proc = None
        controlee_file = None

    def start_new_stream(reason=None):
        nonlocal chunk_index, session, range_parser, session_started
        nonlocal controlee_proc, controlee_file, controller_proc, controller_thread

        stop_current_stream()
        if reason:
            print(f"Restarting TWR stream: {reason}", flush=True)
            state["stream_restarts"] += 1
            if not args.skip_device_reset:
                restart_log = dataset_dir / f"restart_device_reset_{state['stream_restarts']:03d}.txt"
                reset_devices(args.python, [args.controller_port, args.controlee_port], restart_log)

        chunk_index += 1
        session = start_continuous_session(args, dataset_dir, ranging_span, chunk_index)
        chunks.append(
            {
                "chunk_index": chunk_index,
                "continuous_dir": str(session["continuous_dir"]),
                "controller_log": str(session["controller_log"]),
                "controlee_log": str(session["controlee_log"]),
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason or "initial",
            }
        )
        range_parser = RangeLogParser()
        session_started = time.monotonic()
        with lock:
            state["last_sample_monotonic"] = None

        print(f"Starting continuous controlee chunk {chunk_index}...")
        controlee_proc, controlee_file = start_logged_process(
            session["controlee_cmd"], session["controlee_dir"], session["controlee_log"]
        )
        time.sleep(args.startup_delay)
        print(f"Starting continuous controller chunk {chunk_index}...")
        controller_proc, controller_thread = start_streamed_controller(
            session["controller_cmd"],
            session["controller_dir"],
            session["controller_log"],
            on_controller_line,
        )
        write_metadata(
            session["continuous_dir"],
            {
                **metadata,
                "controller_command": session["controller_cmd"],
                "controlee_command": session["controlee_cmd"],
                "chunk_index": chunk_index,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "restart_reason": reason or "initial",
            },
        )

    def maybe_restart_idle_stream():
        if args.stream_idle_timeout <= 0:
            return
        with lock:
            last_sample = state["last_sample_monotonic"]
        if last_sample is None:
            return
        idle_s = time.monotonic() - last_sample
        if idle_s > args.stream_idle_timeout:
            start_new_stream(f"no controller samples for {idle_s:.1f} seconds")

    try:
        start_new_stream()

        accepted_total = 0
        for gesture in gestures:
            trial_index = 1
            while trial_index <= args.trials:
                attempt_index = 1
                while True:
                    if controller_proc.poll() is not None:
                        raise RuntimeError(
                            f"Controller process exited early with code {controller_proc.returncode}."
                        )
                    maybe_restart_idle_stream()
                    show_cue(
                        f"Prepare: {gesture} | trial {trial_index}/{args.trials}",
                        args.cue,
                        countdown=args.pause if args.countdown is None else args.countdown,
                    )
                    maybe_restart_idle_stream()
                    show_cue(f"START {gesture}", args.cue, countdown=0)
                    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    now = time.monotonic()
                    capture = {
                        "range_samples": [],
                        "start_monotonic": now,
                        "end_monotonic": now + float(args.trial_duration),
                    }
                    with lock:
                        state["capture"] = capture
                    time.sleep(float(args.trial_duration))
                    with lock:
                        state["capture"] = None
                        frozen_capture = {
                            "range_samples": list(capture["range_samples"]),
                        }
                    finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    show_cue(f"FINISH {gesture}", args.cue, countdown=0)
                    print_trial_stats(frozen_capture)

                    usable, reason = capture_is_usable(args, frozen_capture)
                    if not usable:
                        print(f"Discarded empty/invalid trial: {reason}.", flush=True)
                        if not args.no_restart_on_empty:
                            start_new_stream(f"invalid capture: {reason}")
                        attempt_index += 1
                        continue

                    keep = args.auto_accept or prompt_keep_trial()
                    if keep:
                        row = save_trial(
                            dataset_dir,
                            args,
                            gesture,
                            trial_index,
                            attempt_index,
                            frozen_capture,
                            started_at,
                            finished_at,
                        )
                        append_manifest(manifest_path, row)
                        accepted_total += 1
                        print(
                            f"Saved trial {trial_index}/{args.trials} for {gesture}: "
                            f"{row['session_dir']}",
                            flush=True,
                        )
                        trial_index += 1
                        break

                    print("Discarded trial. Ranging is still running; redo the same trial.")
                    attempt_index += 1

        print(f"Accepted trials: {accepted_total}")
    except KeyboardInterrupt:
        interrupted = True
        print("Interrupted. Stopping continuous session...")
    except RuntimeError as exc:
        print(exc)
        interrupted = True
    finally:
        stop_current_stream()

        continuous_summary = {
            "controller": summarize_ranging(state["all_range_samples"]),
            "controller_return_code": None if controller_proc is None else controller_proc.returncode,
            "controlee_return_code": None if controlee_proc is None else controlee_proc.returncode,
            "interrupted": interrupted,
            "stream_restarts": state["stream_restarts"],
            "chunks": chunks,
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (continuous_root / "continuous_summary.json").write_text(
            json.dumps(continuous_summary, indent=2, sort_keys=True) + "\n"
        )
        write_ranging_csv(
            state["all_range_samples"],
            aggregate_controller_dir / "ranging_samples.csv",
        )

        if not args.skip_device_reset and not args.skip_final_reset:
            print("Resetting devices once after the continuous session...")
            try:
                reset_devices(args.python, [args.controller_port, args.controlee_port], final_reset_log)
            except RuntimeError as exc:
                print("Final reset failed. Unplug/replug both boards before the next run.")
                print(f"Reset log: {final_reset_log}")
                print(exc)

    print(f"Dataset complete: {dataset_dir}")
    return 1 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
