#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import itertools
import math
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from scripts.analysis.swap_comparison import runner_optimality_contours as opt
from scripts.analysis.swap_comparison.common import (
    MDP_MODE,
    QMDP_MODE,
    MIXED_EVENT,
    PURE_EVENT,
    STATIC_EVENT,
    compute_secret_key_rate_from_split,
    format_duration,
    load_coverage_budget,
    run_command,
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


P_GEN_VALUES = (1 / 32, 1 / 8, 1 / 2, 1 / 1)
EDGE_SKEW_VALUES = (1.0, )
MULTIPLEXING_VALUES = (1, 2, 4, 8)
T_COH_VALUES = (5000, 50000, 500000)
P_SWAP_VALUES = (0.5, 0.75, 1.0)
W0_VALUES = (0.952, 0.968, 0.985, 1.0)
DEFAULT_P_GEN = 1 / 8
DEFAULT_EDGE_SKEW = 1.0
DEFAULT_MULTIPLEXING = 1
DEFAULT_OUTPUT_DIR = Path("output/analyze-sim-swap")
DEFAULT_T_COH = 5000
DEFAULT_P_SWAP = 1.0
DEFAULT_W0 = 0.985
DEFAULT_COVERAGE = 0.99
FILE_PREFIX = "sim_swap_optimality"
FIGURE_PREFIX = "sim_swap_optimality"
EFFECTIVE_MODEL = "effective"
DIRECT_MODEL = "direct"
MULTIPLEXING_MODELS = (EFFECTIVE_MODEL, DIRECT_MODEL)
SWEEP_AXES = opt.AXES
DEFAULT_CONTOUR_PAIRS = tuple(itertools.combinations(SWEEP_AXES, 2))
DEFAULT_BASELINE_PROTOCOLS = ("left-to-right", "doubling")
RIGHT_TO_LEFT_PROTOCOL = "right-to-left"


@dataclass(frozen=True)
class ContourJob:
    spec: opt.ContourSpec
    fixed_values: tuple[tuple[str, float | int], ...]
    caption: str


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run the simultaneous/at-last swap hypothesis suite on the requested "
            "p_ge, heterogeneity, and multiplexing grid, then write CSVs, "
            "contour figures, and a markdown report."
        )
    )
    budget_group = parser.add_mutually_exclusive_group()
    budget_group.add_argument(
        "--coverage",
        type=float,
        default=None,
        help=(
            "Resolve an MDP/static budget per point/protocol until the "
            "worst-scheduler CDF reaches this probability. Defaults to "
            f"{DEFAULT_COVERAGE:g} when --plots-only is not used."
        ),
    )
    budget_group.add_argument(
        "--truncation",
        type=int,
        default=None,
        help="Use a fixed QMDP pure/mixed truncation budget instead of coverage.",
    )
    parser.add_argument(
        "--multiplexing-model",
        choices=MULTIPLEXING_MODELS,
        default=EFFECTIVE_MODEL,
        help=(
            "Use 'effective' to collapse identical multiplexed attempts into "
            "equivalent link probabilities, or 'direct' to run repeated parallel "
            "ucreate attempts through NetworkCapacity literally."
        ),
    )
    parser.add_argument(
        "--contour",
        action="append",
        default=None,
        help=(
            "Contour axes as X,Y. Choices are p-gen, multiplexing, edge-skew, "
            "t-coh, p-swap, and w0. Aliases such as p_ge, p_swap, and w_0 "
            "are accepted. Can be passed multiple times. If omitted, the "
            "script evaluates the full six-dimensional grid and writes one "
            "fixed-slice contour for each pair of dimensions."
        ),
    )
    parser.add_argument(
        "--p-ge-values",
        "--p-gen-values",
        dest="p_gen_values",
        default=",".join(f"{value:.17g}" for value in P_GEN_VALUES),
        help="Comma-separated values for p-ge/p-gen axes.",
    )
    parser.add_argument(
        "--edge-skew-values",
        default=",".join(f"{value:.17g}" for value in EDGE_SKEW_VALUES),
        help="Comma-separated values for edge-skew axes.",
    )
    parser.add_argument(
        "--multiplexing-values",
        default=",".join(str(value) for value in MULTIPLEXING_VALUES),
        help="Comma-separated integer values for multiplexing axes.",
    )
    parser.add_argument(
        "--t-coh-values",
        default=",".join(str(value) for value in T_COH_VALUES),
        help="Comma-separated integer values for t-coh axes.",
    )
    parser.add_argument(
        "--p-swap-values",
        default=",".join(f"{value:.17g}" for value in P_SWAP_VALUES),
        help="Comma-separated values for p-swap axes.",
    )
    parser.add_argument(
        "--w0-values",
        default=",".join(f"{value:.17g}" for value in W0_VALUES),
        help="Comma-separated values for w0 axes.",
    )
    parser.add_argument("--p-ge", "--fixed-p-ge", dest="fixed_p_gen", type=float, default=DEFAULT_P_GEN)
    parser.add_argument(
        "--edge-skew",
        "--fixed-edge-skew",
        dest="fixed_edge_skew",
        type=float,
        default=DEFAULT_EDGE_SKEW,
    )
    parser.add_argument(
        "--multiplexing",
        "--fixed-multiplexing",
        dest="fixed_multiplexing",
        type=int,
        default=DEFAULT_MULTIPLEXING,
    )
    parser.add_argument("--t-coh", type=int, default=DEFAULT_T_COH)
    parser.add_argument("--p-swap", type=float, default=DEFAULT_P_SWAP)
    parser.add_argument("--w0", type=float, default=DEFAULT_W0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Root directory for data, figures, and markdown report.",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "simultaneous-swap-optimality.md",
        help="Markdown report path to write after the run.",
    )
    parser.add_argument(
        "--plot-profile",
        choices=tuple(PLOT_SETTINGS),
        default=DEFAULT_PROFILE,
        help="Plot styling profile.",
    )
    parser.add_argument(
        "--executable",
        default="quantP_compare_swap_5_multiplex",
        help="Cabal executable name.",
    )
    parser.add_argument(
        "--plots-only",
        "--plots_only",
        action="store_true",
        dest="plots_only",
        help="Skip Cabal runs and regenerate CSVs/plots/report from existing JSON dumps.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing JSON dumps for completed runs.",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip the initial cabal build step.",
    )
    parser.add_argument(
        "--mark-protocol-winners",
        action="store_true",
        help=(
            "Overlay high-contrast markers on contour points for the protocol "
            "with maximal SKR: triangle arrows for sequential, diamond for "
            "doubling, star for at-last."
        ),
    )
    parser.add_argument(
        "--include-right-to-left",
        action="store_true",
        help="Also evaluate right-to-left as a baseline protocol.",
    )
    return parser.parse_args()


