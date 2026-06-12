import argparse
import csv
import math
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from scripts.plot.config import (
    DEFAULT_PROFILE,
    PLOT_SETTINGS,
    TIME_AXIS_LABEL,
    JOINT_PLOTS_HSPACE,
    get_plot_profile,
    output_path,
    save_figure,
)
from scripts.plot.plot_extremal import (
    configure_matplotlib,
    derive_average_werner_series,
    derive_plot_series,
    derive_pmf_series,
    load_extremal_payload,
    load_extremal_series,
    style_axes,
    werner_to_fid,
)
from scripts.utils.utils import secret_key_rate


DEFAULT_TRUNCATION = 100
MDP_MODE = "mdp"
QMDP_MODE = "qmdp"
STATIC_EVENT = "static"
PURE_EVENT = "pure"
MIXED_EVENT = "mixed"
COLORS = ("#cddb87", "#ee7833", "#01b56c", "#cc9cff", "#7c0006")

PMF_ASSERTION_TOLERANCE = 1e-4
PMF_PLOT_KIND = "pmf"
CDF_PLOT_KIND = "cdf"
BOTH_PLOT_KIND = "both"
PLOT_KINDS = (PMF_PLOT_KIND, CDF_PLOT_KIND, BOTH_PLOT_KIND)


@dataclass(frozen=True)
class ComparisonConfig:
    description: str
    default_protocols: tuple[str, ...]
    executable: str
    output_dir: str
    figure_dir: str
    file_prefix: str
    figure_prefix: str
    default_truncation: int = DEFAULT_TRUNCATION
    colors: tuple[str, ...] = COLORS
    plot_profile: str = DEFAULT_PROFILE


