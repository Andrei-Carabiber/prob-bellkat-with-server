from __future__ import annotations

import csv
import math
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.lines import Line2D

from scripts.analysis.swap_comparison.common import (
    COLORS,
    build_command,
    executable_command,
    run_command,
)
from scripts.plot.config import (
    TIME_AXIS_LABEL,
    VALIDATION_COMBINED_HEIGHT_INCHES,
    VALIDATION_COMBINED_LINE_WIDTH_INCHES,
    VALIDATION_HEIGHT_INCHES,
    VALIDATION_LINE_WIDTH_INCHES,
    configure_matplotlib,
    get_plot_profile,
    output_path,
    save_figure,
    style_axes,
)
from scripts.plot.plot_extremal import (
    derive_average_werner_series,
    derive_pmf_series,
    load_extremal_series,
)


PROTOCOLS = ("swap-asap", "doubling", "left-to-right", "right-to-left")
GOODENOUGH_SEGMENTS = 4

PROTOCOL_COLORS = {
    "swap-asap": COLORS[0],
    "left-to-right": COLORS[1],
    "right-to-left": COLORS[1],
    "doubling": COLORS[2],
}

REFERENCE_COLORS = {
    "doubling": "#00B8D9",
    "left-to-right": "#003B73",
    "right-to-left": "#003B73",
}
QBKAT_PMF_LINESTYLE = ":"
REFERENCE_PMF_LINESTYLE = "-."
QBKAT_WERNER_LINESTYLE = "-"
REFERENCE_WERNER_LINESTYLE = "--"
REFERENCE_LINEWIDTH = 2.4
PROTOCOL_LABELS = {
    "left-to-right": "sequential",
    "right-to-left": "sequential",
}
REFERENCE_LABELS = {
    "doubling": r"Li \textit{et al.}",
    "left-to-right": r"La Corte \textit{et al.}",
    "right-to-left": r"La Corte \textit{et al.}",
}
PMF_KEYS = ("pmf", "delivery_pmf", "probabilities", "probability")
CDF_KEYS = ("cdf", "delivery_cdf")
WERNER_KEYS = ("werner", "w_out", "lambda", "lambda_series", "average_werner")
FINAL_LAMBDA_KEYS = ("e_lambda", "E_Lambda", "average_lambda", "final_lambda")


@dataclass(frozen=True)
class ProtocolSeries:
    protocol: str
    pure_path: Path
    mixed_path: Path
    pmf: np.ndarray
    cdf: np.ndarray
    werner: np.ndarray
    pure_mass: float
    total_mass: float
    tail: float
    extrema_gap: float


@dataclass(frozen=True)
class ReferenceSeries:
    protocol: str
    source: Path
    pmf: np.ndarray | None = None
    cdf: np.ndarray | None = None
    werner: np.ndarray | None = None
    final_lambda: float | None = None


def memory_lambda(t_coh: int) -> float:
    return math.exp(-1.0 / t_coh)


def goodenough_lambda_n(segments: int, p_gen: float, lambda_memory: float) -> float:
    """Appendix D.1 recursion for E[Lambda_n] without a cut-off policy.

    The paper's n counts elementary segments. A 5-node line has four elementary
    segments, so the 5-node swap-ASAP oracle is E[Lambda_4].
    """
    if segments < 1:
        raise ValueError("segments must be positive")
    if not 0.0 < p_gen < 1.0:
        raise ValueError("p_gen must be in (0, 1)")

    q = 1.0 - p_gen

    def c_coeff(a: int, b: int) -> float:
        numerator = -(q**a) * (lambda_memory**b) * (1.0 / lambda_memory - lambda_memory)
        denominator = (
            (1.0 - (lambda_memory ** (b - 1)) * (q**a))
            * (1.0 - (lambda_memory ** (b + 1)) * (q**a))
        )
        return numerator / denominator

    def d_coeff(a: int, b: int) -> float:
        return (q**a) / ((lambda_memory ** (1 - b)) - (q**a))

    terms: dict[tuple[int, int], float] = {(1, 0): 1.0}
    for _ in range(segments - 1):
        next_terms: dict[tuple[int, int], float] = {}
        for (a, b), coefficient in terms.items():
            next_terms[(a + 1, b)] = (
                next_terms.get((a + 1, b), 0.0)
                + coefficient * c_coeff(a, b)
            )
            next_terms[(1, 1)] = (
                next_terms.get((1, 1), 0.0)
                + coefficient * d_coeff(a, b)
            )
        terms = next_terms

    z_n = sum(
        coefficient
        * ((q**a) * (lambda_memory**b))
        / (1.0 - (q**a) * (lambda_memory**b))
        for (a, b), coefficient in terms.items()
    )
    return ((1.0 - q) / q) ** segments * z_n


