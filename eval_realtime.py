#!/usr/bin/env python3

import argparse
import csv
import json
import sys
import time
from collections import deque
from pathlib import Path

from uwb_lab_common import (
    LAB_DIR,
    RangeLogParser,
    compute_ranging_span_ms,
    extract_range_features,
    extract_range_features_proposal,
    make_session_dir,
    parse_ranging_log,
    print_missing_plot_dependency,
    reset_devices,
    run_logged_process,
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
        description="Run real-time gesture evaluation from UWB range input."
    )
    parser.add_argument("--model", required=True, help="Range model .joblib from train.py.")
    parser.add_argument("--controller-port", required=True)
    parser.add_argument("--controlee-port", required=True)
    parser.add_argument("--group-id", required=True, type=int)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--fps", type=float, help="Default: model training FPS, or 50 Hz if unavailable.")
    parser.add_argument("--ranging-span", type=int)
    parser.add_argument("--slot-span", type=int, help="Default: model training slot span, or 2400.")
    parser.add_argument("--slots-per-rr", type=int, help="Default: model training slots per round, or 6.")
    parser.add_argument("--startup-delay", type=float, default=3.0)
    parser.add_argument("--controlee-extra", type=int, default=5)
    parser.add_argument(
        "--window-seconds",
        type=float,
        help="Default: model training trial duration, or 2 seconds if unavailable.",
    )
    parser.add_argument("--step-seconds", type=float, default=0.5)
    parser.add_argument("--out-root", default=str(LAB_DIR / "sessions"))
    parser.add_argument("--session-name")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--visualize", action="store_true")
    parser.add_argument("--skip-device-reset", action="store_true")
    parser.add_argument(
        "--strict-model-config",
        action="store_true",
        help="Exit instead of warning when evaluation timing differs from the model metadata.",
    )
    return parser.parse_args()


class LiveRangePredictionPlot:
    def __init__(self, max_points=300):
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtWidgets

        self.pg = pg
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.x = deque(maxlen=max_points)
        self.y = deque(maxlen=max_points)
        self.widget = pg.PlotWidget(title="Real-Time Range Gesture Evaluation")
        self.widget.setLabel("bottom", "Sequence")
        self.widget.setLabel("left", "Distance", units="cm")
        self.widget.showGrid(x=True, y=True, alpha=0.25)
        self.curve = self.widget.plot([], [], pen=pg.mkPen("#00a6d6", width=2))
        self.widget.resize(900, 420)
        self.widget.show()
        self.app.processEvents()

    def update(self, sample, prediction=None, confidence=None):
        if sample["status"] == "Ok" and sample["distance_cm"] < 60000:
            self.x.append(sample["sequence"] if sample["sequence"] is not None else len(self.x))
            self.y.append(sample["distance_cm"])
        if self.x:
            self.curve.setData(list(self.x), list(self.y))
            x_min, x_max = min(self.x), max(self.x)
            if x_min == x_max:
                x_min -= 1
                x_max += 1
            y_min, y_max = min(self.y), max(self.y)
            pad = max(5.0, 0.1 * (y_max - y_min or 1.0))
            self.widget.setXRange(x_min, x_max, padding=0.02)
            self.widget.setYRange(y_min - pad, y_max + pad, padding=0.0)
        if prediction is not None:
            self._set_prediction_title(prediction, confidence)
        self.app.processEvents()

    def _set_prediction_title(self, prediction, confidence):
        title = f"Prediction: {prediction}"
        if confidence is not None:
            title += f" ({confidence:.2f})"
        self.widget.setTitle(title)


def estimator_classes(model):
    classes = getattr(model, "classes_", None)
    if classes is not None:
        return list(classes)
    if hasattr(model, "steps") and model.steps:
        final_estimator = model.steps[-1][1]
        classes = getattr(final_estimator, "classes_", None)
        if classes is not None:
            return list(classes)
    return None


def prediction_confidence(model, features, prediction):
    if not hasattr(model, "predict_proba"):
        return None
    probabilities = model.predict_proba([features])[0]
    classes = estimator_classes(model)
    if classes is None:
        return float(max(probabilities))
    if prediction not in classes:
        return None
    return float(probabilities[classes.index(prediction)])


def extract_features_for_payload(samples, feature_set, feature_params, resample_points):
    if feature_set == "baseline":
        return extract_range_features(samples, resample_points=resample_points)
    if feature_set == "proposal":
        return extract_range_features_proposal(
            samples,
            resample_points=resample_points,
            fs=float(feature_params.get("feature_fs", 50.0)),
            close_threshold_cm=float(feature_params.get("close_threshold_cm", 25.0)),
        )
    raise SystemExit(f"Unsupported model feature_set: {feature_set}")


def build_commands(args, ranging_span):
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
    return controller_cmd, controlee_cmd, controlee_duration


def single_training_value(training_config, field, default=None, cast=float):
    summary = training_config.get(field) or {}
    values = summary.get("values") or []
    if len(values) == 1:
        try:
            return cast(values[0])
        except (TypeError, ValueError):
            return default
    return default