def parse_args(config):
    parser = argparse.ArgumentParser(description=config.description)
    parser.add_argument(
        "--protocol",
        action="append",
        choices=config.default_protocols,
        help="Protocol to run. Can be passed multiple times. Defaults to all protocols.",
    )
    budget_group = parser.add_mutually_exclusive_group()
    budget_group.add_argument(
        "--truncation",
        type=int,
        default=None,
        help=(
            "Extremal reachability budget passed to --truncation. "
            f"Defaults to {config.default_truncation} when --coverage is not used."
        ),
    )
    budget_group.add_argument(
        "--coverage",
        type=float,
        default=None,
        help=(
            "First run each selected protocol in MDP/static mode until the "
            "worst-scheduler CDF reaches this probability, then rerun the full "
            "comparison with --truncation set to the maximum resolved budget."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=config.output_dir,
        help="Directory for JSON dumps and timing summary.",
    )
    parser.add_argument(
        "--figure-dir",
        default=config.figure_dir,
        help="Directory for combined PMF/CDF and Werner figures.",
    )
    parser.add_argument(
        "--plot-kind",
        choices=PLOT_KINDS,
        default=BOTH_PLOT_KIND,
        help=(
            "Plot the combined MDP reachability figure as a PMF, CDF, or both. "
            "Use 'both' to produce both figures."
        ),
    )
    parser.add_argument(
        "--plot-profile",
        choices=tuple(PLOT_SETTINGS),
        default=config.plot_profile,
        help="Plot styling profile.",
    )
    parser.add_argument(
        "--executable",
        default=config.executable,
        help="Cabal executable name.",
    )
    parser.add_argument(
        "--validate-deterministic-extrema",
        action="store_true",
        help="Assert that min and max extremal series coincide for every run.",
    )
    parser.add_argument(
        "--validate-static-split",
        action="store_true",
        help=(
            "Assert that the MDP/static PMF equals the sum of the QMDP pure "
            "and mixed PMFs. This runs the extra MDP/static case unless "
            "--plots-only is enabled, in which case the existing static JSON "
            "is loaded from --output-dir."
        ),
    )
    parser.add_argument(
        "--plot-fidelity",
        "--plot_fidelity",
        action="store_true",
        dest="plot_fidelity",
        help="Also plot average fidelity, derived from the average Werner parameter.",
    )
    parser.add_argument(
        "--joint-plots",
        "--joint_plots",
        action="store_true",
        dest="joint_plots",
        help=(
            "Also plot PMF and Werner, or PMF and fidelity when --plot-fidelity "
            "is enabled, as two vertically stacked panels sharing the x-axis."
        ),
    )
    parser.add_argument(
        "--plots-only",
        "--plots_only",
        action="store_true",
        dest="plots_only",
        help=(
            "Skip all Cabal runs and generate plots from existing JSON dumps "
            "in --output-dir, using the selected protocols and plotting flags."
        ),
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip the initial cabal build step.",
    )
    return parser.parse_args()


def run_command(command, stdout_path=None):
    started = time.perf_counter()
    if stdout_path is None:
        result = subprocess.run(command, text=True, capture_output=True)
    else:
        with open(stdout_path, "w", encoding="utf-8") as stdout_handle:
            result = subprocess.run(
                command,
                text=True,
                stdout=stdout_handle,
                stderr=subprocess.PIPE,
            )
    elapsed = time.perf_counter() - started

    if result.returncode != 0:
        command_text = " ".join(command)
        raise SystemExit(
            f"Command failed after {elapsed:.2f}s: {command_text}\n{result.stderr}"
        )

    return elapsed


def output_json_path(output_dir, file_prefix, protocol, mode, event):
    return output_dir / f"{file_prefix}_{protocol}_{mode}_{event}.json"


def output_coverage_json_path(output_dir, file_prefix, protocol):
    return output_dir / f"{file_prefix}_{protocol}_{MDP_MODE}_{STATIC_EVENT}_coverage.json"


def existing_json_path(output_dir, file_prefix, protocol, mode, event):
    path = output_json_path(output_dir, file_prefix, protocol, mode, event)
    if not path.is_file():
        raise SystemExit(
            f"Missing existing JSON for {protocol} {mode}/{event}: {path}\n"
            "Run the comparison without --plots-only first, or adjust "
            "--output-dir/--protocol to match the available dumps."
        )
    return path


def run_extremal_case(
    executable,
    protocol,
    mode,
    event,
    budget_flag,
    budget_value,
    output_path,
):
    command = [
        "cabal",
        "run",
        "-v0",
        executable,
        "--",
        "--protocol",
        protocol,
        "--event",
        event,
        "--json",
        mode,
        "--compute-extremal",
        budget_flag,
        str(budget_value),
    ]
    elapsed = run_command(command, stdout_path=output_path)
    return output_path, elapsed


def run_case(executable, file_prefix, protocol, mode, event, truncation, output_dir):
    return run_extremal_case(
        executable,
        protocol,
        mode,
        event,
        "--truncation",
        truncation,
        output_json_path(output_dir, file_prefix, protocol, mode, event),
    )


def run_coverage_case(executable, file_prefix, protocol, coverage, output_dir):
    return run_extremal_case(
        executable,
        protocol,
        MDP_MODE,
        STATIC_EVENT,
        "--coverage",
        coverage,
        output_coverage_json_path(output_dir, file_prefix, protocol),
    )


def write_summary(summary_path, rows):
    with open(summary_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("protocol", "mode", "event", "seconds", "json_path"),
        )
        writer.writeheader()
        writer.writerows(rows)


def write_coverage_summary(summary_path, rows):
    with open(summary_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "protocol",
                "coverage",
                "resolved_budget",
                "coverage_value",
                "seconds",
                "json_path",
            ),
        )
        writer.writeheader()
        writer.writerows(rows)


def validate_truncation(truncation):
    if truncation < 0:
        raise SystemExit("--truncation must be a non-negative integer.")


def validate_coverage(coverage):
    if not 0.0 < coverage <= 1.0:
        raise SystemExit("--coverage must be a probability in the interval (0, 1].")


def validate_budget_args(args):
    if args.coverage is not None:
        validate_coverage(args.coverage)
    elif args.truncation is not None:
        validate_truncation(args.truncation)


