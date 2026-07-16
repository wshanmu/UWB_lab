#!/usr/bin/env python3

import argparse
import csv
import json
import math
from pathlib import Path

from uwb_lab_common import (
    extract_range_features,
    extract_range_features_proposal,
    proposal_range_feature_names,
    find_ranging_samples,
    range_feature_names,
    timestamp,
)


LAB_MODELS_DIR = Path(__file__).resolve().parent / "models"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train a gesture classifier from collected UWB range trials."
    )
    parser.add_argument(
        "datasets",
        nargs="+",
        help="One or more dataset folders created by collect_dataset.py or combine_datasets.py.",
    )
    parser.add_argument("--side", choices=["controller", "controlee"], default="controller")
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--resample-points", type=int, default=20)
    parser.add_argument(
        "--feature-set",
        choices=["baseline", "proposal"],
        default="baseline",
        help="Range feature extractor to use.",
    )
    parser.add_argument(
        "--feature-fs",
        type=float,
        default=50.0,
        help="Sampling rate used by the proposal feature extractor.",
    )
    parser.add_argument(
        "--close-threshold-cm",
        type=float,
        default=25.0,
        help="Close-distance threshold used by the proposal feature extractor.",
    )
    parser.add_argument(
        "--classifier",
        choices=["random_forest", "svm_rbf", "svm_poly", "decision_tree", "knn"],
        default="random_forest",
        help="Classifier family to train.",
    )
    parser.add_argument("--n-estimators", type=int, default=300, help="Random Forest trees.")
    parser.add_argument("--svm-c", type=float, default=1.0, help="SVM C regularization.")
    parser.add_argument(
        "--svm-gamma",
        default="scale",
        help="SVM gamma: scale, auto, or a positive float.",
    )
    parser.add_argument("--svm-degree", type=int, default=3, help="Polynomial SVM degree.")
    parser.add_argument(
        "--decision-tree-max-depth",
        type=int,
        help="Decision tree maximum depth. Default: no limit.",
    )
    parser.add_argument("--knn-neighbors", type=int, default=5, help="KNN neighbor count.")
    parser.add_argument(
        "--knn-weights",
        choices=["uniform", "distance"],
        default="distance",
        help="KNN voting weight.",
    )
    parser.add_argument(
        "--test-collector",
        action="append",
        help=(
            "Use all trials from this collector as the test set and all other "
            "collectors as training data. Can be repeated or comma separated."
        ),
    )
    parser.add_argument(
        "--model-out",
        help=(
            "Output .joblib path. Default: <dataset>/models for one input, "
            "or UWB_lab/models for multiple inputs."
        ),
    )
    parser.add_argument(
        "--confusion-out",
        help="Output confusion matrix PNG. Default is next to the model.",
    )
    return parser.parse_args()


def parse_svm_gamma(value):
    if value in ("scale", "auto"):
        return value
    try:
        gamma = float(value)
    except ValueError as exc:
        raise SystemExit("--svm-gamma must be scale, auto, or a positive float.") from exc
    if gamma <= 0:
        raise SystemExit("--svm-gamma must be positive.")
    return gamma


def classifier_label(classifier):
    return {
        "random_forest": "Random Forest",
        "svm_rbf": "RBF SVM",
        "svm_poly": "Polynomial SVM",
        "decision_tree": "Decision Tree",
        "knn": "KNN",
    }[classifier]


