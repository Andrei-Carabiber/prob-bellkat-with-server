#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from scripts.analysis.swap_comparison.common import (
    MIXED_EVENT,
    QMDP_MODE,
    PURE_EVENT,
    STATIC_EVENT,
    build_command,
    compute_secret_key_rate_from_split,
    executable_command,
    format_duration,
    load_extremal_payload,
    load_extremal_series,
    run_command,
)
from scripts.plot.config import (
    DEFAULT_PROFILE,
    OPTIMALITY_HEIGHT_INCHES,
    OPTIMALITY_LINE_WIDTH_INCHES,
    PLOT_SETTINGS,
    configure_matplotlib,
    get_plot_profile,
    output_path,
    save_figure,
    style_axes,
)


PROTOCOLS = ("swap", "dist-swap")
BASELINE_PROTOCOL = "swap"
DISTILL_PROTOCOL = "dist-swap"
FILE_PREFIX = "distillation_comparison"
FIGURE_PREFIX = "distillation_comparison"
DEFAULT_OUTPUT_DIR = Path("output/distillation-comparison")
DEFAULT_TRUNCATION = 5000
DEFAULT_P_GE_VALUES_A = "0.005,0.05,0.5"
DEFAULT_W0_VALUES_A = "0.925,0.94,0.955,0.970"


@dataclass(frozen=True)
class DistillationPoint:
    p_ge: float
    w0: float
    p_swap: float | None
    t_coh: int | None


@dataclass(frozen=True)
class PointResult:
    point: DistillationPoint
    skr_by_protocol: dict[str, float]
    coverage_by_protocol: dict[str, float]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Compare plain swap against X-Y-only dist-swap on A-X-Y-C and plot "
            "SKR_swap / SKR_dist-swap over p_ge and w0."
        )
    )
    parser.add_argument("--truncation", type=int, default=DEFAULT_TRUNCATION)
    parser.add_argument(
        "--p-ge-values-a",
        "--p-gen-values-a",
        "--p-ge-values",
        "--p-gen-values",
        dest="p_ge_values_a",
        default=DEFAULT_P_GE_VALUES_A,
        help="Comma-separated 50 km reference p_ge values.",
    )
    parser.add_argument(
        "--w0-values-a",
        "--w0-values",
        dest="w0_values_a",
        default=DEFAULT_W0_VALUES_A,
        help="Comma-separated 50 km reference w0 values.",
    )
    parser.add_argument("--p-swap", "--fixed-p-swap", dest="p_swap", type=float, default=None)
    parser.add_argument("--t-coh", "--fixed-t-coh", dest="t_coh", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--markdown",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "distillation-comparison.md",
    )
    parser.add_argument("--plot-profile", choices=tuple(PLOT_SETTINGS), default=DEFAULT_PROFILE)
    parser.add_argument("--executable", default="quantP_compare_distillation")
    parser.add_argument("--plots-only", action="store_true")
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--resume",
        dest="reuse_existing",
        action="store_true",
        default=True,
        help="Reuse valid results at the requested truncation (default).",
    )
    cache_group.add_argument(
        "--force",
        dest="reuse_existing",
        action="store_false",
        help="Recompute every result even when a matching cached JSON exists.",
    )
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    if args.smoke_test:
        if args.plots_only:
            parser.error("--smoke-test cannot be combined with --plots-only.")
        default_markdown = DEFAULT_OUTPUT_DIR / "distillation-comparison.md"
        args.truncation = 1
        args.p_ge_values_a = first_value(args.p_ge_values_a, "--p-ge-values-a")
        args.w0_values_a = first_value(args.w0_values_a, "--w0-values-a")
        args.output_dir = args.output_dir / "smoke"
        if args.markdown == default_markdown:
            args.markdown = args.output_dir / "distillation-comparison.md"
    return args


def first_value(raw_values: str, flag: str) -> str:
    for raw_value in raw_values.split(","):
        value = raw_value.strip()
        if value:
            return value
    raise SystemExit(f"{flag} must contain at least one value.")


def parse_float_values(raw_values: str, flag: str) -> tuple[float, ...]:
    values = tuple(float(value.strip()) for value in raw_values.split(",") if value.strip())
    if not values:
        raise SystemExit(f"{flag} must contain at least one value.")
    return values


def validate_probability(flag: str, value: float, *, allow_zero: bool = False) -> None:
    lower_ok = value >= 0 if allow_zero else value > 0
    if not lower_ok or value > 1:
        interval = "[0, 1]" if allow_zero else "(0, 1]"
        raise SystemExit(f"{flag} must be in the interval {interval}.")


