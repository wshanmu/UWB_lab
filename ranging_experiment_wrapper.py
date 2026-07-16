#!/usr/bin/env python3

import argparse
import json
import sys
import time
from pathlib import Path

from uwb_lab_common import (
    LAB_DIR,
    RangeLogParser,
    LiveRangePlot,
    compute_ranging_span_ms,
    make_session_dir,
    parse_ranging_log,
    print_missing_plot_dependency,
    run_logged_process,
    reset_devices,
    start_logged_process,
    stop_process,
    summarize_ranging,
    twr_command,
    validate_timing,
    write_metadata,
    write_ranging_csv,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run one DWM3001CDK FiRa TWR ranging session and save logs."
    )
    parser.add_argument("--controller-port", required=True, help="Controller serial port.")
    parser.add_argument("--controlee-port", required=True, help="Controlee serial port.")
    parser.add_argument("--group-id", required=True, type=int, help="Group ID / preamble index.")
    parser.add_argument("--duration", type=int, default=20, help="Controller run time in seconds.")
    parser.add_argument("--fps", type=float, default=50.0, help="Target update rate.")
    parser.add_argument("--ranging-span", type=int, help="Override ranging interval in ms.")
    parser.add_argument("--slot-span", type=int, default=2400, help="FiRa slot duration value.")
    parser.add_argument("--slots-per-rr", type=int, default=6, help="Slots per ranging round.")
    parser.add_argument(
        "--controlee-extra",
        type=int,
        default=15,
        help="Extra seconds to keep the controlee running.",
    )
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=5.0,
        help="Seconds to wait after starting the controlee.",
    )
    parser.add_argument(
        "--out-root",
        default=str(LAB_DIR / "sessions"),
        help="Folder where session folders are created.",
    )
    parser.add_argument("--session-name", help="Optional session folder name.")
    parser.add_argument("--python", default=sys.executable, help="Python executable to use.")
    parser.add_argument(
        "--visualize",
        action="store_true",
        help="Show an efficient live plot of controller ranging results.",
    )
    parser.add_argument(
        "--plot-window",
        type=int,
        default=100,
        help="Number of recent samples shown in the live plot.",
    )
    parser.add_argument(
        "--plot-update-interval",
        type=float,
        default=0.1,
        help="Minimum live plot refresh interval in seconds.",
    )
    parser.add_argument(
        "--keep-plot-open",
        action="store_true",
        help="Keep the live plot open after the run finishes.",
    )
    parser.add_argument(
        "--skip-device-reset",
        action="store_true",
        help="Do not reset boards before starting the experiment.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    ranging_span = compute_ranging_span_ms(args.fps, args.ranging_span)
    validate_timing(args.slot_span, args.slots_per_rr, ranging_span)

    session_dir = make_session_dir(
        args.out_root, "ranging", args.group_id, args.session_name
    )
    controller_dir = session_dir / "controller"
    controlee_dir = session_dir / "controlee"
    controller_log = controller_dir / "controller_terminal_log.txt"
    controlee_log = controlee_dir / "controlee_terminal_log.txt"
    reset_log = session_dir / "device_reset_log.txt"

    if not args.skip_device_reset:
        print("Resetting devices...")
        try:
            reset_devices(
                args.python,
                [args.controller_port, args.controlee_port],
                reset_log,
            )
        except RuntimeError as exc:
            print("Device reset failed. Check that no other terminal is using the boards.")
            print(f"Reset log: {reset_log}")
            print(exc)
            return 2

    controlee_duration = args.duration + args.controlee_extra + int(args.startup_delay)
    controller_cmd = twr_command(
        args.python,
        args.controller_port,
        args.group_id,
        args.duration,
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

    metadata = {
        "kind": "ranging",
        "session_dir": str(session_dir),
        "group_id": args.group_id,
        "controller_port": args.controller_port,
        "controlee_port": args.controlee_port,
        "duration_s": args.duration,
        "controlee_duration_s": controlee_duration,
        "fps_target": args.fps,
        "ranging_span_ms": ranging_span,
        "slot_span": args.slot_span,
        "slots_per_rr": args.slots_per_rr,
        "device_reset": not args.skip_device_reset,
        "controller_command": controller_cmd,
        "controlee_command": controlee_cmd,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_metadata(session_dir, metadata)

    print(f"Session folder: {session_dir}")
    print(f"Target FPS: {1000.0 / ranging_span:.2f} ({ranging_span} ms interval)")
    print("Starting controlee...")
    controlee_proc, controlee_file = start_logged_process(
        controlee_cmd, controlee_dir, controlee_log
    )

    controller_rc = None
    controlee_rc = None
    live_plot = None
    interrupted = False
    parser = RangeLogParser()

    try:
        time.sleep(args.startup_delay)
        print("Starting controller...")

        if args.visualize:
            try:
                live_plot = LiveRangePlot(
                    window=args.plot_window,
                    update_interval_s=args.plot_update_interval,
                )
            except ImportError as exc:
                print_missing_plot_dependency(exc)
                live_plot = None

        def on_controller_line(line):
            sample = parser.feed(line)
            if sample and live_plot:
                live_plot.add_sample(sample)

        controller_rc = run_logged_process(
            controller_cmd,
            controller_dir,
            controller_log,
            on_line=on_controller_line if args.visualize else None,
        )

        print("Waiting for controlee...")
        controlee_rc = controlee_proc.wait(timeout=controlee_duration + 10)
    except KeyboardInterrupt:
        interrupted = True
        print("Interrupted. Stopping running processes...")
    finally:
        stop_process(controlee_proc, controlee_file)
        if live_plot:
            live_plot.finish(keep_open=args.keep_plot_open)

    if interrupted and not args.skip_device_reset:
        interrupt_reset_log = session_dir / "interrupt_reset_log.txt"
        print("Resetting devices after interrupt...")
        try:
            reset_devices(
                args.python,
                [args.controller_port, args.controlee_port],
                interrupt_reset_log,
            )
        except RuntimeError as exc:
            print("Post-interrupt reset failed. Unplug/replug both boards before the next run.")
            print(f"Reset log: {interrupt_reset_log}")
            print(exc)

    controller_samples = parse_ranging_log(controller_log)
    controlee_samples = parse_ranging_log(controlee_log)
    write_ranging_csv(controller_samples, controller_dir / "ranging_samples.csv")
    write_ranging_csv(controlee_samples, controlee_dir / "ranging_samples.csv")

    summary = {
        "controller": summarize_ranging(controller_samples),
        "controlee": summarize_ranging(controlee_samples),
        "controller_return_code": controller_rc,
        "controlee_return_code": controlee_rc,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (session_dir / "ranging_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )

    metadata.update(summary)
    write_metadata(session_dir, metadata)

    print("Done.")
    print(f"Controller samples: {summary['controller']['ok_samples']} Ok / {summary['controller']['total_samples']} total")
    print(f"Controlee samples:  {summary['controlee']['ok_samples']} Ok / {summary['controlee']['total_samples']} total")
    print(f"Logs and CSV files are in: {session_dir}")
    return 0 if controller_rc == 0 else int(controller_rc or 1)


if __name__ == "__main__":
    raise SystemExit(main())