def build_classifier(args, train_count):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import SVC
    from sklearn.tree import DecisionTreeClassifier

    if args.classifier == "random_forest":
        params = {
            "n_estimators": args.n_estimators,
            "random_state": args.random_state,
            "class_weight": "balanced",
        }
        return RandomForestClassifier(**params), params

    if args.classifier == "svm_rbf":
        params = {
            "kernel": "rbf",
            "C": args.svm_c,
            "gamma": parse_svm_gamma(args.svm_gamma),
            "class_weight": "balanced",
            "probability": True,
            "random_state": args.random_state,
        }
        return make_pipeline(StandardScaler(), SVC(**params)), params

    if args.classifier == "svm_poly":
        params = {
            "kernel": "poly",
            "degree": args.svm_degree,
            "C": args.svm_c,
            "gamma": parse_svm_gamma(args.svm_gamma),
            "class_weight": "balanced",
            "probability": True,
            "random_state": args.random_state,
        }
        return make_pipeline(StandardScaler(), SVC(**params)), params

    if args.classifier == "decision_tree":
        params = {
            "max_depth": args.decision_tree_max_depth,
            "random_state": args.random_state,
            "class_weight": "balanced",
        }
        return DecisionTreeClassifier(**params), params

    if args.classifier == "knn":
        requested_neighbors = max(1, int(args.knn_neighbors))
        actual_neighbors = min(requested_neighbors, int(train_count))
        if actual_neighbors < requested_neighbors:
            print(
                f"Reducing KNN neighbors from {requested_neighbors} to {actual_neighbors} "
                f"because the training split has {train_count} examples."
            )
        params = {
            "n_neighbors": actual_neighbors,
            "weights": args.knn_weights,
        }
        return make_pipeline(StandardScaler(), KNeighborsClassifier(**params)), params

    raise SystemExit(f"Unsupported classifier: {args.classifier}")


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


def read_manifest(dataset_dir):
    manifest = dataset_dir / "trials.csv"
    rows = []
    if manifest.exists():
        with open(manifest, newline="") as f:
            rows.extend(csv.DictReader(f))
        return rows

    for metadata_path in sorted(dataset_dir.rglob("trial_metadata.json")):
        try:
            rows.append(json.loads(metadata_path.read_text()))
        except json.JSONDecodeError:
            continue
    return rows


def source_dataset_name(dataset_dir):
    metadata = dataset_dir / "dataset_metadata.json"
    if metadata.exists():
        try:
            return json.loads(metadata.read_text()).get("dataset_name") or dataset_dir.name
        except json.JSONDecodeError:
            pass
    return dataset_dir.name


def read_manifests(dataset_dirs):
    rows = []
    missing = []
    for dataset_dir in dataset_dirs:
        dataset_rows = read_manifest(dataset_dir)
        if not dataset_rows:
            missing.append(str(dataset_dir))
            continue
        source_name = source_dataset_name(dataset_dir)
        for row in dataset_rows:
            item = dict(row)
            item["_dataset_dir"] = str(dataset_dir)
            item["_source_dataset"] = source_name
            rows.append(item)
    return rows, missing


def resolve_session_dir(row):
    session_dir = Path(row.get("session_dir", ""))
    if session_dir.is_absolute():
        return session_dir
    dataset_dir = Path(row.get("_dataset_dir", "."))
    return (dataset_dir / session_dir).resolve()


