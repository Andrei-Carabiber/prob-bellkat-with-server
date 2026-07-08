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

import numpy as np

from scripts.analysis.swap_comparison.common import (
    MDP_MODE,
    QMDP_MODE,
    MIXED_EVENT,
    PURE_EVENT,
    STATIC_EVENT,
    build_command,
    compute_secret_key_rate_from_split,
    executable_command,
    format_duration,
    load_coverage_budget,
    run_command,
    validate_extremal_json,
)
from scripts.plot.config import (
    DEFAULT_PROFILE,
    PLOT_SETTINGS,
    configure_matplotlib,
    get_plot_profile,
    output_path,
    save_figure,
    style_axes,
)


BASELINE_PROTOCOL = "swap-asap"
DOUBLING_PROTOCOL = "doubling"
SEQUENTIAL_PROTOCOLS = ("left-to-right", "right-to-left")
PROTOCOLS = (BASELINE_PROTOCOL, DOUBLING_PROTOCOL, *SEQUENTIAL_PROTOCOLS)
FILE_PREFIX = "swap_scheme_optimality"
FIGURE_PREFIX = "swap_scheme_optimality"
DEFAULT_OUTPUT_DIR = Path("output/swap-scheme-optimality")
DEFAULT_OPTIMALITY_TRUNCATION = 5000
LOG_AXES = {"p-gen", "edge-skew"}
AXIS_LABELS = {
    "p-gen": r"Reference 50 km $p_{\mathrm{ge}}$",
    "w0": r"Reference 50 km Werner parameter $w_0$",
    "edge-skew": r"Right-edge skew penalty $\eta$",
}


@dataclass(frozen=True)
class RatioJob:
    name: str
    x_axis: str
    y_axis: str
    numerator_protocols: tuple[str, ...]
    numerator_label: str
    caption: str


@dataclass(frozen=True)
class SchemePoint:
    p_gen: float | None
    edge_skew: float | None
    t_coh: int | None
    p_swap: float | None
    w0: float | None


@dataclass(frozen=True)
class PointResult:
    point: SchemePoint
    skr_by_protocol: dict[str, float]


@dataclass(frozen=True)
class RatioResult:
    point: SchemePoint
    ratio: float
    numerator_protocol: str
    numerator_skr: float
    baseline_skr: float