def load_coverage_budget(protocol, coverage, json_path):
    payload = load_extremal_payload(json_path)
    if payload is None:
        raise SystemExit(f"{json_path} does not contain an extremal payload.")

    resolved_budget = payload.get("resolved_budget")
    coverage_status = payload.get("coverage_status")

    if resolved_budget is None:
        raise SystemExit(f"{json_path} does not contain resolved_budget.")
    if not isinstance(coverage_status, dict):
        raise SystemExit(f"{json_path} does not contain a coverage_status object.")

    status = coverage_status.get("status")
    coverage_value = coverage_status.get("value")
    if status != "reached":
        raise SystemExit(
            f"{protocol} did not reach coverage {coverage:g}; "
            f"status={status}, R={resolved_budget}, value={coverage_value}."
        )

    status_budget = coverage_status.get("budget")
    if status_budget is not None and int(status_budget) != int(resolved_budget):
        raise SystemExit(
            f"{protocol} coverage budget mismatch: "
            f"resolved_budget={resolved_budget}, coverage_status.budget={status_budget}."
        )

    return int(resolved_budget), coverage_value


def resolve_truncation(args, protocols, output_dir, config):
    if args.coverage is None:
        truncation = (
            args.truncation
            if args.truncation is not None
            else config.default_truncation
        )
        validate_truncation(truncation)
        return truncation

    validate_coverage(args.coverage)
    coverage_rows = []
    coverage_budgets = {}

    for protocol in protocols:
        json_path, elapsed = run_coverage_case(
            args.executable,
            config.file_prefix,
            protocol,
            args.coverage,
            output_dir,
        )
        resolved_budget, coverage_value = load_coverage_budget(
            protocol,
            args.coverage,
            json_path,
        )
        coverage_budgets[protocol] = resolved_budget
        coverage_rows.append(
            {
                "protocol": protocol,
                "coverage": f"{args.coverage:.12g}",
                "resolved_budget": resolved_budget,
                "coverage_value": f"{coverage_value:.12g}",
                "seconds": f"{elapsed:.6f}",
                "json_path": str(json_path),
            }
        )
        print(
            f"{protocol} {MDP_MODE}/{STATIC_EVENT} coverage {args.coverage:g}: "
            f"R={resolved_budget}, value={coverage_value:.12g}, "
            f"{elapsed:.2f}s -> {json_path}"
        )

    write_coverage_summary(output_dir / "coverage_budgets.csv", coverage_rows)
    truncation = max(coverage_budgets.values())
    print(f"Using max coverage budget R={truncation} for the full comparison.")
    return truncation


def assert_close_series(left, right, description):
    if len(left) != len(right):
        raise SystemExit(
            f"{description} has mismatched lengths: left={len(left)}, right={len(right)}"
        )

    for t, (left_value, right_value) in enumerate(zip(left, right)):
        if not math.isclose(
            left_value,
            right_value,
            rel_tol=0.0,
            abs_tol=PMF_ASSERTION_TOLERANCE,
        ):
            raise SystemExit(
                f"{description} differs at t={t}: "
                f"left={left_value:.17g}, right={right_value:.17g}"
            )


def is_close_series(left, right):
    if len(left) != len(right):
        return False

    return all(
        math.isclose(
            left_value,
            right_value,
            rel_tol=0.0,
            abs_tol=PMF_ASSERTION_TOLERANCE,
        )
        for left_value, right_value in zip(left, right)
    )


def assert_split_bounds(lower, value, upper, description):
    if len(lower) != len(value) or len(value) != len(upper):
        raise SystemExit(
            f"{description} has mismatched lengths: "
            f"lower={len(lower)}, value={len(value)}, upper={len(upper)}"
        )

    for t, (lower_value, value_value, upper_value) in enumerate(zip(lower, value, upper)):
        if value_value + PMF_ASSERTION_TOLERANCE < lower_value:
            raise SystemExit(
                f"{description} is below its split lower bound at t={t}: "
                f"value={value_value:.17g}, lower={lower_value:.17g}"
            )
        if value_value - PMF_ASSERTION_TOLERANCE > upper_value:
            raise SystemExit(
                f"{description} is above its split upper bound at t={t}: "
                f"value={value_value:.17g}, upper={upper_value:.17g}"
            )