def log(message: str = "") -> None:
    print(message, flush=True)


def baseline_protocols(args) -> tuple[str, ...]:
    if args.include_right_to_left:
        return ("left-to-right", RIGHT_TO_LEFT_PROTOCOL, "doubling")
    return DEFAULT_BASELINE_PROTOCOLS


def evaluation_protocols(args) -> tuple[str, ...]:
    return (opt.TARGET_PROTOCOL, *baseline_protocols(args))


def progress_line(completed: int, total: int, started_at: float) -> str:
    elapsed = time.perf_counter() - started_at
    if completed <= 0:
        eta = "unknown"
    else:
        average = elapsed / completed
        eta = format_duration(average * (total - completed))
    return (
        f"[progress] points {completed}/{total}; "
        f"elapsed {format_duration(elapsed)}; eta {eta}"
    )


def validate_probability(flag: str, value: float, *, allow_zero: bool = False) -> None:
    lower_ok = value >= 0 if allow_zero else value > 0
    if not lower_ok or value > 1:
        interval = "[0, 1]" if allow_zero else "(0, 1]"
        raise SystemExit(f"{flag} must be in the interval {interval}.")


def values_contain(values: tuple[float | int, ...], value: float | int) -> bool:
    if isinstance(value, int):
        return value in values
    return any(math.isclose(float(candidate), value, rel_tol=0.0, abs_tol=1e-15) for candidate in values)


def validate_args(args) -> None:
    if not args.plots_only and args.coverage is None and args.truncation is None:
        args.coverage = DEFAULT_COVERAGE
    if args.coverage is not None:
        validate_probability("--coverage", args.coverage)
    if args.truncation is not None and args.truncation < 0:
        raise SystemExit("--truncation must be non-negative.")
    validate_probability("--p-ge", args.fixed_p_gen)
    if args.fixed_multiplexing <= 0:
        raise SystemExit("--multiplexing must be positive.")
    if args.fixed_edge_skew < 1:
        raise SystemExit("--edge-skew must be at least 1.")
    if args.t_coh <= 0:
        raise SystemExit("--t-coh must be positive.")
    validate_probability("--p-swap", args.p_swap, allow_zero=True)
    validate_probability("--w0", args.w0, allow_zero=True)
    for value in axis_values("p-gen", args):
        validate_probability("--p-ge-values", value)
    for value in axis_values("edge-skew", args):
        if value < 1:
            raise SystemExit("--edge-skew-values entries must be at least 1.")
    for value in axis_values("multiplexing", args):
        if value <= 0:
            raise SystemExit("--multiplexing-values entries must be positive.")
    for value in axis_values("t-coh", args):
        if value <= 0:
            raise SystemExit("--t-coh-values entries must be positive.")
    for value in axis_values("p-swap", args):
        validate_probability("--p-swap-values", value, allow_zero=True)
    for value in axis_values("w0", args):
        validate_probability("--w0-values", value, allow_zero=True)
    fixed_values = fixed_axis_values(args)
    for axis, value in fixed_values.items():
        values = axis_values(axis, args)
        if not values_contain(values, value):
            raise SystemExit(
                f"The fixed slice value for {axis_label_plain(axis)} is {value}, "
                f"but it is not present in {axis_values_flag(axis)}. "
                "Add it to the value list or choose a fixed value from the list."
            )