def validate_args(args) -> None:
    if args.truncation < 0:
        raise SystemExit("--truncation must be non-negative.")
    for value in p_ge_values(args):
        validate_probability("--p-ge-values-a", value)
    for value in w0_values(args):
        validate_probability("--w0-values-a", value, allow_zero=True)
    if args.p_swap is not None:
        validate_probability("--p-swap", args.p_swap, allow_zero=True)
    if args.t_coh is not None and args.t_coh <= 0:
        raise SystemExit("--t-coh must be positive.")


def p_ge_values(args) -> tuple[float, ...]:
    return parse_float_values(args.p_ge_values_a, "--p-ge-values-a")


def w0_values(args) -> tuple[float, ...]:
    return parse_float_values(args.w0_values_a, "--w0-values-a")


def all_points(args) -> list[DistillationPoint]:
    return [
        DistillationPoint(p_ge=p_ge, w0=w0, p_swap=args.p_swap, t_coh=args.t_coh)
        for w0 in w0_values(args)
        for p_ge in p_ge_values(args)
    ]


def value_tag(value: float | int | None) -> str:
    if value is None:
        return "default"
    if isinstance(value, int):
        return str(value)
    return f"{value:.12g}".replace("-", "m").replace("+", "").replace(".", "p")


def value_text(value: float | int | None) -> str:
    if value is None:
        return "default"
    return f"{value:.12g}"


def scenario_tag(point: DistillationPoint) -> str:
    return (
        f"p{value_tag(point.p_ge)}"
        f"_w{value_tag(point.w0)}"
        f"_pswap{value_tag(point.p_swap)}"
        f"_t{value_tag(point.t_coh)}"
    )


def command_args_for_point(point: DistillationPoint) -> list[str]:
    command = ["--p-ge", f"{point.p_ge:.17g}", "--w0", f"{point.w0:.17g}"]
    if point.p_swap is not None:
        command.extend(("--p-swap", f"{point.p_swap:.17g}"))
    if point.t_coh is not None:
        command.extend(("--t-coh", str(point.t_coh)))
    return command


def json_path(data_dir: Path, point: DistillationPoint, protocol: str, event: str) -> Path:
    return data_dir / f"{FILE_PREFIX}_{scenario_tag(point)}_{protocol}_{QMDP_MODE}_{event}.json"


def existing_json_path(
    data_dir: Path,
    point: DistillationPoint,
    protocol: str,
    event: str,
    expected_truncation: int,
) -> Path:
    path = json_path(data_dir, point, protocol, event)
    if not path.is_file():
        raise SystemExit(f"Unusable existing JSON: {path} (file does not exist)")
    if path.stat().st_size == 0:
        raise SystemExit(f"Unusable existing JSON: {path} (file is empty)")
    try:
        payload = load_extremal_payload(path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit(f"Unusable existing JSON: {path} (cannot parse JSON: {exc})") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Unusable existing JSON: {path} (missing extremal object)")
    if not isinstance(payload.get("series"), dict):
        raise SystemExit(f"Unusable existing JSON: {path} (missing extremal.series object)")

    resolved_budget = payload.get("resolved_budget")
    try:
        actual_truncation = int(resolved_budget)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            f"Unusable existing JSON: {path} "
            f"(invalid truncation {resolved_budget!r})"
        ) from exc
    if actual_truncation != expected_truncation:
        raise SystemExit(
            f"Unusable existing JSON: {path} "
            f"(truncation {resolved_budget}, expected {expected_truncation})"
        )
    return path


def build_if_needed(args) -> None:
    if args.no_build or args.plots_only or args.build_performed:
        return
    command = build_command(args.executable)
    if command is not None:
        run_command(command, status_label=f"cabal build {args.executable}")
    args.build_performed = True