def assert_extrema_coincide(protocol, mode, event, json_path):
    series = load_extremal_series(json_path)
    assert_close_series(
        series["cdf_min"],
        series["cdf_max"],
        f"{protocol} {mode}/{event} CDF min/max",
    )
    pmf_min, pmf_max = derive_pmf_series(series)
    assert_close_series(
        pmf_min,
        pmf_max,
        f"{protocol} {mode}/{event} PMF min/max",
    )


def assert_static_pmf_equals_pure_plus_mixed(protocol, static_path, pure_path, mixed_path):
    static_pmf_min, static_pmf_max = derive_pmf_series(load_extremal_series(static_path))
    pure_pmf_min, pure_pmf_max = derive_pmf_series(load_extremal_series(pure_path))
    mixed_pmf_min, mixed_pmf_max = derive_pmf_series(load_extremal_series(mixed_path))

    reconstructed_min = [
        pure_prob + mixed_prob
        for pure_prob, mixed_prob in zip(pure_pmf_min, mixed_pmf_min)
    ]
    reconstructed_max = [
        pure_prob + mixed_prob
        for pure_prob, mixed_prob in zip(pure_pmf_max, mixed_pmf_max)
    ]

    deterministic = all(
        is_close_series(pmf_min, pmf_max)
        for pmf_min, pmf_max in (
            (static_pmf_min, static_pmf_max),
            (pure_pmf_min, pure_pmf_max),
            (mixed_pmf_min, mixed_pmf_max),
        )
    )

    if deterministic:
        assert_close_series(
            static_pmf_max,
            reconstructed_max,
            f"{protocol} PMF static != pure + mixed",
        )
        return "exact"

    assert_split_bounds(
        reconstructed_min,
        static_pmf_min,
        reconstructed_max,
        f"{protocol} PMF min static",
    )
    assert_split_bounds(
        reconstructed_min,
        static_pmf_max,
        reconstructed_max,
        f"{protocol} PMF max static",
    )
    return "bounds"


def cdf_from_pmf(pmf):
    cdf = []
    total = 0.0
    for probability in pmf:
        total += probability
        cdf.append(total)
    return cdf


def sum_pmf_series(left, right, description):
    if len(left) != len(right):
        raise SystemExit(
            f"{description} has mismatched lengths: left={len(left)}, right={len(right)}"
        )

    return [
        left_probability + right_probability
        for left_probability, right_probability in zip(left, right)
    ]


def derive_full_pmf_series_from_split(pure_path, mixed_path):
    pure_pmf_min, pure_pmf_max = derive_pmf_series(load_extremal_series(pure_path))
    mixed_pmf_min, mixed_pmf_max = derive_pmf_series(load_extremal_series(mixed_path))

    full_pmf_min = sum_pmf_series(
        pure_pmf_min,
        mixed_pmf_min,
        "pure/mixed PMF min",
    )
    full_pmf_max = sum_pmf_series(
        pure_pmf_max,
        mixed_pmf_max,
        "pure/mixed PMF max",
    )
    return full_pmf_min, full_pmf_max


def derive_full_series_from_split(pure_path, mixed_path):
    full_pmf_min, full_pmf_max = derive_full_pmf_series_from_split(pure_path, mixed_path)
    return {
        "cdf_min": cdf_from_pmf(full_pmf_min),
        "cdf_max": cdf_from_pmf(full_pmf_max),
    }


def compute_secret_key_rate_from_split(pure_path, mixed_path):
    _, static_pmf = derive_full_pmf_series_from_split(pure_path, mixed_path)
    pure_series = load_extremal_series(pure_path)
    mixed_series = load_extremal_series(mixed_path)
    _, _, werner = derive_average_werner_series(pure_series, mixed_series)

    if len(static_pmf) != len(werner):
        raise SystemExit(
            "Static PMF and Werner series must have matching lengths to compute SKR."
        )

    return secret_key_rate(np.array(static_pmf), np.array(werner))