DEFAULT_JOBS = (
    RatioJob(
        name="doubling_over_swap_asap",
        x_axis="p-gen",
        y_axis="w0",
        numerator_protocols=(DOUBLING_PROTOCOL,),
        numerator_label="doubling",
        caption=r"$\mathrm{SKR}_{\mathrm{doubling}}/\mathrm{SKR}_{\mathrm{swap\mbox{-}asap}}$",
    ),
    RatioJob(
        name="sequential_over_swap_asap",
        x_axis="p-gen",
        y_axis="edge-skew",
        numerator_protocols=SEQUENTIAL_PROTOCOLS,
        numerator_label="best sequential",
        caption=r"$\max\mathrm{SKR}_{\mathrm{sequential}}/\mathrm{SKR}_{\mathrm{swap\mbox{-}asap}}$",
    ),
)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run a compact topology-aware swap-scheme optimality analysis. "
            "The script plots doubling/swap-asap over 50 km p_ge and 50 km w0, "
            "and the best sequential order/swap-asap over 50 km p_ge and skew."
        )
    )
    budget_group = parser.add_mutually_exclusive_group()
    budget_group.add_argument(
        "--truncation",
        type=int,
        default=DEFAULT_OPTIMALITY_TRUNCATION,
        help="QMDP pure/mixed truncation budget.",
    )
    budget_group.add_argument(
        "--coverage",
        type=float,
        default=None,
        help=(
            "Resolve an MDP/static budget per point/protocol until the "
            "worst-scheduler CDF reaches this coverage, then reuse the maximum "
            "budget for QMDP pure/mixed runs."
        ),
    )
    parser.add_argument(
        "--p-ge-values",
        "--p-gen-values",
        dest="p_gen_values",
        default="0.01,0.1,1.0",
        help="Comma-separated 50 km reference p_ge values.",
    )
    parser.add_argument(
        "--w0-values",
        default="0.961,0.985,1.0",
        help="Comma-separated 50 km reference w0 values.",
    )
    parser.add_argument(
        "--edge-skew-values",
        default="1,4,16",
        help="Comma-separated rightmost-link skew penalties.",
    )
    parser.add_argument("--p-ge", "--fixed-p-ge", dest="fixed_p_gen", type=float, default=None, help="Fixed 50 km p_ge for jobs where p_ge is not an axis; omitted values use the executable default.")
    parser.add_argument("--w0", "--fixed-w0", dest="fixed_w0", type=float, default=None, help="Fixed 50 km w0 for jobs where w0 is not an axis; omitted values use the executable default.")
    parser.add_argument("--edge-skew", "--fixed-edge-skew", dest="fixed_edge_skew", type=float, default=None, help="Fixed edge skew for jobs where edge skew is not an axis; omitted values use the executable default.")
    parser.add_argument("--t-coh", "--fixed-t-coh", dest="fixed_t_coh", type=int, default=None, help="Fixed coherence time; omitted values use the executable default.")
    parser.add_argument("--p-swap", "--fixed-p-swap", dest="fixed_p_swap", type=float, default=None, help="Fixed swap probability; omitted values use the executable default.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Root directory for JSON dumps, CSVs, figures, and report.",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "swap-scheme-optimality.md",
        help="Markdown report path.",
    )
    parser.add_argument(
        "--plot-profile",
        choices=tuple(PLOT_SETTINGS),
        default=DEFAULT_PROFILE,
        help="Plot styling profile.",
    )
    parser.add_argument(
        "--executable",
        default="quantP_compare_swap_parameters",
        help="Cabal executable name, or path to an already-built executable.",
    )
    parser.add_argument("--plots-only", action="store_true", help="Reuse existing JSONs and only rewrite CSVs/plots/report.")
    parser.add_argument("--resume", action="store_true", help="Reuse valid existing JSONs and run only missing cases.")
    parser.add_argument("--no-build", action="store_true", help="Skip the initial cabal build step.")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run a one-point truncation-1 sweep under an output smoke directory.",
    )
    args = parser.parse_args()
    if args.smoke_test:
        if args.plots_only:
            parser.error("--smoke-test cannot be combined with --plots-only.")
        default_markdown = DEFAULT_OUTPUT_DIR / "swap-scheme-optimality.md"
        args.coverage = None
        args.truncation = 1
        args.p_gen_values = smoke_value_text(args.fixed_p_gen, args.p_gen_values)
        args.w0_values = smoke_value_text(args.fixed_w0, args.w0_values)
        args.edge_skew_values = smoke_value_text(args.fixed_edge_skew, args.edge_skew_values)
        args.output_dir = args.output_dir / "smoke"
        if args.markdown == default_markdown:
            args.markdown = args.output_dir / "swap-scheme-optimality.md"
    return args


def smoke_value_text(fixed_value: float | None, axis_values_text: str) -> str:
    if fixed_value is not None:
        return f"{fixed_value:.12g}"
    for part in axis_values_text.split(","):
        value = part.strip()
        if value:
            return value
    raise SystemExit("--smoke-test requires at least one value on each plotted axis.")


def log(message: str = "") -> None:
    print(message, flush=True)


def parse_float_values(raw: str, flag: str) -> tuple[float, ...]:
    values = tuple(float(part.strip()) for part in raw.split(",") if part.strip())
    if not values:
        raise SystemExit(f"{flag} must contain at least one value.")
    return values


def validate_probability(flag: str, value: float, *, allow_zero: bool = False) -> None:
    lower_ok = value >= 0 if allow_zero else value > 0
    if not lower_ok or value > 1:
        interval = "[0, 1]" if allow_zero else "(0, 1]"
        raise SystemExit(f"{flag} must be in the interval {interval}.")