def axis_values(axis: str, args) -> tuple[float | int, ...]:
    if axis == "p-gen":
        return opt.parse_float_values(args.p_gen_values, "--p-ge-values")
    if axis == "edge-skew":
        return opt.parse_float_values(args.edge_skew_values, "--edge-skew-values")
    if axis == "multiplexing":
        return opt.parse_int_values(args.multiplexing_values, "--multiplexing-values")
    if axis == "t-coh":
        return opt.parse_int_values(args.t_coh_values, "--t-coh-values")
    if axis == "p-swap":
        return opt.parse_float_values(args.p_swap_values, "--p-swap-values")
    if axis == "w0":
        return opt.parse_float_values(args.w0_values, "--w0-values")
    raise AssertionError(f"Unexpected axis: {axis}")


def fixed_axis_values(args) -> dict[str, float | int]:
    return {
        "p-gen": args.fixed_p_gen,
        "multiplexing": args.fixed_multiplexing,
        "edge-skew": args.fixed_edge_skew,
        "t-coh": args.t_coh,
        "p-swap": args.p_swap,
        "w0": args.w0,
    }


def point_from_values(values: dict[str, float | int]) -> opt.SweepPoint:
    return opt.SweepPoint(
        p_gen=float(values["p-gen"]),
        multiplexing=int(values["multiplexing"]),
        edge_skew=float(values["edge-skew"]),
        t_coh=int(values["t-coh"]),
        p_swap=float(values["p-swap"]),
        w0=float(values["w0"]),
    )


def point_for_job(
    job: ContourJob,
    x_value: float | int,
    y_value: float | int,
) -> opt.SweepPoint:
    values = dict(job.fixed_values)
    values[job.spec.x_axis] = x_value
    values[job.spec.y_axis] = y_value
    return point_from_values(values)


def default_contour_jobs(args) -> list[ContourJob]:
    jobs = []
    for x_axis, y_axis in DEFAULT_CONTOUR_PAIRS:
        x_count = len(axis_values(x_axis, args))
        y_count = len(axis_values(y_axis, args))
        if x_count < 2 or y_count < 2:
            log(
                f"[plot] skipping {axis_label_plain(x_axis)} vs "
                f"{axis_label_plain(y_axis)}: contour plots require at least "
                f"two values per axis (got {x_count} x {y_count})"
            )
            continue
        fixed_values = fixed_axis_values(args)
        caption = (
            f"{axis_label_plain(x_axis)} vs {axis_label_plain(y_axis)} "
            f"at {format_fixed_values(fixed_values, exclude={x_axis, y_axis})}"
        )
        jobs.append(
            ContourJob(
                spec=opt.ContourSpec(x_axis, y_axis),
                fixed_values=tuple(sorted(fixed_values.items())),
                caption=caption,
            )
        )
    return jobs


def explicit_contour_jobs(args) -> list[ContourJob]:
    jobs = []
    for spec in opt.parse_contours(args.contour):
        x_count = len(axis_values(spec.x_axis, args))
        y_count = len(axis_values(spec.y_axis, args))
        if x_count < 2 or y_count < 2:
            raise SystemExit(
                f"--contour {spec.x_axis},{spec.y_axis} requires at least two "
                f"values for each axis, but got {x_count} x {y_count}."
            )
        fixed_values = fixed_axis_values(args)
        caption = (
            f"{axis_label_plain(spec.x_axis)} vs {axis_label_plain(spec.y_axis)} "
            f"at {format_fixed_values(fixed_values, exclude={spec.x_axis, spec.y_axis})}"
        )
        jobs.append(
            ContourJob(
                spec=spec,
                fixed_values=tuple(sorted(fixed_values.items())),
                caption=caption,
            )
        )
    return jobs


def contour_jobs(args) -> list[ContourJob]:
    if args.contour is None:
        return default_contour_jobs(args)
    return explicit_contour_jobs(args)


def all_points(args) -> list[opt.SweepPoint]:
    points = []
    for values in itertools.product(*(axis_values(axis, args) for axis in SWEEP_AXES)):
        points.append(point_from_values(dict(zip(SWEEP_AXES, values))))
    return points


def effective_generation_probability(p: float, multiplexing: int) -> float:
    return 1.0 - (1.0 - p) ** multiplexing


def command_point_for(original: opt.SweepPoint, model: str) -> opt.SweepPoint:
    if model == DIRECT_MODEL:
        return original

    fast_p = effective_generation_probability(original.p_gen, original.multiplexing)
    slow_single_p = original.p_gen / original.edge_skew
    slow_p = effective_generation_probability(slow_single_p, original.multiplexing)
    effective_skew = fast_p / slow_p
    return replace(
        original,
        p_gen=fast_p,
        multiplexing=1,
        edge_skew=effective_skew,
    )