def ensure_protocol_jsons(
    point: DistillationPoint,
    protocol: str,
    data_dir: Path,
    args,
) -> tuple[Path, Path, Path]:
    paths = []
    for event in (STATIC_EVENT, PURE_EVENT, MIXED_EVENT):
        target_path = json_path(data_dir, point, protocol, event)
        reused = False
        if args.plots_only or args.reuse_existing:
            try:
                paths.append(
                    existing_json_path(
                        data_dir,
                        point,
                        protocol,
                        event,
                        args.truncation,
                    )
                )
                reused = True
            except SystemExit as exc:
                if args.plots_only:
                    raise
                print(f"{exc}; rerunning {protocol} {event}", flush=True)
                paths.append(target_path)
        else:
            paths.append(target_path)
        if reused:
            print(f"{scenario_tag(point)} {protocol} {event}: reused {paths[-1]}", flush=True)
            continue
        build_if_needed(args)
        command = [
            *executable_command(args.executable),
            "--protocol",
            protocol,
            "--event",
            event,
            *command_args_for_point(point),
            "--json",
            QMDP_MODE,
            "--compute-extremal",
            "--truncation",
            str(args.truncation),
        ]
        status_label = f"{scenario_tag(point)} {protocol} {event}"
        elapsed = run_command(command, stdout_path=paths[-1], status_label=status_label)
        args.executed_cases += 1
        print(f"{status_label}: {elapsed:.2f}s -> {paths[-1]}", flush=True)
    return paths[0], paths[1], paths[2]


def evaluate_point(
    point: DistillationPoint,
    data_dir: Path,
    args,
    *,
    index: int,
    total: int,
) -> PointResult:
    skr_by_protocol = {}
    coverage_by_protocol = {}
    for protocol_index, protocol in enumerate(PROTOCOLS, start=1):
        print(
            f"[progress] point {index}/{total}; protocol {protocol_index}/{len(PROTOCOLS)}: {protocol}",
            flush=True,
        )
        static_path, pure_path, mixed_path = ensure_protocol_jsons(point, protocol, data_dir, args)
        skr_by_protocol[protocol] = compute_secret_key_rate_from_split(pure_path, mixed_path)
        static_series = load_extremal_series(static_path)
        coverage_by_protocol[protocol] = static_series["cdf_min"][-1]
        print(
            f"{scenario_tag(point)} {protocol}: "
            f"SKR={skr_by_protocol[protocol]:.12g}, "
            f"coverage={coverage_by_protocol[protocol]:.12g}",
            flush=True,
        )
    return PointResult(
        point=point,
        skr_by_protocol=skr_by_protocol,
        coverage_by_protocol=coverage_by_protocol,
    )


def swap_over_dist(result: PointResult) -> float:
    swap_skr = result.skr_by_protocol[BASELINE_PROTOCOL]
    dist_skr = result.skr_by_protocol[DISTILL_PROTOCOL]
    return swap_skr / dist_skr if dist_skr > 0 else math.nan