def validate_args(args) -> None:
    if args.coverage is not None:
        validate_probability("--coverage", args.coverage)
    if args.truncation is not None and args.truncation < 0:
        raise SystemExit("--truncation must be non-negative.")
    if args.fixed_p_gen is not None:
        validate_probability("--p-ge", args.fixed_p_gen)
    if args.fixed_w0 is not None:
        validate_probability("--w0", args.fixed_w0, allow_zero=True)
    if args.fixed_p_swap is not None:
        validate_probability("--p-swap", args.fixed_p_swap, allow_zero=True)
    if args.fixed_edge_skew is not None and args.fixed_edge_skew < 1:
        raise SystemExit("--edge-skew must be at least 1.")
    if args.fixed_t_coh is not None and args.fixed_t_coh <= 0:
        raise SystemExit("--t-coh must be positive.")
    for value in axis_values("p-gen", args):
        validate_probability("--p-ge-values", value)
    for value in axis_values("w0", args):
        validate_probability("--w0-values", value, allow_zero=True)
    for value in axis_values("edge-skew", args):
        if value < 1:
            raise SystemExit("--edge-skew-values entries must be at least 1.")


def axis_values(axis: str, args) -> tuple[float, ...]:
    if axis == "p-gen":
        return parse_float_values(args.p_gen_values, "--p-ge-values")
    if axis == "w0":
        return parse_float_values(args.w0_values, "--w0-values")
    if axis == "edge-skew":
        return parse_float_values(args.edge_skew_values, "--edge-skew-values")
    raise AssertionError(f"Unexpected axis: {axis}")


def fixed_values(args) -> dict[str, float | int | None]:
    return {
        "p-gen": args.fixed_p_gen,
        "w0": args.fixed_w0,
        "edge-skew": args.fixed_edge_skew,
        "t-coh": args.fixed_t_coh,
        "p-swap": args.fixed_p_swap,
    }


def point_from_values(values: dict[str, float | int | None]) -> SchemePoint:
    return SchemePoint(
        p_gen=float(values["p-gen"]) if values["p-gen"] is not None else None,
        edge_skew=float(values["edge-skew"]) if values["edge-skew"] is not None else None,
        t_coh=int(values["t-coh"]) if values["t-coh"] is not None else None,
        p_swap=float(values["p-swap"]) if values["p-swap"] is not None else None,
        w0=float(values["w0"]) if values["w0"] is not None else None,
    )


def point_for_job(job: RatioJob, x_value: float, y_value: float, args) -> SchemePoint:
    values = fixed_values(args)
    values[job.x_axis] = x_value
    values[job.y_axis] = y_value
    return point_from_values(values)


def all_points(args) -> list[SchemePoint]:
    points = []
    seen = set()
    for job in DEFAULT_JOBS:
        for y_value in axis_values(job.y_axis, args):
            for x_value in axis_values(job.x_axis, args):
                point = point_for_job(job, x_value, y_value, args)
                if point not in seen:
                    seen.add(point)
                    points.append(point)
    return points


def command_args_for_point(point: SchemePoint) -> list[str]:
    args = []
    if point.p_gen is not None:
        args.extend(("--p-gen", f"{point.p_gen:.17g}"))
    if point.p_swap is not None:
        args.extend(("--p-swap", f"{point.p_swap:.17g}"))
    if point.w0 is not None:
        args.extend(("--w0", f"{point.w0:.17g}"))
    if point.t_coh is not None:
        args.extend(("--t-coh", str(point.t_coh)))
    if point.edge_skew is not None:
        args.extend(("--edge-skew", f"{point.edge_skew:.17g}"))
    return args