def effective_metadata(original: opt.SweepPoint, model: str) -> dict[str, float | int | str]:
    command_point = command_point_for(original, model)
    return {
        "model": model,
        "command_p_gen": command_point.p_gen,
        "command_multiplexing": command_point.multiplexing,
        "command_edge_skew": command_point.edge_skew,
    }


def json_path(data_dir: Path, point: opt.SweepPoint, protocol: str, mode: str, event: str) -> Path:
    return opt.output_json_path(data_dir, FILE_PREFIX, point, protocol, mode, event)


def existing_json_path(
    data_dir: Path,
    point: opt.SweepPoint,
    protocol: str,
    mode: str,
    event: str,
) -> Path:
    return opt.existing_output_json_path(
        data_dir,
        FILE_PREFIX,
        point,
        protocol,
        mode,
        event,
        allow_legacy=False,
    )


def make_runner_args(args) -> SimpleNamespace:
    return SimpleNamespace(
        executable=args.executable,
        p_swap=args.p_swap,
        w0=args.w0,
        file_prefix=FILE_PREFIX,
        coverage=args.coverage,
        truncation=args.truncation,
        plots_only=args.plots_only,
        resume=args.resume,
    )


def resolve_budget(
    original: opt.SweepPoint,
    command_point: opt.SweepPoint,
    data_dir: Path,
    args,
    runner_args: SimpleNamespace,
) -> int:
    if args.coverage is None:
        return args.truncation

    budgets = []
    for protocol in evaluation_protocols(args):
        target_path = json_path(data_dir, original, protocol, MDP_MODE, STATIC_EVENT)
        if args.plots_only:
            path = existing_json_path(data_dir, original, protocol, MDP_MODE, STATIC_EVENT)
        elif args.resume:
            try:
                path = existing_json_path(
                    data_dir,
                    original,
                    protocol,
                    MDP_MODE,
                    STATIC_EVENT,
                )
                log(
                    f"{opt.scenario_tag(original)} {protocol} coverage: "
                    f"reused {path}"
                )
            except SystemExit as exc:
                path = target_path
                log(f"{exc}; rerunning {protocol} {MDP_MODE}/{STATIC_EVENT}")
                elapsed = opt.run_extremal_case(
                    args.executable,
                    protocol,
                    MDP_MODE,
                    STATIC_EVENT,
                    command_point,
                    runner_args,
                    "--coverage",
                    args.coverage,
                    path,
                )
                log(f"{opt.scenario_tag(original)} {protocol} coverage: {elapsed:.2f}s -> {path}")
        else:
            path = target_path
            elapsed = opt.run_extremal_case(
                args.executable,
                protocol,
                MDP_MODE,
                STATIC_EVENT,
                command_point,
                runner_args,
                "--coverage",
                args.coverage,
                path,
            )
            log(f"{opt.scenario_tag(original)} {protocol} coverage: {elapsed:.2f}s -> {path}")
        resolved_budget, coverage_value = load_coverage_budget(protocol, args.coverage, path)
        budgets.append(resolved_budget)
        log(
            f"{opt.scenario_tag(original)} {protocol}: "
            f"coverage {coverage_value:.12g} at R={resolved_budget}"
        )
    return max(budgets)


def ensure_qmdp_jsons(
    original: opt.SweepPoint,
    command_point: opt.SweepPoint,
    protocol: str,
    data_dir: Path,
    args,
    runner_args: SimpleNamespace,
    budget: int,
) -> tuple[Path, Path]:
    paths = []
    for event in (PURE_EVENT, MIXED_EVENT):
        target_path = json_path(data_dir, original, protocol, QMDP_MODE, event)
        reused = False
        if args.plots_only:
            path = existing_json_path(data_dir, original, protocol, QMDP_MODE, event)
            paths.append(path)
            continue
        if args.resume:
            try:
                path = existing_json_path(data_dir, original, protocol, QMDP_MODE, event)
                reused = True
            except SystemExit as exc:
                path = target_path
                log(f"{exc}; rerunning {protocol} {QMDP_MODE}/{event}")
        else:
            path = target_path
        paths.append(path)
        if reused:
            log(f"{opt.scenario_tag(original)} {protocol} {event}: reused {path}")
            continue
        elapsed = opt.run_extremal_case(
            args.executable,
            protocol,
            QMDP_MODE,
            event,
            command_point,
            runner_args,
            "--truncation",
            budget,
            path,
        )
        log(f"{opt.scenario_tag(original)} {protocol} {event}: {elapsed:.2f}s -> {path}")
    return paths[0], paths[1]