def numeric_summary(rows, field):
    values = []
    for row in rows:
        value = row.get(field)
        if value in (None, ""):
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return {
            "field": field,
            "count": 0,
            "values": [],
            "consistent": False,
            "min": None,
            "max": None,
            "mean": None,
        }
    unique = sorted(set(values))
    return {
        "field": field,
        "count": len(values),
        "values": unique,
        "consistent": len(unique) == 1,
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def training_config_summary(rows, side, resample_points):
    return {
        "side": side,
        "resample_points": int(resample_points),
        "trial_duration_s": numeric_summary(rows, "trial_duration_s"),
        "fps": numeric_summary(rows, "fps"),
        "ranging_span_ms": numeric_summary(rows, "ranging_span_ms"),
        "slot_span": numeric_summary(rows, "slot_span"),
        "slots_per_rr": numeric_summary(rows, "slots_per_rr"),
    }


def feature_names_for_args(args):
    if args.feature_set == "baseline":
        return range_feature_names(resample_points=args.resample_points)
    if args.feature_set == "proposal":
        return proposal_range_feature_names(resample_points=args.resample_points)
    raise SystemExit(f"Unsupported feature set: {args.feature_set}")


def extract_features_for_args(samples, args):
    if args.feature_set == "baseline":
        return extract_range_features(samples, resample_points=args.resample_points)
    if args.feature_set == "proposal":
        return extract_range_features_proposal(
            samples,
            resample_points=args.resample_points,
            fs=args.feature_fs,
            close_threshold_cm=args.close_threshold_cm,
        )
    raise SystemExit(f"Unsupported feature set: {args.feature_set}")


def feature_params_for_args(args):
    params = {
        "feature_set": args.feature_set,
    }
    if args.feature_set == "baseline":
        params["resample_points"] = int(args.resample_points)
    elif args.feature_set == "proposal":
        params["resample_points"] = int(args.resample_points)
        params["feature_fs"] = float(args.feature_fs)
        params["close_threshold_cm"] = float(args.close_threshold_cm)
    return params


def build_examples(rows, args):
    examples = []
    labels = []
    collectors = []
    sources = []
    session_dirs = []
    feature_names = feature_names_for_args(args)
    skipped = []
    used_rows = []

    for row in rows:
        if row.get("input_type") and row.get("input_type") != "range":
            skipped.append(
                {
                    "session_dir": row.get("session_dir", ""),
                    "collector": row.get("collector", ""),
                    "gesture": row.get("gesture", ""),
                    "reason": f"unsupported input_type {row.get('input_type')}",
                }
            )
            continue

        gesture = row.get("gesture")
        session_dir = resolve_session_dir(row)
        if not session_dir.exists():
            skipped.append(
                {
                    "session_dir": str(session_dir),
                    "collector": row.get("collector", ""),
                    "gesture": gesture,
                    "reason": "missing session",
                }
            )
            continue

        samples = find_ranging_samples(session_dir, args.side)
        features = extract_features_for_args(samples, args)
        if features is None:
            skipped.append(
                {
                    "session_dir": str(session_dir),
                    "collector": row.get("collector", ""),
                    "gesture": gesture,
                    "reason": "not enough range samples",
                }
            )
            continue
        if len(features) != len(feature_names):
            skipped.append(
                {
                    "session_dir": str(session_dir),
                    "collector": row.get("collector", ""),
                    "gesture": gesture,
                    "reason": "feature length mismatch",
                }
            )
            continue

        examples.append(features)
        labels.append(gesture)
        collectors.append(row.get("collector", ""))
        sources.append(row.get("_source_dataset", ""))
        session_dirs.append(str(session_dir))
        used_rows.append(row)

    return examples, labels, collectors, sources, session_dirs, feature_names, skipped, used_rows


def default_model_path(dataset_dirs, classifier):
    if len(dataset_dirs) == 1:
        return dataset_dirs[0] / "models" / f"{classifier}_range_{timestamp()}.joblib"
    return LAB_MODELS_DIR / f"{classifier}_range_{timestamp()}.joblib"


def main():
    args = parse_args()
    dataset_dirs = [Path(item).expanduser().resolve() for item in args.datasets]
    rows, missing_datasets = read_manifests(dataset_dirs)
    if not rows:
        raise SystemExit("No trials found. Expected trials.csv or trial_metadata.json files.")

    (
        examples,
        labels,
        collectors,
        sources,
        session_dirs,
        feature_names,
        skipped,
        used_rows,
    ) = build_examples(
        rows,
        args=args,
    )
    if len(set(labels)) < 2:
        raise SystemExit("Need at least two gesture classes to train.")
    if len(examples) < 4:
        raise SystemExit("Need at least four usable trials to train.")

    try:
        import joblib
        import numpy as np
        import matplotlib.pyplot as plt
        from sklearn.metrics import (
            ConfusionMatrixDisplay,
            accuracy_score,
            classification_report,
            confusion_matrix,
        )
        from sklearn.model_selection import train_test_split
    except ImportError as exc:
        raise SystemExit(f"Training dependencies missing: {exc}")

    X = np.asarray(examples, dtype=float)
    y = np.asarray(labels)
    collectors_array = np.asarray(collectors)
    class_counts = {label: int((y == label).sum()) for label in sorted(set(labels))}
    test_collectors = normalize_filter(args.test_collector)
    split_method = "random"

    if test_collectors:
        test_mask = np.asarray([collector in test_collectors for collector in collectors], dtype=bool)
        train_mask = ~test_mask
        if not test_mask.any():
            raise SystemExit(
                "No usable examples matched --test-collector. Available collectors: "
                + ", ".join(sorted(set(collectors)))
            )
        if not train_mask.any():
            raise SystemExit("No training examples remain after applying --test-collector.")
        X_train, X_test = X[train_mask], X[test_mask]
        y_train, y_test = y[train_mask], y[test_mask]
        train_collectors = sorted(set(collectors_array[train_mask]))
        test_collectors_used = sorted(set(collectors_array[test_mask]))
        split_method = "collector_holdout"
    else:
        stratify = y if min(class_counts.values()) >= 2 else None
        split_test_size = args.test_size
        if stratify is not None:
            n_classes = len(class_counts)
            n_samples = len(y)
            if args.test_size < 1:
                requested_test_count = int(math.ceil(args.test_size * n_samples))
            else:
                requested_test_count = int(args.test_size)
            split_test_size = max(n_classes, requested_test_count)
            split_test_size = min(split_test_size, n_samples - n_classes)

        indices = np.arange(len(y))
        train_idx, test_idx = train_test_split(
            indices,
            test_size=split_test_size,
            random_state=args.random_state,
            stratify=stratify,
        )
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        train_collectors = sorted(set(collectors_array[train_idx]))
        test_collectors_used = sorted(set(collectors_array[test_idx]))

    train_labels = set(y_train)
    test_only_labels = sorted(set(y_test) - train_labels)
    if test_only_labels:
        raise SystemExit(
            "The test set contains gesture labels not present in training: "
            + ", ".join(test_only_labels)
        )
    if len(set(y_train)) < 2:
        raise SystemExit("Training split needs at least two gesture classes.")

    model, classifier_params = build_classifier(args, train_count=len(X_train))
    model.fit(X_train, y_train)
    predictions = model.predict(X_test)
    accuracy = float(accuracy_score(y_test, predictions))
    labels_order = sorted(set(y))
    matrix = confusion_matrix(y_test, predictions, labels=labels_order)

    model_out = (
        Path(args.model_out)
        if args.model_out
        else default_model_path(dataset_dirs, args.classifier)
    )
    model_out.parent.mkdir(parents=True, exist_ok=True)
    training_config = training_config_summary(
        used_rows,
        side=args.side,
        resample_points=args.resample_points,
    )
    feature_params = feature_params_for_args(args)
    payload = {
        "model": model,
        "feature_names": feature_names,
        "feature_set": args.feature_set,
        "feature_params": feature_params,
        "input_type": "range",
        "classifier": args.classifier,
        "classifier_label": classifier_label(args.classifier),
        "classifier_params": classifier_params,
        "side": args.side,
        "resample_points": args.resample_points,
        "training_config": training_config,
        "labels": labels_order,
        "training_datasets": [str(path) for path in dataset_dirs],
        "split_method": split_method,
        "test_collectors": test_collectors_used,
    }
    joblib.dump(payload, model_out)

    confusion_out = (
        Path(args.confusion_out)
        if args.confusion_out
        else model_out.with_name(model_out.stem + "_confusion_matrix.png")
    )
    display = ConfusionMatrixDisplay(confusion_matrix=matrix, display_labels=labels_order)
    fig, ax = plt.subplots(figsize=(6, 5))
    display.plot(ax=ax, cmap="Blues", colorbar=False)
    ax.set_title(f"{classifier_label(args.classifier)} Gesture Classifier\nAccuracy: {accuracy:.3f}")
    fig.tight_layout()
    fig.savefig(confusion_out, dpi=180)
    plt.close(fig)

    summary = {
        "datasets": [str(path) for path in dataset_dirs],
        "input_type": "range",
        "classifier": args.classifier,
        "classifier_label": classifier_label(args.classifier),
        "classifier_params": classifier_params,
        "feature_set": args.feature_set,
        "feature_params": feature_params,
        "side": args.side,
        "model": str(model_out),
        "confusion_matrix": str(confusion_out),
        "accuracy": accuracy,
        "class_counts": class_counts,
        "n_examples": int(len(examples)),
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "split_method": split_method,
        "training_config": training_config,
        "train_collectors": train_collectors,
        "test_collectors": test_collectors_used,
        "source_datasets": sorted(set(sources)),
        "session_dirs": session_dirs,
        "missing_datasets": missing_datasets,
        "skipped": skipped,
        "classification_report": classification_report(
            y_test,
            predictions,
            labels=labels_order,
            zero_division=0,
            output_dict=True,
        ),
    }
    summary_path = model_out.with_name(model_out.stem + "_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