def value_tag(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return f"{value:.12g}".replace("-", "m").replace("+", "").replace(".", "p")


def optional_value_tag(value: float | int | None) -> str:
    if value is None:
        return "default"
    return value_tag(value)


def value_text(value: float | int | None) -> str:
    if value is None:
        return "default"
    return f"{value:.12g}"


def fixed_value_text(value: float | int | None) -> str:
    if value is None:
        return "example default"
    return f"{value:g}"


def scenario_tag(point: SchemePoint) -> str:
    return (
        f"p{optional_value_tag(point.p_gen)}"
        f"_skew{optional_value_tag(point.edge_skew)}"
        f"_t{optional_value_tag(point.t_coh)}"
        f"_pswap{optional_value_tag(point.p_swap)}"
        f"_w{optional_value_tag(point.w0)}"
    )


def json_path(data_dir: Path, point: SchemePoint, protocol: str, mode: str, event: str) -> Path:
    return data_dir / f"{FILE_PREFIX}_{scenario_tag(point)}_{protocol}_{mode}_{event}.json"


def existing_json_path(data_dir: Path, point: SchemePoint, protocol: str, mode: str, event: str) -> Path:
    path = json_path(data_dir, point, protocol, mode, event)
    require_coverage = mode == MDP_MODE and event == STATIC_EVENT
    valid, reason = validate_extremal_json(path, require_coverage=require_coverage)
    if valid:
        return path
    raise SystemExit(f"Unusable existing JSON: {path} ({reason})")


def run_extremal_case(
    executable: str,
    protocol: str,
    mode: str,
    event: str,
    point: SchemePoint,
    budget_flag: str,
    budget_value: float | int,
    target_path: Path,
) -> float:
    command = [
        *executable_command(executable),
        "--protocol",
        protocol,
        "--event",
        event,
        *command_args_for_point(point),
        "--json",
        mode,
        "--compute-extremal",
        budget_flag,
        str(budget_value),
    ]
    status_label = f"{scenario_tag(point)} {protocol} {mode}/{event}"
    return run_command(command, stdout_path=target_path, status_label=status_label)


def resolve_budget(point: SchemePoint, data_dir: Path, args) -> int:
    if args.coverage is None:
        return args.truncation

    budgets = []
    for protocol in PROTOCOLS:
        target_path = json_path(data_dir, point, protocol, MDP_MODE, STATIC_EVENT)
        if args.plots_only:
            path = existing_json_path(data_dir, point, protocol, MDP_MODE, STATIC_EVENT)
        elif args.resume:
            try:
                path = existing_json_path(data_dir, point, protocol, MDP_MODE, STATIC_EVENT)
                log(f"{scenario_tag(point)} {protocol} coverage: reused {path}")
            except SystemExit as exc:
                log(f"{exc}; rerunning {protocol} {MDP_MODE}/{STATIC_EVENT}")
                path = target_path
                elapsed = run_extremal_case(
                    args.executable,
                    protocol,
                    MDP_MODE,
                    STATIC_EVENT,
                    point,
                    "--coverage",
                    args.coverage,
                    path,
                )
                log(f"{scenario_tag(point)} {protocol} coverage: {elapsed:.2f}s -> {path}")
        else:
            path = target_path
            elapsed = run_extremal_case(
                args.executable,
                protocol,
                MDP_MODE,
                STATIC_EVENT,
                point,
                "--coverage",
                args.coverage,
                path,
            )
            log(f"{scenario_tag(point)} {protocol} coverage: {elapsed:.2f}s -> {path}")
        resolved_budget, coverage_value = load_coverage_budget(protocol, args.coverage, path)
        budgets.append(resolved_budget)
        log(f"{scenario_tag(point)} {protocol}: coverage {coverage_value:.12g} at R={resolved_budget}")
    return max(budgets)


def ensure_protocol_jsons(
    point: SchemePoint,
    protocol: str,
    data_dir: Path,
    args,
    budget: int,
) -> tuple[Path, Path]:
    paths = []
    for event in (PURE_EVENT, MIXED_EVENT):
        target_path = json_path(data_dir, point, protocol, QMDP_MODE, event)
        reused = False
        if args.plots_only:
            paths.append(existing_json_path(data_dir, point, protocol, QMDP_MODE, event))
            continue
        if args.resume:
            try:
                paths.append(existing_json_path(data_dir, point, protocol, QMDP_MODE, event))
                reused = True
            except SystemExit as exc:
                log(f"{exc}; rerunning {protocol} {QMDP_MODE}/{event}")
                paths.append(target_path)
        else:
            paths.append(target_path)
        if reused:
            log(f"{scenario_tag(point)} {protocol} {event}: reused {paths[-1]}")
            continue
        elapsed = run_extremal_case(
            args.executable,
            protocol,
            QMDP_MODE,
            event,
            point,
            "--truncation",
            budget,
            paths[-1],
        )
        log(f"{scenario_tag(point)} {protocol} {event}: {elapsed:.2f}s -> {paths[-1]}")
    return paths[0], paths[1]


def evaluate_point(point: SchemePoint, data_dir: Path, args, *, index: int, total: int) -> PointResult:
    budget = 0 if args.plots_only else resolve_budget(point, data_dir, args)
    skr_by_protocol = {}
    for protocol_index, protocol in enumerate(PROTOCOLS, start=1):
        log(f"[progress] point {index}/{total}; protocol {protocol_index}/{len(PROTOCOLS)}: {protocol}")
        pure_path, mixed_path = ensure_protocol_jsons(point, protocol, data_dir, args, budget)
        skr_by_protocol[protocol] = compute_secret_key_rate_from_split(pure_path, mixed_path)
        log(f"{scenario_tag(point)} {protocol}: SKR={skr_by_protocol[protocol]:.12g}")
    return PointResult(point=point, skr_by_protocol=skr_by_protocol)


def ratio_result(job: RatioJob, point_result: PointResult) -> RatioResult:
    baseline_skr = point_result.skr_by_protocol[BASELINE_PROTOCOL]
    numerator_protocol = max(
        job.numerator_protocols,
        key=lambda protocol: point_result.skr_by_protocol[protocol],
    )
    numerator_skr = point_result.skr_by_protocol[numerator_protocol]
    ratio = numerator_skr / baseline_skr if baseline_skr > 0 else math.nan
    return RatioResult(
        point=point_result.point,
        ratio=ratio,
        numerator_protocol=numerator_protocol,
        numerator_skr=numerator_skr,
        baseline_skr=baseline_skr,
    )


def ratio_grid(job: RatioJob, results: dict[SchemePoint, PointResult], args):
    x_values = axis_values(job.x_axis, args)
    y_values = axis_values(job.y_axis, args)
    grid = [
        [
            ratio_result(job, results[point_for_job(job, x_value, y_value, args)])
            for x_value in x_values
        ]
        for y_value in y_values
    ]
    return x_values, y_values, grid


def axis_label(axis: str) -> str:
    return AXIS_LABELS[axis]


def require_axis_number(axis: str, value: float | int | None) -> float:
    if value is None:
        raise AssertionError(f"Axis {axis} unexpectedly used an example default value.")
    return float(value)


def tick_label(value: float | int) -> str:
    return f"{float(value):.12g}"


def plot_ratio(plt, figure_dir: Path, job: RatioJob, results: dict[SchemePoint, PointResult], args) -> Path:
    from matplotlib.colors import TwoSlopeNorm
    from matplotlib.ticker import NullLocator

    x_values, y_values, grid = ratio_grid(job, results, args)
    x = np.array([require_axis_number(job.x_axis, value) for value in x_values], dtype=float)
    y = np.array([require_axis_number(job.y_axis, value) for value in y_values], dtype=float)
    ratio = np.array([[entry.ratio for entry in row] for row in grid], dtype=float)
    advantage = np.array(
        [[entry.numerator_skr - entry.baseline_skr for entry in row] for row in grid],
        dtype=float,
    )

    fig, ax = plt.subplots()
    finite_ratio = ratio[np.isfinite(ratio)]
    contour_kwargs = {"levels": 21, "cmap": "coolwarm"}
    if finite_ratio.size > 0:
        ratio_min = float(np.nanmin(finite_ratio))
        ratio_max = float(np.nanmax(finite_ratio))
        if ratio_min < 1.0 < ratio_max:
            contour_kwargs["norm"] = TwoSlopeNorm(vmin=ratio_min, vcenter=1.0, vmax=ratio_max)

    heatmap = ax.contourf(x, y, np.ma.masked_invalid(ratio), **contour_kwargs)
    finite_advantage = advantage[np.isfinite(advantage)]
    if finite_advantage.size > 0 and np.nanmin(finite_advantage) <= 0 <= np.nanmax(finite_advantage):
        boundary = ax.contour(x, y, advantage, levels=[0.0], colors="black", linewidths=1.2)
        if boundary.allsegs[0]:
            ax.plot([], [], color="black", linewidth=1.2, label="Equal SKR")

    if job.x_axis in LOG_AXES:
        ax.set_xscale("log")
        ax.xaxis.set_minor_locator(NullLocator())
    if job.y_axis in LOG_AXES:
        ax.set_yscale("log")
        ax.yaxis.set_minor_locator(NullLocator())
    ax.set_xlabel(axis_label(job.x_axis))
    ax.set_ylabel(axis_label(job.y_axis))
    ax.set_xticks(x)
    ax.set_yticks(y)
    ax.set_xticklabels([tick_label(require_axis_number(job.x_axis, value)) for value in x_values])
    ax.set_yticklabels([tick_label(require_axis_number(job.y_axis, value)) for value in y_values])
    ax.set_title(job.numerator_label + " over swap-asap")
    style_axes(ax)
    fig.colorbar(heatmap, ax=ax, label=job.caption)
    add_external_legend(ax)

    plot_profile = get_plot_profile(args.plot_profile)
    figure_path = output_path(figure_dir, FIGURE_PREFIX, job.name, plot_profile)
    save_figure(fig, figure_path)
    plt.close(fig)
    return figure_path


def add_external_legend(ax) -> None:
    handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return
    ax.legend(
        handles,
        labels,
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.26),
        ncol=min(3, len(handles)),
        borderaxespad=0.0,
        columnspacing=1.1,
        handletextpad=0.5,
    )