def goodenough_lambda_4(p_gen: float, t_coh: int) -> float:
    return goodenough_lambda_n(GOODENOUGH_SEGMENTS, p_gen, memory_lambda(t_coh))


def protocol_tag(protocol: str) -> str:
    return protocol.replace("-", "_")


def output_json_path(output_dir: Path, file_prefix: str, protocol: str, event: str) -> Path:
    return output_dir / f"{file_prefix}_{protocol_tag(protocol)}_qmdp_{event}.json"


def run_qmdp_case(args: Any, protocol: str, event: str, output_dir: Path) -> Path:
    path = output_json_path(output_dir, args.file_prefix, protocol, event)
    if args.reuse_existing and path.is_file():
        print(f"{protocol} qmdp/{event}: reusing {path}")
        return path

    command = [
        *executable_command(args.executable),
        "--paper-assumptions",
        "--protocol",
        protocol,
        "--event",
        event,
        "--p-gen",
        f"{args.p_gen:.17g}",
        "--p-swap",
        "1",
        "--w0",
        "1",
        "--t-coh",
        str(args.t_coh),
        "--json",
        "qmdp",
        "--compute-extremal",
        "--truncation",
        str(args.truncation),
    ]
    run_command(command, stdout_path=path, status_label=f"{protocol} qmdp/{event}")
    return path


def run_protocol_outputs(args: Any, protocols: tuple[str, ...], output_dir: Path) -> None:
    if not args.no_build:
        command = build_command(args.executable)
        if command is not None:
            run_command(command, status_label=f"cabal build {args.executable}")

    output_dir.mkdir(parents=True, exist_ok=True)
    for protocol in protocols:
        run_qmdp_case(args, protocol, "pure", output_dir)
        run_qmdp_case(args, protocol, "mixed", output_dir)


def load_protocol_series(output_dir: Path, file_prefix: str, protocol: str) -> ProtocolSeries:
    pure_path = output_json_path(output_dir, file_prefix, protocol, "pure")
    mixed_path = output_json_path(output_dir, file_prefix, protocol, "mixed")
    pure_series = load_extremal_series(pure_path)
    mixed_series = load_extremal_series(mixed_path)

    pure_min, pure_max = derive_pmf_series(pure_series)
    mixed_min, mixed_max = derive_pmf_series(mixed_series)
    if len(pure_min) != len(mixed_min):
        raise SystemExit(f"{protocol}: pure/mixed PMFs have different lengths.")

    pure_pmf = np.array(pure_max, dtype=float)
    mixed_pmf = np.array(mixed_max, dtype=float)
    pmf = pure_pmf + mixed_pmf
    cdf = np.cumsum(pmf)
    _, werner_min, werner_max = derive_average_werner_series(pure_series, mixed_series)
    werner = np.array(werner_max, dtype=float)
    extrema_gap = max(
        max_abs_diff(pure_min, pure_max),
        max_abs_diff(mixed_min, mixed_max),
        max_abs_diff(werner_min, werner_max),
    )

    total_mass = float(np.sum(pmf))
    pure_mass = float(np.sum(pure_pmf))
    return ProtocolSeries(
        protocol=protocol,
        pure_path=pure_path,
        mixed_path=mixed_path,
        pmf=pmf,
        cdf=cdf,
        werner=werner,
        pure_mass=pure_mass,
        total_mass=total_mass,
        tail=max(0.0, 1.0 - total_mass),
        extrema_gap=extrema_gap,
    )


