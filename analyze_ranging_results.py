#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

from uwb_lab_common import (
    filter_distances_cm,
    find_ranging_samples,
    print_missing_plot_dependency,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot filtered KDE curves for one ranging session."
    )
    parser.add_argument("session", help="Session folder created by the ranging wrapper.")
    parser.add_argument(
        "--side",
        choices=["controller", "controlee", "both"],
        default="controller",
        help="Which side to analyze.",
    )
    parser.add_argument(
        "--max-distance-cm",
        type=float,
        default=10000.0,
        help="Reject distances above this value before KDE.",
    )
    parser.add_argument(
        "--mad-z",
        type=float,
        default=4.0,
        help="Robust median absolute deviation outlier threshold.",
    )
    parser.add_argument(
        "--output",
        help="Output PNG path. Default: <session>/ranging_kde_<side>.png.",
    )
    parser.add_argument("--show", action="store_true", help="Show the plot window.")
    return parser.parse_args()


def kde(values, grid):
    import numpy as np

    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return np.zeros_like(grid)

    # TODO option A: implement a Gaussian kernel density estimate yourself,
    # then plot the returned density with Matplotlib in main().
    #
    # Hints:
    # - Pick a bandwidth from the distance spread and sample count.
    # - For each measured distance x_i, add a Gaussian centered at x_i.
    # - Normalize so the density integrates to about 1.
    # - A good starting formula is Silverman's rule:
    #   bandwidth = 0.9 * sigma * n ** (-1 / 5)
    #
    # - Matplotlib curve API:
    #   https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.plot.html
    #
    # Return an array with the same shape as `grid`.
    #
    # TODO option B: instead of using this helper, you may change main() to call
    # seaborn.kdeplot(values, ax=ax, label=...) directly.
    # Seaborn API:
    # https://seaborn.pydata.org/generated/seaborn.kdeplot.html
    raise NotImplementedError(
        "TODO: implement KDE here, or update main() to use seaborn.kdeplot()."
    )


def side_values(session_dir, side, max_distance_cm, mad_z):
    samples = find_ranging_samples(session_dir, side)
    raw = [
        s["distance_cm"]
        for s in samples
        if s["status"] == "Ok" and s["distance_cm"] < 60000
    ]
    filtered = filter_distances_cm(raw, max_distance_cm=max_distance_cm)
    return raw, filtered


def main():
    args = parse_args()
    session_dir = Path(args.session).expanduser().resolve()
    if not session_dir.exists():
        raise SystemExit(f"Session does not exist: {session_dir}")

    sides = ["controller", "controlee"] if args.side == "both" else [args.side]
    series = []
    summary = {}
    for side in sides:
        raw, filtered = side_values(
            session_dir,
            side,
            max_distance_cm=args.max_distance_cm,
            mad_z=args.mad_z,
        )
        summary[side] = {
            "raw_ok_count": len(raw),
            "filtered_count": len(filtered),
            "removed_count": len(raw) - len(filtered),
            "mean_cm": sum(filtered) / len(filtered) if filtered else None,
            "median_cm": sorted(filtered)[len(filtered) // 2] if filtered else None,
        }
        if filtered:
            series.append((side, filtered))

    if not series:
        raise SystemExit("No successful ranging samples found after filtering.")

    try:
        import numpy as np
        import matplotlib.pyplot as plt
    except ImportError as exc:
        print_missing_plot_dependency(exc)
        return 2

    all_values = [v for _, values in series for v in values]
    low = min(all_values)
    high = max(all_values)
    pad = max(5.0, 0.1 * (high - low or 1.0))
    grid = np.linspace(low - pad, high + pad, 500)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for side, values in series:
        # TODO: visualize the filtered distance distribution.
        # Option A: keep kde(values, grid), then plot it with ax.plot().
        # Option B: replace these two lines with seaborn.kdeplot().
        # You may keep ax.hist(...) as a helpful reference histogram.
        density = kde(values, grid)
        ax.plot(grid, density, lw=2, label=f"{side} KDE")
        ax.hist(values, bins="auto", density=True, alpha=0.18)
        median = float(np.median(np.asarray(values)))
        ax.axvline(median, ls="--", lw=1, alpha=0.65)

    ax.set_title("Filtered Ranging Distance KDE")
    ax.set_xlabel("Distance (cm)")
    ax.set_ylabel("Density")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()

    output = Path(args.output) if args.output else session_dir / f"ranging_kde_{args.side}.png"
    fig.savefig(output, dpi=180)
    (session_dir / f"ranging_kde_{args.side}_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )

    print(f"Saved plot: {output}")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.show:
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