def write_protocol_csv(path: Path, results: dict[SchemePoint, PointResult]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        fieldnames = ("scenario", "p_ge", "w0", "edge_skew", "t_coh", "p_swap", "protocol", "skr")
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for point_result in results.values():
            point = point_result.point
            for protocol, skr in point_result.skr_by_protocol.items():
                writer.writerow(
                    {
                        "scenario": scenario_tag(point),
                        "p_ge": value_text(point.p_gen),
                        "w0": value_text(point.w0),
                        "edge_skew": value_text(point.edge_skew),
                        "t_coh": value_text(point.t_coh),
                        "p_swap": value_text(point.p_swap),
                        "protocol": protocol,
                        "skr": f"{skr:.12g}",
                    }
                )


def write_ratio_csv(path: Path, results: dict[SchemePoint, PointResult], args) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        fieldnames = (
            "job",
            "scenario",
            "p_ge",
            "w0",
            "edge_skew",
            "numerator",
            "numerator_skr",
            "swap_asap_skr",
            "ratio",
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for job in DEFAULT_JOBS:
            for y_value in axis_values(job.y_axis, args):
                for x_value in axis_values(job.x_axis, args):
                    point = point_for_job(job, x_value, y_value, args)
                    ratio = ratio_result(job, results[point])
                    writer.writerow(
                        {
                            "job": job.name,
                            "scenario": scenario_tag(point),
                            "p_ge": value_text(point.p_gen),
                            "w0": value_text(point.w0),
                            "edge_skew": value_text(point.edge_skew),
                            "numerator": ratio.numerator_protocol,
                            "numerator_skr": f"{ratio.numerator_skr:.12g}",
                            "swap_asap_skr": f"{ratio.baseline_skr:.12g}",
                            "ratio": f"{ratio.ratio:.12g}",
                        }
                    )


def relative_link(from_path: Path, to_path: Path) -> str:
    return os.path.relpath(to_path, start=from_path.parent)


def write_report(
    markdown_path: Path,
    args,
    protocol_csv: Path,
    ratio_csv: Path,
    figures: list[tuple[RatioJob, Path]],
    results: dict[SchemePoint, PointResult],
) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    budget_text = (
        "plots-only"
        if args.plots_only
        else (f"coverage={args.coverage:g}" if args.coverage is not None else f"truncation={args.truncation}")
    )
    lines = [
        "# Swap Scheme Optimality",
        "",
        "Generated by `scripts.analysis.swap_comparison.runner_scheme_optimality`.",
        "",
        "## Configuration",
        "",
        f"- 50 km `p_ge` values: `{args.p_gen_values}`",
        f"- 50 km `w0` values: `{args.w0_values}`",
        f"- `edge_skew` values: `{args.edge_skew_values}`",
        (
            f"- fixed values: `p_ge_50km={fixed_value_text(args.fixed_p_gen)}, "
            f"w0_50km={fixed_value_text(args.fixed_w0)}, "
            f"edge_skew={fixed_value_text(args.fixed_edge_skew)}, "
            f"t_coh={fixed_value_text(args.fixed_t_coh)}, "
            f"p_swap={fixed_value_text(args.fixed_p_swap)}`"
        ),
        f"- budget: `{budget_text}`",
        f"- protocols: `{', '.join(PROTOCOLS)}`",
        f"- command: `{' '.join(sys.argv)}`",
        "",
        "## Data",
        "",
        f"- Protocol SKR CSV: [{protocol_csv.name}]({relative_link(markdown_path, protocol_csv)})",
        f"- Ratio CSV: [{ratio_csv.name}]({relative_link(markdown_path, ratio_csv)})",
        "",
        "## Figures",
        "",
    ]
    for job, figure_path in figures:
        lines.extend(
            [
                f"### {job.numerator_label} over swap-asap",
                "",
                f"![{job.name}]({relative_link(markdown_path, figure_path)})",
                "",
            ]
        )

    lines.extend(["## Ratio Summary", ""])
    for job in DEFAULT_JOBS:
        ratios = []
        for y_value in axis_values(job.y_axis, args):
            for x_value in axis_values(job.x_axis, args):
                point = point_for_job(job, x_value, y_value, args)
                ratios.append(ratio_result(job, results[point]))
        finite = [entry.ratio for entry in ratios if math.isfinite(entry.ratio)]
        if finite:
            best = max(ratios, key=lambda entry: entry.ratio)
            lines.append(
                f"- `{job.name}`: min={min(finite):.6g}, max={max(finite):.6g}; "
                f"best point `{scenario_tag(best.point)}` via `{best.numerator_protocol}`."
            )
    lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    validate_args(args)
    data_dir = args.output_dir / "data"
    figure_dir = args.output_dir / "figures"
    if args.plots_only:
        if not data_dir.is_dir():
            raise SystemExit(f"--plots-only requires existing data directory: {data_dir}")
        log("--plots-only: using existing JSONs; Cabal build/run steps are skipped.")
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_build and not args.plots_only:
        command = build_command(args.executable)
        if command is not None:
            run_command(command, status_label=f"cabal build {args.executable}")

    points = all_points(args)
    started = time.perf_counter()
    results = {}
    log(f"[progress] starting {len(points)} point(s), {len(PROTOCOLS)} protocols per point")
    for index, point in enumerate(points, start=1):
        log()
        elapsed = time.perf_counter() - started
        log(f"[progress] point {index}/{len(points)} after {format_duration(elapsed)}: {scenario_tag(point)}")
        results[point] = evaluate_point(point, data_dir, args, index=index, total=len(points))

    protocol_csv = args.output_dir / f"{FILE_PREFIX}_protocol_skr.csv"
    ratio_csv = args.output_dir / f"{FILE_PREFIX}_ratios.csv"
    write_protocol_csv(protocol_csv, results)
    write_ratio_csv(ratio_csv, results, args)
    log()
    log(f"Wrote protocol SKRs to {protocol_csv}")
    log(f"Wrote ratios to {ratio_csv}")

    plt = configure_matplotlib(args.plot_profile)
    figures = []
    if args.smoke_test:
        log("Smoke test: skipped contour figures for the one-point grid.")
    else:
        for job in DEFAULT_JOBS:
            figure_path = plot_ratio(plt, figure_dir, job, results, args)
            figures.append((job, figure_path))
            log(f"Saved {job.name} figure to {figure_path}")

    write_report(args.markdown, args, protocol_csv, ratio_csv, figures, results)
    log(f"Wrote markdown report to {args.markdown}")


if __name__ == "__main__":
    main()