def max_abs_diff(left: Any, right: Any) -> float:
    left_array = np.array(left, dtype=float)
    right_array = np.array(right, dtype=float)
    if left_array.shape != right_array.shape:
        return math.inf
    if left_array.size == 0:
        return 0.0
    return float(np.max(np.abs(left_array - right_array)))


def pick_sequence(mapping: dict[str, Any], keys: tuple[str, ...]) -> np.ndarray | None:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return np.array(mapping[key], dtype=float)
    return None


def pick_float(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return float(mapping[key])
    return None


def normalize_reference_entry(protocol: str, source: Path, entry: Any) -> ReferenceSeries:
    if not isinstance(entry, dict):
        raise SystemExit(
            f"{source}: reference entry for {protocol!r} must be a dict. "
            "Expected keys include pmf, cdf, werner, and/or e_lambda."
        )

    pmf = pick_sequence(entry, PMF_KEYS)
    cdf = pick_sequence(entry, CDF_KEYS)
    if pmf is None and cdf is not None:
        pmf = np.diff(np.insert(cdf, 0, 0.0))
    if cdf is None and pmf is not None:
        cdf = np.cumsum(pmf)

    return ReferenceSeries(
        protocol=protocol,
        source=source,
        pmf=pmf,
        cdf=cdf,
        werner=pick_sequence(entry, WERNER_KEYS),
        final_lambda=pick_float(entry, FINAL_LAMBDA_KEYS),
    )


def load_reference_pickle(
    path: Path | None,
    protocols: tuple[str, ...],
    *,
    quiet_missing: bool = False,
) -> dict[str, ReferenceSeries]:
    if path is None:
        return {}
    if not path.exists():
        if not quiet_missing:
            print_reference_template(path, protocols)
        return {}

    with open(path, "rb") as handle:
        payload = pickle.load(handle)

    if isinstance(payload, dict) and isinstance(payload.get("protocols"), dict):
        payload = payload["protocols"]

    references: dict[str, ReferenceSeries] = {}
    if isinstance(payload, dict):
        if "protocol" in payload:
            protocol = str(payload["protocol"])
            references[protocol] = normalize_reference_entry(protocol, path, payload)
        else:
            for protocol in protocols:
                if protocol in payload:
                    references[protocol] = normalize_reference_entry(protocol, path, payload[protocol])
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, dict) and "protocol" in item:
                protocol = str(item["protocol"])
                if protocol in protocols:
                    references[protocol] = normalize_reference_entry(protocol, path, item)
    else:
        raise SystemExit(f"{path}: unsupported reference pickle shape {type(payload).__name__}.")

    if references:
        loaded = ", ".join(sorted(references))
        print(f"Loaded reference data for {loaded} from {path}")
    else:
        print_reference_template(path, protocols)
    return references


def load_reference_pair_pickles(
    pmf_path: Path,
    werner_path: Path,
    protocols: tuple[str, ...],
) -> dict[str, ReferenceSeries]:
    if not pmf_path.exists() or not werner_path.exists():
        missing = [
            str(path)
            for path in (pmf_path, werner_path)
            if not path.exists()
        ]
        print(
            "Reference PMF/Werner pickle pair missing: "
            + ", ".join(missing)
            + ". Missing references are skipped."
        )
        return {}

    pmf = load_array_pickle(pmf_path)
    werner = load_array_pickle(werner_path)
    cdf = np.cumsum(pmf)
    references = {
        protocol: ReferenceSeries(
            protocol=protocol,
            source=pmf_path,
            pmf=pmf,
            cdf=cdf,
            werner=werner,
        )
        for protocol in protocols
    }
    loaded = ", ".join(protocols)
    print(f"Loaded PMF/Werner reference data for {loaded} from {pmf_path} and {werner_path}")
    return references


def load_array_pickle(path: Path) -> np.ndarray:
    with open(path, "rb") as handle:
        payload = pickle.load(handle)
    return np.array(payload, dtype=float)