def evaluate_point(
    point: opt.SweepPoint,
    data_dir: Path,
    args,
    runner_args: SimpleNamespace,
    *,
    point_index: int,
    point_total: int,
) -> opt.PointResult:
    command_point = command_point_for(point, args.multiplexing_model)
    budget = (
        0
        if args.plots_only
        else resolve_budget(point, command_point, data_dir, args, runner_args)
    )
    skr_by_protocol = {}
    protocols = evaluation_protocols(args)
    baselines = baseline_protocols(args)

    for protocol_index, protocol in enumerate(protocols, start=1):
        log(
            f"[progress] point {point_index}/{point_total}; "
            f"protocol {protocol_index}/{len(protocols)}: {protocol}"
        )
        pure_path, mixed_path = ensure_qmdp_jsons(
            point,
            command_point,
            protocol,
            data_dir,
            args,
            runner_args,
            budget,
        )
        skr_by_protocol[protocol] = compute_secret_key_rate_from_split(pure_path, mixed_path)
        log(f"{opt.scenario_tag(point)} {protocol}: SKR={skr_by_protocol[protocol]:.12g}")

    best_baseline_protocol = max(baselines, key=lambda name: skr_by_protocol[name])
    best_baseline_skr = skr_by_protocol[best_baseline_protocol]
    target_skr = skr_by_protocol[opt.TARGET_PROTOCOL]
    ratio = target_skr / best_baseline_skr if best_baseline_skr > 0 else math.nan
    advantage = target_skr - best_baseline_skr
    winner = max(protocols, key=lambda name: skr_by_protocol[name])
    return opt.PointResult(
        point=point,
        skr_by_protocol=skr_by_protocol,
        best_baseline_protocol=best_baseline_protocol,
        best_baseline_skr=best_baseline_skr,
        target_skr=target_skr,
        ratio=ratio,
        advantage=advantage,
        winner=winner,
    )