def write_csv(path: Path, results: dict[DistillationPoint, PointResult]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        fieldnames = (
            "scenario",
            "p_ge",
            "w0",
            "p_swap",
            "t_coh",
            "swap_skr",
            "swap_coverage_at_truncation",
            "dist_swap_skr",
            "dist_swap_coverage_at_truncation",
            "dist_swap_over_swap",
            "swap_over_dist_swap",
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for point, result in results.items():
            swap_skr = result.skr_by_protocol[BASELINE_PROTOCOL]
            dist_skr = result.skr_by_protocol[DISTILL_PROTOCOL]
            writer.writerow(
                {
                    "scenario": scenario_tag(point),
                    "p_ge": value_text(point.p_ge),
                    "w0": value_text(point.w0),
                    "p_swap": value_text(point.p_swap),
                    "t_coh": value_text(point.t_coh),
                    "swap_skr": f"{swap_skr:.12g}",
                    "swap_coverage_at_truncation": f"{result.coverage_by_protocol[BASELINE_PROTOCOL]:.12g}",
                    "dist_swap_skr": f"{dist_skr:.12g}",
                    "dist_swap_coverage_at_truncation": f"{result.coverage_by_protocol[DISTILL_PROTOCOL]:.12g}",
                    "dist_swap_over_swap": f"{dist_skr / swap_skr:.12g}" if swap_skr > 0 else "nan",
                    "swap_over_dist_swap": f"{swap_skr / dist_skr:.12g}" if dist_skr > 0 else "nan",
                }
            )


def plot_ratio(plt, figure_dir: Path, results: dict[DistillationPoint, PointResult], args) -> Path:
    from matplotlib.colors import TwoSlopeNorm
    from matplotlib.ticker import NullLocator

    x_values = p_ge_values(args)
    y_values = w0_values(args)
    x = np.array(x_values, dtype=float)
    y = np.array(y_values, dtype=float)
    ratio = np.array(
        [
            [
                swap_over_dist(results[DistillationPoint(p_ge=x_value, w0=y_value, p_swap=args.p_swap, t_coh=args.t_coh)])
                for x_value in x_values
            ]
            for y_value in y_values
        ],
        dtype=float,
    )

    fig, ax = plt.subplots(
        figsize=(OPTIMALITY_LINE_WIDTH_INCHES, OPTIMALITY_HEIGHT_INCHES)
    )
    finite_ratio = ratio[np.isfinite(ratio)]
    contour_kwargs = {"levels": 21, "cmap": "BrBG"}
    if finite_ratio.size > 0:
        ratio_min = float(np.nanmin(finite_ratio))
        ratio_max = float(np.nanmax(finite_ratio))
        if ratio_min < 1.0 < ratio_max:
            contour_kwargs["norm"] = TwoSlopeNorm(vmin=ratio_min, vcenter=1.0, vmax=ratio_max)

    heatmap = ax.contourf(x, y, np.ma.masked_invalid(ratio), **contour_kwargs)
    ax.set_xscale("log")
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xlabel(r"Generation success probability $p_{\mathrm{ge}}$")
    ax.set_ylabel(r"Initial Werner parameter $w_0$")
    ax.set_xticks(x)
    ax.set_yticks(y)
    ax.set_xticklabels([f"{value:.12g}" for value in x_values])
    ax.set_yticklabels([f"{value:.12g}" for value in y_values])
    style_axes(ax)
    fig.colorbar(
        heatmap,
        ax=ax,
        label=r"$\mathrm{SKR}_{\mathrm{swap}}/\mathrm{SKR}_{\mathrm{dist\!-\!swap}}$",
    )

    plot_profile = get_plot_profile(args.plot_profile)
    figure_path = output_path(figure_dir, FIGURE_PREFIX, "swap_over_dist_swap", plot_profile)
    save_figure(fig, figure_path, bbox_inches=None)
    plt.close(fig)
    return figure_path


def relative_link(from_path: Path, to_path: Path) -> str:
    return os.path.relpath(to_path, start=from_path.parent)


def write_report(markdown_path: Path, args, csv_path: Path, figure_path: Path | None) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Distillation Comparison",
        "",
        "Generated by `scripts/run_distillation_comparison.py`.",
        "",
        "## Configuration",
        "",
        f"- `p_ge_50km={args.p_ge_values_a}`",
        f"- `w0_50km={args.w0_values_a}`",
        f"- `p_swap={value_text(args.p_swap)}`",
        f"- `t_coh={value_text(args.t_coh)}`",
        f"- `truncation={args.truncation}`",
        "- `dist-swap`: distill `X-Y` only; generate `A-X` and `Y-C` once",
        f"- command: `{' '.join(sys.argv)}`",
        "",
        "## Data",
        "",
        f"- SKR CSV: [{csv_path.name}]({relative_link(markdown_path, csv_path)})",
        "",
    ]
    if figure_path is not None:
        lines.extend(
            [
                "## Figure",
                "",
                f"![swap over dist-swap]({relative_link(markdown_path, figure_path)})",
                "",
            ]
        )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    validate_args(args)
    data_dir = args.output_dir / "data"
    figure_dir = args.output_dir / "figures"
    if args.plots_only:
        if not data_dir.is_dir():
            raise SystemExit(f"--plots-only requires existing data directory: {data_dir}")
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)

    args.build_performed = False
    args.executed_cases = 0
    points = all_points(args)
    started = time.perf_counter()
    results = {}
    print(f"[progress] starting {len(points)} point(s), {len(PROTOCOLS)} protocols per point", flush=True)
    for index, point in enumerate(points, start=1):
        elapsed = time.perf_counter() - started
        print(f"[progress] point {index}/{len(points)} after {format_duration(elapsed)}: {scenario_tag(point)}", flush=True)
        results[point] = evaluate_point(point, data_dir, args, index=index, total=len(points))

    if args.reuse_existing and not args.plots_only and args.executed_cases == 0:
        print("All simulation results were reused; Cabal build and execution were skipped.", flush=True)

    csv_path = args.output_dir / f"{FILE_PREFIX}_skr.csv"
    write_csv(csv_path, results)
    print(f"Wrote SKRs to {csv_path}", flush=True)

    figure_path = None
    if args.smoke_test:
        print("Smoke test: skipped contour figure for the one-point grid.", flush=True)
    else:
        plt = configure_matplotlib(args.plot_profile)
        figure_path = plot_ratio(plt, figure_dir, results, args)
        print(f"Saved distillation comparison figure to {figure_path}", flush=True)

    write_report(args.markdown, args, csv_path, figure_path)
    print(f"Wrote markdown report to {args.markdown}", flush=True)


if __name__ == "__main__":
    main()