def print_reference_template(path: Path, protocols: tuple[str, ...]) -> None:
    expected = ", ".join(protocols)
    print(
        f"Reference pickle not found or did not contain expected protocols at {path}.\n"
        f"Template shape: {{protocol: {{'pmf': [...], 'werner': [...]}}}} for {expected}.\n"
        "Optional keys: cdf, e_lambda/final_lambda. Missing references are skipped."
    )


def compare_reference(
    series: ProtocolSeries,
    reference: ReferenceSeries,
    *,
    atol: float,
) -> dict[str, float | str]:
    row: dict[str, float | str] = {
        "protocol": series.protocol,
        "reference_source": str(reference.source),
    }
    if reference.pmf is not None:
        row["pmf_max_abs_diff"] = compare_arrays(
            series.pmf,
            reference.pmf,
            f"{series.protocol} PMF",
            atol,
        )
    if reference.cdf is not None:
        row["cdf_max_abs_diff"] = compare_arrays(
            series.cdf,
            reference.cdf,
            f"{series.protocol} CDF",
            atol,
        )
    if reference.werner is not None:
        row["werner_max_abs_diff"] = compare_arrays(
            series.werner,
            reference.werner,
            f"{series.protocol} Werner",
            atol,
        )
    if reference.final_lambda is not None:
        diff = series.pure_mass - reference.final_lambda
        if abs(diff) > atol:
            raise SystemExit(
                f"{series.protocol} final Lambda differs from reference by {diff:.12e}; "
                f"tolerance is {atol:.12e}."
            )
        row["final_lambda_diff"] = diff
    return row


def compare_arrays(model: np.ndarray, reference: np.ndarray, label: str, atol: float) -> float:
    common = min(len(model), len(reference))
    if common == 0:
        raise SystemExit(f"{label}: cannot compare empty arrays.")

    diff = float(np.max(np.abs(model[:common] - reference[:common])))
    if diff > atol:
        raise SystemExit(
            f"{label} max abs diff {diff:.12e} exceeds tolerance {atol:.12e}."
        )
    if len(model) != len(reference):
        print(
            f"{label}: compared first {common} points only "
            f"(model={len(model)}, reference={len(reference)})."
        )
    return diff


def validate_swap_asap(
    series: ProtocolSeries,
    *,
    p_gen: float,
    t_coh: int,
    atol: float,
    tail_tolerance: float,
    strict: bool,
) -> dict[str, float | str]:
    oracle = goodenough_lambda_4(p_gen, t_coh)
    diff = series.pure_mass - oracle
    tail_exhausted = series.tail <= tail_tolerance
    if strict and not tail_exhausted:
        raise SystemExit(
            f"swap-asap static tail {series.tail:.12e} exceeds tolerance "
            f"{tail_tolerance:.12e}; increase --truncation."
        )
    if (strict or tail_exhausted) and abs(diff) > atol:
        raise SystemExit(
            f"swap-asap E[Lambda_4] mismatch: model={series.pure_mass:.12e}, "
            f"Goodenough={oracle:.12e}, diff={diff:.12e}, tolerance={atol:.12e}."
        )
    return {
        "protocol": series.protocol,
        "reference_source": "Goodenough Appendix D.1 recursion, n=4",
        "goodenough_status": "matched" if tail_exhausted else "truncated-tail",
        "model_pure_mass": series.pure_mass,
        "reference_lambda": oracle,
        "final_lambda_diff": diff,
    }