def apply_model_defaults(args, training_config):
    args.fps = args.fps if args.fps is not None else single_training_value(
        training_config, "fps", default=50.0, cast=float
    )
    args.slot_span = args.slot_span if args.slot_span is not None else single_training_value(
        training_config, "slot_span", default=2400, cast=int
    )
    args.slots_per_rr = (
        args.slots_per_rr
        if args.slots_per_rr is not None
        else single_training_value(training_config, "slots_per_rr", default=6, cast=int)
    )
    args.window_seconds = (
        args.window_seconds
        if args.window_seconds is not None
        else single_training_value(training_config, "trial_duration_s", default=2.0, cast=float)
    )


def value_mismatch(training_config, field, eval_value, label, tolerance=1e-6):
    summary = training_config.get(field) or {}
    values = summary.get("values") or []
    if not values:
        return None
    if not summary.get("consistent"):
        return (
            f"training used multiple {label} values {values}; "
            f"evaluation is using {eval_value}"
        )
    trained_value = float(values[0])
    if abs(float(eval_value) - trained_value) > tolerance:
        return (
            f"training {label} was {trained_value:g}; "
            f"evaluation is using {float(eval_value):g}"
        )
    return None


def model_config_warnings(training_config, args, ranging_span):
    checks = [
        ("fps", args.fps, "FPS", 1e-3),
        ("ranging_span_ms", ranging_span, "ranging span (ms)", 1e-3),
        ("slot_span", args.slot_span, "slot span", 1e-6),
        ("slots_per_rr", args.slots_per_rr, "slots per ranging round", 1e-6),
        ("trial_duration_s", args.window_seconds, "window/trial duration (s)", 1e-3),
    ]
    warnings = []
    for field, value, label, tolerance in checks:
        warning = value_mismatch(training_config, field, value, label, tolerance=tolerance)
        if warning:
            warnings.append(warning)
    return warnings