def write_protocol_csv(path: Path, results: dict[opt.SweepPoint, opt.PointResult]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        fieldnames = (
            "scenario",
            "p_ge",
            "multiplexing",
            "edge_skew",
            "t_coh",
            "p_swap",
            "w0",
            "protocol",
            "skr",
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results.values():
            point = result.point
            for protocol, skr in result.skr_by_protocol.items():
                writer.writerow(
                    {
                        "scenario": opt.scenario_tag(point),
                        "p_ge": f"{point.p_gen:.12g}",
                        "multiplexing": point.multiplexing,
                        "edge_skew": f"{point.edge_skew:.12g}",
                        "t_coh": point.t_coh,
                        "p_swap": f"{point.p_swap:.12g}",
                        "w0": f"{point.w0:.12g}",
                        "protocol": protocol,
                        "skr": f"{skr:.12g}",
                    }
                )


def write_points_csv(path: Path, results: dict[opt.SweepPoint, opt.PointResult], args) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        fieldnames = (
            "scenario",
            "p_ge",
            "multiplexing",
            "edge_skew",
            "t_coh",
            "p_swap",
            "w0",
            "model",
            "command_p_gen",
            "command_multiplexing",
            "command_edge_skew",
            "target_skr",
            "best_baseline_protocol",
            "best_baseline_skr",
            "ratio",
            "advantage",
            "winner",
            "at_last_is_optimal",
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results.values():
            point = result.point
            metadata = effective_metadata(point, args.multiplexing_model)
            writer.writerow(
                {
                    "scenario": opt.scenario_tag(point),
                    "p_ge": f"{point.p_gen:.12g}",
                    "multiplexing": point.multiplexing,
                    "edge_skew": f"{point.edge_skew:.12g}",
                    "t_coh": point.t_coh,
                    "p_swap": f"{point.p_swap:.12g}",
                    "w0": f"{point.w0:.12g}",
                    "model": metadata["model"],
                    "command_p_gen": f"{metadata['command_p_gen']:.12g}",
                    "command_multiplexing": metadata["command_multiplexing"],
                    "command_edge_skew": f"{metadata['command_edge_skew']:.12g}",
                    "target_skr": f"{result.target_skr:.12g}",
                    "best_baseline_protocol": result.best_baseline_protocol,
                    "best_baseline_skr": f"{result.best_baseline_skr:.12g}",
                    "ratio": f"{result.ratio:.12g}",
                    "advantage": f"{result.advantage:.12g}",
                    "winner": result.winner,
                    "at_last_is_optimal": result.winner == opt.TARGET_PROTOCOL,
                }
            )


def plot_slice(
    plt,
    figure_dir: Path,
    plot_profile_name: str,
    results: dict[opt.SweepPoint, opt.PointResult],
    *,
    job: ContourJob,
    args,
    mark_protocol_winners: bool,
) -> Path:
    from matplotlib.colors import TwoSlopeNorm

    x_axis = job.spec.x_axis
    y_axis = job.spec.y_axis
    x_values = axis_values(x_axis, args)
    y_values = axis_values(y_axis, args)
    if len(x_values) < 2 or len(y_values) < 2:
        raise SystemExit(
            f"Cannot plot contour {x_axis},{y_axis}: expected at least a 2 x 2 "
            f"grid, got {len(x_values)} x {len(y_values)}."
        )
    ratio = np.array(
        [
            [
                results[point_for_job(job, x_value, y_value)].ratio
                for x_value in x_values
            ]
            for y_value in y_values
        ],
        dtype=float,
    )
    advantage = np.array(
        [
            [
                results[point_for_job(job, x_value, y_value)].advantage
                for x_value in x_values
            ]
            for y_value in y_values
        ],
        dtype=float,
    )
    x = np.array(x_values, dtype=float)
    y = np.array(y_values, dtype=float)

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

    if x_axis in opt.LOG_AXES:
        ax.set_xscale("log")
    if y_axis in opt.LOG_AXES:
        ax.set_yscale("log")

    ax.set_xlabel(axis_label(x_axis))
    ax.set_ylabel(axis_label(y_axis))
    fixed_values = dict(job.fixed_values)
    ax.set_title(
        "At-last optimality, "
        + format_fixed_values(fixed_values, exclude={x_axis, y_axis})
    )
    ax.set_xticks(x)
    ax.set_yticks(y)
    if x_axis == "multiplexing":
        ax.set_xticklabels([str(value) for value in x_values])
    if y_axis == "multiplexing":
        ax.set_yticklabels([str(value) for value in y_values])

    if mark_protocol_winners:
        ax.margins(x=0.08, y=0.08)
        plot_protocol_winner_markers(
            ax,
            x_values,
            y_values,
            results,
            job=job,
        )

    style_axes(ax)
    fig.colorbar(
        heatmap,
        ax=ax,
        label=r"$\mathrm{SKR}_{\mathrm{at-last}} / \max \mathrm{SKR}_{\mathrm{baseline}}$",
    )
    opt.add_external_legend(ax)

    plot_profile = get_plot_profile(plot_profile_name)
    suffix = (
        f"{file_axis_name(x_axis)}_vs_{file_axis_name(y_axis)}"
        f"_{fixed_values_tag(fixed_values, exclude={x_axis, y_axis})}"
    )
    figure_path = output_path(figure_dir, FIGURE_PREFIX, suffix, plot_profile)
    save_figure(fig, figure_path)
    plt.close(fig)
    return figure_path


def plot_protocol_winner_markers(
    ax,
    x_values,
    y_values,
    results: dict[opt.SweepPoint, opt.PointResult],
    *,
    job: ContourJob,
) -> None:
    labelled_protocols = set()
    for y_value in y_values:
        for x_value in x_values:
            point = point_for_job(job, x_value, y_value)
            winner = results[point].winner
            marker = opt.PROTOCOL_MARKERS[winner]
            label = None
            if winner not in labelled_protocols:
                label = opt.PROTOCOL_MARKER_LABELS[winner]
                labelled_protocols.add(winner)
            ax.plot(
                [float(x_value)],
                [float(y_value)],
                marker=marker,
                linestyle="None",
                color="white",
                markerfacecolor="white",
                markeredgecolor="white",
                markersize=opt.WINNER_MARKER_HALO_SIZE,
                markeredgewidth=1.0,
                clip_on=False,
                zorder=5,
            )
            ax.plot(
                [float(x_value)],
                [float(y_value)],
                marker=marker,
                linestyle="None",
                color="black",
                markerfacecolor="black",
                markeredgecolor="black",
                markersize=opt.WINNER_MARKER_SIZE,
                markeredgewidth=0.8,
                label=label,
                clip_on=False,
                zorder=6,
            )


def axis_label(axis: str) -> str:
    if axis == "p-gen":
        return r"Elementary generation probability $p_{\mathrm{ge}}$"
    return opt.AXIS_LABELS[axis]


def axis_label_plain(axis: str) -> str:
    if axis == "p-gen":
        return "p_ge"
    if axis == "edge-skew":
        return "skew"
    if axis == "p-swap":
        return "p_swap"
    return axis


def file_axis_name(axis: str) -> str:
    return axis.replace("-", "_")


def axis_values_flag(axis: str) -> str:
    if axis == "p-gen":
        return "--p-ge-values"
    return f"--{axis}-values"


def format_value(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return f"{value:.6g}"


def format_fixed_values(values: dict[str, float | int], *, exclude: set[str]) -> str:
    parts = [
        f"{axis_label_plain(axis)}={format_value(value)}"
        for axis, value in sorted(values.items())
        if axis not in exclude
    ]
    return ", ".join(parts)


def fixed_values_tag(values: dict[str, float | int], *, exclude: set[str]) -> str:
    parts = [
        f"{file_axis_name(axis)}_{opt.value_tag(value)}"
        for axis, value in sorted(values.items())
        if axis not in exclude
    ]
    return "_".join(parts)


def make_figures(
    plt,
    figure_dir: Path,
    args,
    results: dict[opt.SweepPoint, opt.PointResult],
    jobs: list[ContourJob],
) -> list[tuple[str, Path]]:
    figures = []
    for job in jobs:
        path = plot_slice(
            plt,
            figure_dir,
            args.plot_profile,
            results,
            job=job,
            args=args,
            mark_protocol_winners=args.mark_protocol_winners,
        )
        figures.append((job.caption, path))
    return figures


def relative_link(from_path: Path, to_path: Path) -> str:
    return os.path.relpath(to_path, start=from_path.parent)


def report_figures(
    args,
    generated_figures: list[tuple[str, Path]],
) -> list[tuple[str, Path]]:
    captions_by_name = {path.name: caption for caption, path in generated_figures}
    figure_dir = args.output_dir / "figures"
    supported_suffixes = {".pdf", ".png", ".svg", ".jpg", ".jpeg"}
    entries = []
    if not figure_dir.is_dir():
        return entries

    for path in sorted(figure_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in supported_suffixes:
            continue
        caption = captions_by_name.get(path.name, path.name)
        entries.append((caption, path))
    return entries


def markdown_table(results: dict[opt.SweepPoint, opt.PointResult]) -> str:
    lines = [
        "| p_ge | skew | mux | t_coh | p_swap | w0 | at-last SKR | best baseline | baseline SKR | ratio | winner |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    sorted_results = sorted(
        results.values(),
        key=lambda result: (
            result.point.edge_skew,
            result.point.multiplexing,
            result.point.p_gen,
            result.point.t_coh,
            result.point.p_swap,
            result.point.w0,
        ),
    )
    for result in sorted_results:
        point = result.point
        lines.append(
            "| "
            f"{point.p_gen:.8g} | "
            f"{point.edge_skew:.8g} | "
            f"{point.multiplexing} | "
            f"{point.t_coh} | "
            f"{point.p_swap:.8g} | "
            f"{point.w0:.8g} | "
            f"{result.target_skr:.6g} | "
            f"{result.best_baseline_protocol} | "
            f"{result.best_baseline_skr:.6g} | "
            f"{result.ratio:.6g} | "
            f"{result.winner} |"
        )
    return "\n".join(lines)


def write_report(
    markdown_path: Path,
    args,
    point_csv: Path,
    protocol_csv: Path,
    figures: list[tuple[str, Path]],
    results: dict[opt.SweepPoint, opt.PointResult],
) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    if args.plots_only:
        budget_text = "plots-only (existing JSONs)"
        if args.coverage is not None or args.truncation is not None:
            budget_text += "; supplied budget ignored"
    else:
        budget_text = (
            f"coverage={args.coverage:g}"
            if args.coverage is not None
            else f"truncation={args.truncation}"
        )
    total_grid_points = math.prod(len(axis_values(axis, args)) for axis in SWEEP_AXES)
    protocols = evaluation_protocols(args)
    baselines = baseline_protocols(args)
    baseline_formula = ", ".join(f"SKR({protocol})" for protocol in baselines)
    marker_symbols = ["> left-to-right", "diamond doubling", "star at-last"]
    if args.include_right_to_left:
        marker_symbols.insert(1, "< right-to-left")
    command = " ".join(sys.argv)
    lines = [
        "# Simultaneous Swap Optimality",
        "",
        "This report is generated by `scripts.analysis.swap_comparison.run_sim_swap_analysis_suite`.",
        "It records the raw outputs needed to approve or reject the working hypotheses.",
        "No conclusion is written automatically here; inspect the CSVs and contour slices.",
        "",
        "## Configuration",
        "",
        f"- `p_ge` values: `{', '.join(f'{value:.8g}' for value in axis_values('p-gen', args))}`",
        f"- `edge_skew` values: `{', '.join(f'{value:.8g}' for value in axis_values('edge-skew', args))}`",
        f"- `multiplexing` values: `{', '.join(str(value) for value in axis_values('multiplexing', args))}`",
        f"- `t_coh` values: `{', '.join(str(value) for value in axis_values('t-coh', args))}`",
        f"- `p_swap` values: `{', '.join(f'{value:.8g}' for value in axis_values('p-swap', args))}`",
        f"- `w0` values: `{', '.join(f'{value:.8g}' for value in axis_values('w0', args))}`",
        f"- full simulation grid points: `{total_grid_points}`",
        f"- contour fixed-slice values: `{format_fixed_values(fixed_axis_values(args), exclude=set())}`",
        f"- budget: `{budget_text}`",
        f"- multiplexing model: `{args.multiplexing_model}`",
        f"- protocols: `{', '.join(protocols)}`",
        f"- protocol-winner markers: `{args.mark_protocol_winners}`",
        (
            f"- protocol-winner symbols: `{', '.join(marker_symbols)}`"
            if args.mark_protocol_winners
            else "- protocol-winner symbols: `disabled`"
        ),
        f"- command: `{command}`",
        "",
        "The plotted value is",
        "",
        "```text",
        f"SKR(at-last) / max({baseline_formula})",
        "```",
        "",
        "Values above one mean at-last is optimal against these baselines.",
        "",
        "## Multiplexing Model",
        "",
    ]
    if args.multiplexing_model == EFFECTIVE_MODEL:
        lines.extend(
            [
                "This run uses the effective-capacity-equivalent model. For an edge with",
                "single-attempt probability `p` and `m` identical attempts, the script runs",
                "one generation action with probability:",
                "",
                "```text",
                "p_eff = 1 - (1 - p)^m",
                "```",
                "",
                "For the skewed edge, the effective probability is computed from",
                "`p_ge / edge_skew`; the command-line skew passed to the executable is adjusted",
                "so both fast and slow effective probabilities match the direct multiplexed",
                "NetworkCapacity semantics.",
            ]
        )
    else:
        lines.extend(
            [
                "This run uses the direct model: the executable attempts `m` parallel",
                "`ucreate` actions per elementary edge, and `NetworkCapacity` retains at most",
                "one Bell pair per edge.",
            ]
        )

    lines.extend(
        [
            "",
            "## Empirical Insights",
            "",
            "These are trends observed in the present homogeneous (`edge_skew = 1`) grid,",
            "not general optimality proofs or sharp parameter thresholds.",
            "",
            "- `p_swap` and the effective elementary-link generation probability are the",
            "  dominant indicators of at-last optimality. Multiplexing contributes through",
            "  `p_eff = 1 - (1 - p_ge)^m`, so increasing the number of parallel attempts can",
            "  move a low single-attempt `p_ge` into the same favorable region as a larger",
            "  direct generation probability. With deterministic swaps (`p_swap = 1`) and",
            "  `p_ge` roughly at or above `0.1` (or a comparable `p_eff`), at-last is optimal",
            "  over left-to-right and doubling across a substantial part of the tested grid.",
            "",
            "- Larger `w0` and `t_coh` tend to increase the ratio",
            "  `SKR(at-last) / SKR(doubling)`, pushing the system toward at-last optimality.",
            "  This tendency is clearest when `p_swap` and the effective generation",
            "  probability are already in the favorable regime; it is not uniformly",
            "  monotone at every low-probability point.",
        ]
    )

    lines.extend(
        [
            "",
            "## Data",
            "",
            f"- Point summary CSV: [{point_csv.name}]({relative_link(markdown_path, point_csv)})",
            f"- Protocol SKR CSV: [{protocol_csv.name}]({relative_link(markdown_path, protocol_csv)})",
            "",
            "## Contour Slices",
            "",
        ]
    )
    for caption, figure_path in report_figures(args, figures):
        lines.extend(
            [
                f"### {caption}",
                "",
                f"![{caption}]({relative_link(markdown_path, figure_path)})",
                "",
            ]
        )

    lines.extend(
        [
            "## Point Summary",
            "",
            markdown_table(results),
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
            raise SystemExit(f"--plots-only requires an existing data directory: {data_dir}")
        log("--plots-only: using existing JSONs; Cabal build/run steps are skipped.")
        if args.coverage is not None or args.truncation is not None:
            log("--plots-only: budget options are ignored.")
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_build and not args.plots_only:
        run_command(
            ["cabal", "build", args.executable],
            status_label=f"cabal build {args.executable}",
        )

    jobs = contour_jobs(args)
    runner_args = make_runner_args(args)
    points = all_points(args)
    total_points = len(points)
    protocols = evaluation_protocols(args)
    started_at = time.perf_counter()
    log(
        f"[progress] starting {total_points} point(s) across "
        f"{len(jobs)} contour slice(s); {len(protocols)} protocol(s) per point: "
        f"{', '.join(protocols)}"
    )
    results = {}
    for point_index, point in enumerate(points, start=1):
        log()
        log(
            f"{progress_line(point_index - 1, total_points, started_at)}; "
            f"starting point {point_index}/{total_points}: {opt.scenario_tag(point)}"
        )
        results[point] = evaluate_point(
            point,
            data_dir,
            args,
            runner_args,
            point_index=point_index,
            point_total=total_points,
        )
        log(
            f"{progress_line(point_index, total_points, started_at)}; "
            f"completed point {point_index}/{total_points}: {opt.scenario_tag(point)}"
        )

    point_csv = args.output_dir / f"{FILE_PREFIX}_points.csv"
    protocol_csv = args.output_dir / f"{FILE_PREFIX}_protocol_skr.csv"
    write_points_csv(point_csv, results, args)
    write_protocol_csv(protocol_csv, results)
    log()
    log(f"Wrote point summary to {point_csv}")
    log(f"Wrote protocol SKRs to {protocol_csv}")

    plt = configure_matplotlib(args.plot_profile)
    figures = make_figures(plt, figure_dir, args, results, jobs)
    for caption, path in figures:
        log(f"Saved {caption} to {path}")

    write_report(args.markdown, args, point_csv, protocol_csv, figures, results)
    log(f"Wrote markdown report to {args.markdown}")


if __name__ == "__main__":
    main()