def write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)

    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def plot_validation(
    plt: Any,
    figure_dir: Path,
    file_prefix: str,
    plot_profile_name: str,
    series_by_protocol: dict[str, ProtocolSeries],
    references: dict[str, ReferenceSeries],
    swap_asap_oracle: float,
    *,
    skip_werner_legend: bool = False,
) -> dict[str, Path]:
    plot_profile = get_plot_profile(plot_profile_name)
    fig, pmf_ax = plt.subplots(
        figsize=(
            VALIDATION_COMBINED_LINE_WIDTH_INCHES,
            VALIDATION_COMBINED_HEIGHT_INCHES,
        )
    )
    werner_ax = pmf_ax.twinx()
    protocols = plot_combined_validation_curves(
        pmf_ax,
        werner_ax,
        series_by_protocol,
        references,
    )

    pmf_ax.set_ylabel("Probability")
    werner_ax.set_ylabel("Werner parameter")
    pmf_ax.set_xlabel(TIME_AXIS_LABEL)
    style_axes(pmf_ax)
    style_axes(werner_ax)
    werner_ax.grid(False)
    add_combined_legends(pmf_ax, protocols)

    figure_dir.mkdir(parents=True, exist_ok=True)
    combined_path = output_path(figure_dir, file_prefix, "validation", plot_profile)
    save_figure(fig, combined_path, tight_layout=True, bbox_inches=None)
    plt.close(fig)

    pmf_path = plot_pmf_validation(
        plt,
        figure_dir,
        file_prefix,
        plot_profile,
        series_by_protocol,
        references,
    )
    werner_path = plot_werner_validation(
        plt,
        figure_dir,
        file_prefix,
        plot_profile,
        series_by_protocol,
        references,
        skip_legend=skip_werner_legend,
    )
    return {
        "combined": combined_path,
        "pmf": pmf_path,
        "werner": werner_path,
    }


def plotted_protocols(series_by_protocol: dict[str, ProtocolSeries]) -> tuple[str, ...]:
    protocols = [
        protocol
        for protocol in ("swap-asap", "doubling")
        if protocol in series_by_protocol
    ]
    sequential = next(
        (
            protocol
            for protocol in ("left-to-right", "right-to-left")
            if protocol in series_by_protocol
        ),
        None,
    )
    if sequential is not None:
        protocols.append(sequential)
    return tuple(protocols)


def plot_combined_validation_curves(
    pmf_ax: Any,
    werner_ax: Any,
    series_by_protocol: dict[str, ProtocolSeries],
    references: dict[str, ReferenceSeries],
) -> tuple[str, ...]:
    protocols = plotted_protocols(series_by_protocol)
    for protocol in protocols:
        color = PROTOCOL_COLORS[protocol]
        series = series_by_protocol[protocol]
        pmf_ax.plot(
            np.arange(len(series.pmf)),
            series.pmf,
            color=color,
            linestyle=QBKAT_PMF_LINESTYLE,
        )
        werner_ax.plot(
            np.arange(1, len(series.werner)),
            series.werner[1:],
            color=color,
            linestyle=QBKAT_WERNER_LINESTYLE,
        )

    # References are drawn last so they remain visible over the nearly
    # identical QBKAT curves.
    for protocol in protocols:
        reference = references.get(protocol)
        if reference is None:
            continue
        color = PROTOCOL_COLORS[protocol]
        if reference.pmf is not None:
            pmf_ax.plot(
                np.arange(len(reference.pmf)),
                reference.pmf,
                color=color,
                linestyle=REFERENCE_PMF_LINESTYLE,
                linewidth=REFERENCE_LINEWIDTH,
            )
        if reference.werner is not None:
            werner_ax.plot(
                np.arange(1, len(reference.werner)),
                reference.werner[1:],
                color=color,
                linestyle=REFERENCE_WERNER_LINESTYLE,
                linewidth=REFERENCE_LINEWIDTH,
            )
    return protocols


def add_combined_legends(ax: Any, protocols: tuple[str, ...]) -> None:
    series_handles = (
        Line2D([], [], color="black", linestyle=QBKAT_PMF_LINESTYLE, label="QBKAT PMF"),
        Line2D(
            [],
            [],
            color="black",
            linestyle=REFERENCE_PMF_LINESTYLE,
            linewidth=REFERENCE_LINEWIDTH,
            label="Reference PMF",
        ),
        Line2D([], [], color="black", linestyle=QBKAT_WERNER_LINESTYLE, label="QBKAT Werner"),
        Line2D(
            [],
            [],
            color="black",
            linestyle=REFERENCE_WERNER_LINESTYLE,
            linewidth=REFERENCE_LINEWIDTH,
            label="Reference Werner",
        ),
    )
    scheme_handles = tuple(
        Line2D(
            [],
            [],
            color=PROTOCOL_COLORS[protocol],
            linestyle="-",
            label=PROTOCOL_LABELS.get(protocol, protocol),
        )
        for protocol in protocols
    )

    scheme_legend = ax.legend(
        handles=scheme_handles,
        title="Color",
        loc="lower left",
        bbox_to_anchor=(0.0, 0.98),
        ncol=1,
        columnspacing=1.0,
        labelspacing=0.35,
        borderaxespad=0.0,
    )
    ax.add_artist(scheme_legend)
    ax.legend(
        handles=series_handles,
        title="Line style",
        loc="lower right",
        bbox_to_anchor=(1.0, 0.98),
        ncol=1,
        columnspacing=1.0,
        labelspacing=0.35,
        borderaxespad=0.0,
    )