def main():
    args = parse_args()
    try:
        import joblib
    except ImportError as exc:
        raise SystemExit(f"Missing joblib/sklearn environment: {exc}")

    payload = joblib.load(args.model)
    input_type = payload.get("input_type", "range")
    if input_type != "range":
        raise SystemExit(f"This range-only evaluator cannot run model input_type: {input_type}")

    model = payload["model"]
    classifier_name = payload.get("classifier", "unknown")
    classifier_label = payload.get("classifier_label", classifier_name)
    feature_set = payload.get("feature_set", "baseline")
    feature_params = payload.get("feature_params") or {}
    resample_points = int(payload.get("resample_points", 20))
    feature_names = payload.get("feature_names") or []
    training_config = payload.get("training_config") or {}

    fps_overridden = args.fps is not None
    apply_model_defaults(args, training_config)

    if args.ranging_span is None:
        model_ranging_span = single_training_value(
            training_config, "ranging_span_ms", default=None, cast=int
        )
        ranging_span = (
            compute_ranging_span_ms(args.fps, args.ranging_span)
            if fps_overridden or model_ranging_span is None
            else model_ranging_span
        )
    else:
        ranging_span = compute_ranging_span_ms(args.fps, args.ranging_span)
    validate_timing(args.slot_span, args.slots_per_rr, ranging_span)
    range_window_samples = max(2, int(round(args.window_seconds * 1000.0 / ranging_span)))

    warnings = model_config_warnings(training_config, args, ranging_span)
    if warnings:
        print("Model/evaluation timing warning:")
        for warning in warnings:
            print(f"  - {warning}")
        print("For best results, evaluate with the same timing used for data collection.")
        if args.strict_model_config:
            print("Stopping because --strict-model-config was set.")
            return 2

    session_dir = make_session_dir(args.out_root, "eval", args.group_id, args.session_name)
    controller_dir = session_dir / "controller"
    controlee_dir = session_dir / "controlee"
    controller_log = controller_dir / "controller_terminal_log.txt"
    controlee_log = controlee_dir / "controlee_terminal_log.txt"
    reset_log = session_dir / "device_reset_log.txt"
    predictions_path = session_dir / "realtime_predictions.csv"

    if not args.skip_device_reset:
        print("Resetting devices...")
        try:
            reset_devices(args.python, [args.controller_port, args.controlee_port], reset_log)
        except RuntimeError as exc:
            print("Device reset failed. Check that no other terminal is using the boards.")
            print(f"Reset log: {reset_log}")
            print(exc)
            return 2

    controller_cmd, controlee_cmd, controlee_duration = build_commands(args, ranging_span)
    write_metadata(
        session_dir,
        {
            "kind": "eval_realtime",
            "input_type": "range",
            "model": str(Path(args.model).expanduser().resolve()),
            "classifier": classifier_name,
            "classifier_label": classifier_label,
            "feature_set": feature_set,
            "feature_params": feature_params,
            "group_id": args.group_id,
            "duration_s": args.duration,
            "window_seconds": args.window_seconds,
            "step_seconds": args.step_seconds,
            "ranging_span_ms": ranging_span,
            "fps": args.fps,
            "slot_span": args.slot_span,
            "slots_per_rr": args.slots_per_rr,
            "range_window_samples": range_window_samples,
            "model_training_config": training_config,
            "model_config_warnings": warnings,
            "controller_command": controller_cmd,
            "controlee_command": controlee_cmd,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    print(f"Session folder: {session_dir}")
    print(f"Loaded classifier: {classifier_label}")
    print(f"Loaded feature set: {feature_set}")
    print(
        f"Range window: {range_window_samples} samples "
        f"({args.window_seconds:.2f} s, {ranging_span} ms interval)"
    )
    print("Starting controlee...")
    controlee_proc, controlee_file = start_logged_process(controlee_cmd, controlee_dir, controlee_log)

    range_parser = RangeLogParser()
    range_window = deque(maxlen=range_window_samples)
    last_prediction_time = 0.0
    plot = None
    if args.visualize:
        try:
            plot = LiveRangePredictionPlot(max_points=max(300, range_window_samples * 3))
        except ImportError as exc:
            print_missing_plot_dependency(exc)
    interrupted = False
    controller_rc = None
    controlee_rc = None

    with open(predictions_path, "w", newline="") as prediction_file:
        writer = csv.DictWriter(
            prediction_file,
            fieldnames=[
                "time_s",
                "sequence",
                "input_type",
                "prediction",
                "confidence",
                "range_window_ok_samples",
            ],
        )
        writer.writeheader()
        start_time = time.monotonic()

        def write_prediction(now, sequence, prediction, confidence, ok_count):
            writer.writerow(
                {
                    "time_s": f"{now - start_time:.3f}",
                    "sequence": sequence,
                    "input_type": "range",
                    "prediction": prediction,
                    "confidence": "" if confidence is None else f"{confidence:.4f}",
                    "range_window_ok_samples": ok_count,
                }
            )
            prediction_file.flush()
            conf_text = "" if confidence is None else f" ({confidence:.2f})"
            print(f"prediction: {prediction}{conf_text}", flush=True)

        def predict_from_features(features):
            if feature_names and len(features) != len(feature_names):
                return None, None
            prediction = model.predict([features])[0]
            confidence = prediction_confidence(model, features, prediction)
            return prediction, confidence

        def on_controller_line(line):
            nonlocal last_prediction_time
            now = time.monotonic()
            sample = range_parser.feed(line)
            if not sample:
                return

            range_window.append(sample)
            prediction = None
            confidence = None
            ok_count = sum(
                1
                for item in range_window
                if item["status"] == "Ok" and item["distance_cm"] < 60000
            )

            if now - last_prediction_time >= args.step_seconds:
                features = extract_features_for_payload(
                    list(range_window),
                    feature_set=feature_set,
                    feature_params=feature_params,
                    resample_points=resample_points,
                )
                if features is not None:
                    prediction, confidence = predict_from_features(features)
                if prediction is not None:
                    last_prediction_time = now
                    write_prediction(
                        now,
                        sample.get("sequence"),
                        prediction,
                        confidence,
                        ok_count,
                    )

            if plot:
                plot.update(sample, prediction=prediction, confidence=confidence)

        try:
            time.sleep(args.startup_delay)
            print("Starting controller...")
            controller_rc = run_logged_process(
                controller_cmd,
                controller_dir,
                controller_log,
                on_line=on_controller_line,
            )
            print("Waiting for controlee...")
            controlee_rc = controlee_proc.wait(timeout=controlee_duration + 10)
        except KeyboardInterrupt:
            interrupted = True
            print("Interrupted. Stopping running processes...")
        finally:
            stop_process(controlee_proc, controlee_file)

    if interrupted and not args.skip_device_reset:
        interrupt_reset_log = session_dir / "interrupt_reset_log.txt"
        print("Resetting devices after interrupt...")
        try:
            reset_devices(args.python, [args.controller_port, args.controlee_port], interrupt_reset_log)
        except RuntimeError as exc:
            print("Post-interrupt reset failed. Unplug/replug both boards before the next run.")
            print(f"Reset log: {interrupt_reset_log}")
            print(exc)

    controller_samples = parse_ranging_log(controller_log)
    controlee_samples = parse_ranging_log(controlee_log)
    write_ranging_csv(controller_samples, controller_dir / "ranging_samples.csv")
    write_ranging_csv(controlee_samples, controlee_dir / "ranging_samples.csv")

    summary = {
        "input_type": "range",
        "controller": summarize_ranging(controller_samples),
        "controlee": summarize_ranging(controlee_samples),
        "controller_return_code": controller_rc,
        "controlee_return_code": controlee_rc,
        "predictions_csv": str(predictions_path),
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (session_dir / "eval_realtime_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(f"Done. Predictions saved to: {predictions_path}")
    return 0 if controller_rc in (0, None) else int(controller_rc)


if __name__ == "__main__":
    raise SystemExit(main())