def plot_combined_reachability(plt, figure_dir, protocol_series, plot_kind, config):
    fig, ax = plt.subplots()
    plot_profile = get_plot_profile(config.plot_profile)

    for index, (protocol, series) in enumerate(protocol_series):
        if plot_kind == CDF_PLOT_KIND:
            t, _, _, _, reachability = derive_plot_series(series)
        else:
            _, reachability = derive_pmf_series(series)
            t = list(range(len(reachability)))
        color = config.colors[index % len(config.colors)]

        ax.plot(
            t,
            reachability,
            color=color,
            linestyle="-",
            label=protocol,
        )

    ax.set_xlabel(TIME_AXIS_LABEL)
    if plot_kind == CDF_PLOT_KIND:
        ax.set_ylabel("Cumulative probability")
        # ax.set_ylim(0.0, 1.0)
    else:
        ax.set_ylabel("Probability")
    style_axes(ax)
    ax.legend(frameon=False, loc="best", ncol=2)

    figure_path = output_path(figure_dir, config.figure_prefix, f"mdp_{plot_kind}s", plot_profile)
    save_figure(fig, figure_path)
    plt.close(fig)
    print(f"Saved {plot_kind.upper()}s figure to {figure_path}")


def plot_combined_quality(plt, figure_dir, protocol_paths, config, *, quality):
    fig, ax = plt.subplots()
    plot_profile = get_plot_profile(config.plot_profile)

    for index, (protocol, pure_path, mixed_path) in enumerate(protocol_paths):
        pure_series = load_extremal_series(pure_path)
        mixed_series = load_extremal_series(mixed_path)
        t, _, werner = derive_average_werner_series(
            pure_series,
            mixed_series,
        )
        color = config.colors[index % len(config.colors)]

        filtered = [
            (t_i, w)
            for t_i, w in zip(t, werner)
            if w > 0.0
        ]
        if not filtered:
            continue

        t, werner = zip(*filtered)
        if quality == "fidelity":
            values = [werner_to_fid(w) for w in werner]
        else:
            values = werner

        ax.plot(
            t,
            values,
            color=color,
            linestyle="-",
            label=protocol,
        )

    ax.set_xlabel(TIME_AXIS_LABEL)
    if quality == "fidelity":
        ax.set_ylabel(r"Fidelity")
        # ax.set_ylim(0.25, 1.0)
        suffix = "qmdp_fids"
    else:
        ax.set_ylabel(r"Werner parameter")
        ax.set_ylim(0.0, 1.0)
        suffix = "qmdp_ws"

    style_axes(ax)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(frameon=False, loc="best", ncol=2)

    figure_path = output_path(figure_dir, config.figure_prefix, suffix, plot_profile)
    save_figure(fig, figure_path)
    plt.close(fig)
    print(f"Saved {quality} figure to {figure_path}")