def plot_pmf_curves(
    ax: Any,
    series_by_protocol: dict[str, ProtocolSeries],
    references: dict[str, ReferenceSeries],
) -> None:
    protocols = plotted_protocols(series_by_protocol)
    for protocol in protocols:
        color = PROTOCOL_COLORS.get(protocol)
        series = series_by_protocol[protocol]

        ax.plot(
            np.arange(len(series.pmf)),
            series.pmf,
            label=PROTOCOL_LABELS.get(protocol, protocol),
            color=color,
        )

    # Draw references last and in a contrasting color. Their values nearly
    # coincide with QBKAT, so a same-color overlay would hide the dash pattern.
    for protocol in protocols:
        reference = references.get(protocol)
        if reference is not None and reference.pmf is not None:
            ax.plot(
                np.arange(len(reference.pmf)),
                reference.pmf,
                color=REFERENCE_COLORS[protocol],
                linestyle="--",
                label=REFERENCE_LABELS.get(protocol, r"reference"),
            )



def plot_werner_curves(
    ax: Any,
    series_by_protocol: dict[str, ProtocolSeries],
    references: dict[str, ReferenceSeries],
) -> None:
    protocols = plotted_protocols(series_by_protocol)
    for protocol in protocols:
        color = PROTOCOL_COLORS.get(protocol)
        series = series_by_protocol[protocol]
        ax.plot(
            np.arange(1, len(series.werner)),
            series.werner[1:],
            label=PROTOCOL_LABELS.get(protocol, protocol),
            color=color,
        )

    # Draw references last and in a contrasting color. Their values nearly
    # coincide with QBKAT, so a same-color overlay would hide the dash pattern.
    for protocol in protocols:
        reference = references.get(protocol)
        if reference is not None and reference.werner is not None:
            ax.plot(
                np.arange(1, len(reference.werner)),
                reference.werner[1:],
                color=REFERENCE_COLORS[protocol],
                linestyle="--",
                label=REFERENCE_LABELS.get(protocol, r"reference"),
            )


def plot_pmf_validation(
    plt: Any,
    figure_dir: Path,
    file_prefix: str,
    plot_profile: Any,
    series_by_protocol: dict[str, ProtocolSeries],
    references: dict[str, ReferenceSeries],
) -> Path:
    fig, ax = plt.subplots(
        figsize=(VALIDATION_LINE_WIDTH_INCHES, VALIDATION_HEIGHT_INCHES)
    )
    plot_pmf_curves(ax, series_by_protocol, references)
    ax.set_xlabel(TIME_AXIS_LABEL)
    ax.set_ylabel("Probability")
    ax.legend(loc="best")
    style_axes(ax)
    path = output_path(figure_dir, file_prefix, "validation_pmf", plot_profile)
    save_figure(fig, path, tight_layout=True, bbox_inches=None)
    plt.close(fig)
    return path