def plot_joint_pmf_quality(
    plt,
    figure_dir,
    reachability_protocol_series,
    protocol_paths,
    config,
    *,
    quality,
):
    plot_profile = get_plot_profile(config.plot_profile)
    width, height = plot_profile.figure_size
    fig, (pmf_ax, quality_ax) = plt.subplots(
        2,
        1,
        sharex=True,
        figsize=(width, height * 1.55),
        gridspec_kw={"height_ratios": (1.0, 1.0), "hspace": JOINT_PLOTS_HSPACE},
    )

    for index, (protocol, series) in enumerate(reachability_protocol_series):
        _, pmf = derive_pmf_series(series)
        t = list(range(len(pmf)))
        color = config.colors[index % len(config.colors)]
        pmf_ax.plot(
            t,
            pmf,
            color=color,
            linestyle="-",
            label=protocol,
        )

    for index, (protocol, pure_path, mixed_path) in enumerate(protocol_paths):
        pure_series = load_extremal_series(pure_path)
        mixed_series = load_extremal_series(mixed_path)
        t, _, werner = derive_average_werner_series(
            pure_series,
            mixed_series,
        )
        color = config.colors[index % len(config.colors)]

        filtered = [
            (t_i, w)
            for t_i, w in zip(t, werner)
            if w > 0.0
        ]
        if not filtered:
            continue

        t, werner = zip(*filtered)
        if quality == "fidelity":
            values = [werner_to_fid(w) for w in werner]
        else:
            values = werner

        quality_ax.plot(
            t,
            values,
            color=color,
            linestyle="-",
            label=protocol,
        )

    pmf_ax.set_ylabel("Probability")
    if quality == "fidelity":
        quality_ax.set_ylabel(r"Fidelity")
        suffix = "pmfs_fids"
    else:
        quality_ax.set_ylabel(r"Werner parameter")
        quality_ax.set_ylim(0.0, 1.0)
        suffix = "pmfs_ws"
    quality_ax.set_xlabel(TIME_AXIS_LABEL)

    style_axes(pmf_ax)
    style_axes(quality_ax)
    if pmf_ax.get_legend_handles_labels()[0]:
        pmf_ax.legend(frameon=False, loc="best", ncol=2)

    fig.align_ylabels((pmf_ax, quality_ax))
    figure_path = output_path(figure_dir, config.figure_prefix, suffix, plot_profile)
    save_figure(fig, figure_path, tight_layout=False)
    plt.close(fig)
    print(f"Saved joint PMF/{quality} figure to {figure_path}")


def run_comparison(config):
    args = parse_args(config)
    validate_budget_args(args)

    if not args.no_build and not args.plots_only:
        run_command(["cabal", "build", args.executable])

    protocols = args.protocol or list(config.default_protocols)
    output_dir = Path(args.output_dir)
    figure_dir = Path(args.figure_dir)
    if args.plots_only:
        if not output_dir.is_dir():
            raise SystemExit(f"--plots-only requires an existing --output-dir: {output_dir}")
        if args.coverage is not None or args.truncation is not None:
            print("--plots-only: using existing JSONs; budget options are ignored.")
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)
    config = replace(config, plot_profile=args.plot_profile)
    plt = configure_matplotlib(args.plot_profile)
    truncation = None
    if not args.plots_only:
        truncation = resolve_truncation(args, protocols, output_dir, config)

    rows = []
    reachability_protocol_series = []
    werner_protocol_paths = []
    skr_rows = []

    for protocol in protocols:
        if args.plots_only:
            pure_path = existing_json_path(
                output_dir,
                config.file_prefix,
                protocol,
                QMDP_MODE,
                PURE_EVENT,
            )
            mixed_path = existing_json_path(
                output_dir,
                config.file_prefix,
                protocol,
                QMDP_MODE,
                MIXED_EVENT,
            )
        else:
            pure_path, pure_elapsed = run_case(
                args.executable,
                config.file_prefix,
                protocol,
                QMDP_MODE,
                PURE_EVENT,
                truncation,
                output_dir,
            )
            mixed_path, mixed_elapsed = run_case(
                args.executable,
                config.file_prefix,
                protocol,
                QMDP_MODE,
                MIXED_EVENT,
                truncation,
                output_dir,
            )

        if args.validate_deterministic_extrema:
            assert_extrema_coincide(protocol, QMDP_MODE, PURE_EVENT, pure_path)
            assert_extrema_coincide(protocol, QMDP_MODE, MIXED_EVENT, mixed_path)
            print(f"{protocol}: QMDP pure/mixed min/max extrema coincide")

        if args.validate_static_split:
            if args.plots_only:
                mdp_static_path = existing_json_path(
                    output_dir,
                    config.file_prefix,
                    protocol,
                    MDP_MODE,
                    STATIC_EVENT,
                )
            else:
                mdp_static_path, mdp_static_elapsed = run_case(
                    args.executable,
                    config.file_prefix,
                    protocol,
                    MDP_MODE,
                    STATIC_EVENT,
                    truncation,
                    output_dir,
                )
            if args.validate_deterministic_extrema:
                assert_extrema_coincide(protocol, MDP_MODE, STATIC_EVENT, mdp_static_path)
            split_check = assert_static_pmf_equals_pure_plus_mixed(
                protocol,
                mdp_static_path,
                pure_path,
                mixed_path,
            )
            if args.plots_only:
                print(f"{protocol} {MDP_MODE}/{STATIC_EVENT}: loaded {mdp_static_path}")
            else:
                rows.append(
                    {
                        "protocol": protocol,
                        "mode": MDP_MODE,
                        "event": STATIC_EVENT,
                        "seconds": f"{mdp_static_elapsed:.6f}",
                        "json_path": str(mdp_static_path),
                    }
                )
                print(
                    f"{protocol} {MDP_MODE}/{STATIC_EVENT}: "
                    f"{mdp_static_elapsed:.2f}s -> {mdp_static_path}"
                )
            if split_check == "exact":
                print(f"{protocol}: static PMF = pure PMF + mixed PMF")
            else:
                print(f"{protocol}: static PMF lies within pure/mixed split bounds")

        full_reachability_series = derive_full_series_from_split(pure_path, mixed_path)
        skr = compute_secret_key_rate_from_split(pure_path, mixed_path)

        reachability_protocol_series.append((protocol, full_reachability_series))
        werner_protocol_paths.append((protocol, pure_path, mixed_path))
        skr_rows.append({"protocol": protocol, "skr": skr})
        if args.plots_only:
            print(f"{protocol} {QMDP_MODE}/{PURE_EVENT}: loaded {pure_path}")
            print(f"{protocol} {QMDP_MODE}/{MIXED_EVENT}: loaded {mixed_path}")
        else:
            rows.extend(
                [
                    {
                        "protocol": protocol,
                        "mode": QMDP_MODE,
                        "event": PURE_EVENT,
                        "seconds": f"{pure_elapsed:.6f}",
                        "json_path": str(pure_path),
                    },
                    {
                        "protocol": protocol,
                        "mode": QMDP_MODE,
                        "event": MIXED_EVENT,
                        "seconds": f"{mixed_elapsed:.6f}",
                        "json_path": str(mixed_path),
                    },
                ]
            )
            print(f"{protocol} {QMDP_MODE}/{PURE_EVENT}: {pure_elapsed:.2f}s -> {pure_path}")
            print(f"{protocol} {QMDP_MODE}/{MIXED_EVENT}: {mixed_elapsed:.2f}s -> {mixed_path}")

    if args.plots_only:
        print("--plots-only: timings.csv was not rewritten.")
    else:
        write_summary(output_dir / "timings.csv", rows)

    if args.plot_kind == BOTH_PLOT_KIND:
        plot_combined_reachability(
            plt,
            figure_dir,
            reachability_protocol_series,
            PMF_PLOT_KIND,
            config,
        )
        plot_combined_reachability(
            plt,
            figure_dir,
            reachability_protocol_series,
            CDF_PLOT_KIND,
            config,
        )
    else:
        plot_combined_reachability(
            plt,
            figure_dir,
            reachability_protocol_series,
            args.plot_kind,
            config,
        )

    plot_combined_quality(plt, figure_dir, werner_protocol_paths, config, quality="werner")
    if args.plot_fidelity:
        plot_combined_quality(plt, figure_dir, werner_protocol_paths, config, quality="fidelity")
    if args.joint_plots:
        joint_quality = "fidelity" if args.plot_fidelity else "werner"
        plot_joint_pmf_quality(
            plt,
            figure_dir,
            reachability_protocol_series,
            werner_protocol_paths,
            config,
            quality=joint_quality,
        )

    print("\nSecret key rates:")
    print(f"{'Protocol':<15} {'SKR':<18}")
    print("-" * 33)
    for row in skr_rows:
        print(f"{row['protocol']:<15} {row['skr']:<18.12g}")