def plot_werner_validation(
    plt: Any,
    figure_dir: Path,
    file_prefix: str,
    plot_profile: Any,
    series_by_protocol: dict[str, ProtocolSeries],
    references: dict[str, ReferenceSeries],
    *,
    skip_legend: bool = False,
) -> Path:
    fig, ax = plt.subplots(
        figsize=(VALIDATION_LINE_WIDTH_INCHES, VALIDATION_HEIGHT_INCHES)
    )
    plot_werner_curves(ax, series_by_protocol, references)
    ax.set_xlabel(TIME_AXIS_LABEL)
    ax.set_ylabel("Werner parameter")
    if not skip_legend:
        ax.legend(loc="best")
    style_axes(ax)
    path = output_path(figure_dir, file_prefix, "validation_werner", plot_profile)
    save_figure(fig, path, tight_layout=True, bbox_inches=None)
    plt.close(fig)
    return path


def run_validation(args: Any) -> None:
    protocols = tuple(args.protocol or PROTOCOLS)
    output_dir = Path(args.output_dir)
    figure_dir = Path(args.figure_dir)

    if not args.plots_only:
        run_protocol_outputs(args, protocols, output_dir)
    elif not output_dir.is_dir():
        raise SystemExit(f"--plots-only requires an existing output directory: {output_dir}")

    series_by_protocol = {
        protocol: load_protocol_series(output_dir, args.file_prefix, protocol)
        for protocol in protocols
    }
    swap_asap_oracle = goodenough_lambda_4(args.p_gen, args.t_coh)

    li_references = load_reference_pickle(
        Path(args.li_reference),
        ("doubling",),
        quiet_missing=True,
    ) or load_reference_pair_pickles(
        Path(args.li_pmf_reference),
        Path(args.li_werner_reference),
        ("doubling",),
    )
    lacorte_references = load_reference_pickle(
        Path(args.lacorte_reference),
        ("left-to-right", "right-to-left"),
        quiet_missing=True,
    )
    lacorte_references.update(
        load_reference_pair_pickles(
            Path(args.lacorte_left_pmf_reference),
            Path(args.lacorte_left_werner_reference),
            ("left-to-right",),
        )
    )
    lacorte_references.update(
        load_reference_pair_pickles(
            Path(args.lacorte_right_pmf_reference),
            Path(args.lacorte_right_werner_reference),
            ("right-to-left",),
        )
    )
    references = {**li_references, **lacorte_references}

    rows: list[dict[str, Any]] = []
    for protocol, series in series_by_protocol.items():
        base_row: dict[str, Any] = {
            "protocol": protocol,
            "pure_mass": f"{series.pure_mass:.15g}",
            "total_mass": f"{series.total_mass:.15g}",
            "tail": f"{series.tail:.15g}",
            "extrema_gap": f"{series.extrema_gap:.15g}",
            "pure_json": str(series.pure_path),
            "mixed_json": str(series.mixed_path),
        }
        if protocol == "swap-asap":
            validation_row = validate_swap_asap(
                series,
                p_gen=args.p_gen,
                t_coh=args.t_coh,
                atol=args.goodenough_atol,
                tail_tolerance=args.tail_tolerance,
                strict=args.strict_goodenough,
            )
            base_row.update(validation_row)
        if protocol in references:
            base_row.update(
                compare_reference(
                    series,
                    references[protocol],
                    atol=args.reference_atol,
                )
            )
        rows.append(base_row)

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "validation_summary.csv"
    write_summary_csv(summary_path, rows)

    plt = configure_matplotlib(args.plot_profile)
    figure_paths = plot_validation(
        plt,
        figure_dir,
        args.file_prefix,
        args.plot_profile,
        series_by_protocol,
        references,
        swap_asap_oracle,
        skip_werner_legend=args.skip_werner_legend,
    )

    print("\n5-node swap-scheme validation")
    print(f"p_gen={args.p_gen:.12g}, t_coh={args.t_coh}, truncation={args.truncation}")
    print(f"Goodenough E[Lambda_4] = {swap_asap_oracle:.15g}")
    for row in rows:
        print(
            f"{row['protocol']:<15} pure_mass={row['pure_mass']} "
            f"tail={row['tail']}"
        )
    print(f"Summary CSV: {summary_path}")
    print(f"Combined validation figure: {figure_paths['combined']}")
    print(f"PMF validation figure: {figure_paths['pmf']}")
    print(f"Werner validation figure: {figure_paths['werner']}")
