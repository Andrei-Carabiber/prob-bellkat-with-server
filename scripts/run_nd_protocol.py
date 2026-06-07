#!/usr/bin/env python3

"""Run a non-deterministic protocol by optimizing once for the primary event 
and injecting scheduler artifacts for the secondary events.

The configuration file describes the semantics, truncation or coverage target,
primary event, secondary events, and derived series. Command-line options
override the corresponding configuration entries.
"""

import argparse
import json
import pickle
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_EXTREMA = ("min", "max")
FIGURE_SIZE = (3.5, 2.3)
LINK_COLORS = ("#1b4f72", "#ee7833")
CHECK_ATOL = 1e-9


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a non-deterministic BellKAT protocol with replayed scheduler artifacts."
    )
    parser.add_argument("config", help="YAML or JSON run configuration.")
    parser.add_argument(
        "--executable",
        help="Cabal executable to run. Overrides config executable.",
    )
    parser.add_argument(
        "--output-dir",
        default="output/nd_protocol",
        help="Directory for solver JSON, scheduler artifacts, derived pickles, and plots.",
    )
    parser.add_argument("--mode", choices=("mdp", "qmdp"), help="Override config mode.")
    parser.add_argument("--truncation", type=int, help="Override config truncation/R.")
    parser.add_argument("--coverage", type=float, help="Override config coverage/eta.")
    parser.add_argument("--no-plots", action="store_true", help="Skip PDF plot generation.")
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)

    try:
        import yaml  # type: ignore
    except ImportError:
        return simple_yaml_load(text)

    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise SystemExit("Configuration must be a mapping/object.")
    return loaded


def simple_yaml_load(text: str) -> dict[str, Any]:
    """A tiny YAML subset loader for the guide's config shape.

    It supports top-level scalars plus top-level lists of dictionaries. If users
    need richer YAML, installing PyYAML automatically enables the full parser.
    """
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    current_item: dict[str, Any] | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = strip_yaml_comment(raw_line).rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            key, value = split_yaml_key_value(stripped, line_number)
            if value == "":
                result[key] = []
                current_list_key = key
                current_item = None
            else:
                result[key] = parse_scalar(value)
                current_list_key = None
                current_item = None
            continue

        if indent == 2 and stripped.startswith("- "):
            if current_list_key is None or not isinstance(result.get(current_list_key), list):
                raise SystemExit(f"Line {line_number}: list item without a list key.")
            current_item = {}
            result[current_list_key].append(current_item)
            rest = stripped[2:].strip()
            if rest:
                key, value = split_yaml_key_value(rest, line_number)
                current_item[key] = parse_scalar(value)
            continue

        if indent >= 4:
            if current_item is None:
                raise SystemExit(f"Line {line_number}: nested value without a list item.")
            key, value = split_yaml_key_value(stripped, line_number)
            current_item[key] = parse_scalar(value)
            continue

        raise SystemExit(f"Line {line_number}: unsupported YAML indentation.")

    return result


def strip_yaml_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return line[:index]
    return line


def split_yaml_key_value(text: str, line_number: int) -> tuple[str, str]:
    if ":" not in text:
        raise SystemExit(f"Line {line_number}: expected key: value.")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise SystemExit(f"Line {line_number}: empty key.")
    return key, value.strip()


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part.strip()) for part in split_inline_list(inner)]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def split_inline_list(value: str) -> list[str]:
    parts: list[str] = []
    quote: str | None = None
    start = 0
    for index, char in enumerate(value):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == ",":
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return parts


def resolve_run_config(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(config)
    if args.mode is not None:
        resolved["mode"] = args.mode
    if args.truncation is not None:
        resolved["truncation"] = args.truncation
        resolved.pop("coverage", None)
        resolved.pop("eta", None)
        resolved.pop("ev_eta", None)
    if args.coverage is not None:
        resolved["coverage"] = args.coverage
        resolved.pop("truncation", None)

    mode = resolved.get("mode", "qmdp")
    if mode not in {"mdp", "qmdp"}:
        raise SystemExit("mode must be either 'mdp' or 'qmdp'.")
    resolved["mode"] = mode

    has_truncation = "truncation" in resolved or "R" in resolved
    has_coverage = "coverage" in resolved or "eta" in resolved
    if has_truncation and has_coverage:
        raise SystemExit("Specify either truncation/R or coverage/eta, not both.")
    if not has_truncation and not has_coverage:
        raise SystemExit("Specify either truncation/R or coverage/eta plus ev_eta.")
    if has_coverage and not resolved.get("ev_eta"):
        raise SystemExit("coverage/eta requires ev_eta.")
    if has_truncation and resolved.get("ev_eta"):
        raise SystemExit("ev_eta is only used with coverage/eta.")
    if not resolved.get("ev_opt"):
        raise SystemExit("Configuration requires ev_opt.")
    if not isinstance(resolved.get("evs"), list) or not resolved["evs"]:
        raise SystemExit("Configuration requires a non-empty evs list.")

    return resolved


def config_truncation(config: dict[str, Any]) -> int | None:
    value = config.get("truncation", config.get("R"))
    return None if value is None else int(value)


def config_coverage(config: dict[str, Any]) -> float | None:
    value = config.get("coverage", config.get("eta"))
    return None if value is None else float(value)


def run_solver(
    executable: str,
    mode: str,
    event: str,
    output_path: Path,
    *,
    truncation: int | None = None,
    coverage: float | None = None,
    scheduler_path: Path | None = None,
) -> dict[str, Any]:
    cmd = [
        "cabal",
        "-v0",
        "run",
        executable,
        "--",
        "--event",
        event,
        "--json",
        mode,
        "--compute-extremal",
    ]
    if truncation is not None:
        cmd.extend(["--truncation", str(truncation)])
    if coverage is not None:
        cmd.extend(["--coverage", str(coverage)])
    if scheduler_path is not None:
        cmd.extend(["--scheduler", str(scheduler_path)])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    print("Running:", " ".join(cmd), flush=True)
    with output_path.open("w", encoding="utf-8") as handle:
        subprocess.run(cmd, check=True, stdout=handle)

    with output_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_scheduler_artifact(path: Path, scheduler: dict[str, Any]) -> None:
    path.write_text(
        json.dumps({"scheduler": scheduler}, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def extract_coverage_budget(payload: dict[str, Any]) -> int:
    status = payload.get("extremal", {}).get("coverage_status")
    if not isinstance(status, dict):
        raise SystemExit("Coverage run did not return coverage_status.")
    if status.get("status") != "reached":
        raise SystemExit(
            "Coverage target was not reached; refusing to continue with an unresolved truncation."
        )
    return int(status["budget"])


def scheduled_series(payload: dict[str, Any], label: str) -> tuple[list[float], list[float]]:
    try:
        series = payload["scheduled"]["series"]
        cdf = [float(value) for value in series["cdf"]]
    except KeyError as exc:
        raise SystemExit("Scheduled solver output is missing scheduled.series.cdf.") from exc

    if "pmf" in series:
        pmf = [float(value) for value in series["pmf"]]
    else:
        validate_cdf_monotone(cdf, label)
        pmf = pmf_from_cdf(cdf)

    validate_pmf_nonnegative(pmf, label)
    validate_cdf_monotone(cdf, label)
    validate_probability_bounds(cdf, label)
    align_lengths([pmf, cdf], label)

    reconstructed = cdf_from_pmf(pmf)
    max_abs_error = max(
        (abs(actual - expected) for actual, expected in zip(cdf, reconstructed)),
        default=0.0,
    )
    if max_abs_error > CHECK_ATOL:
        raise SystemExit(
            f"Scheduled series {label!r} has inconsistent PMF/CDF: "
            f"max_abs_error={max_abs_error:.3e} > {CHECK_ATOL:.3e}."
        )

    return pmf, cdf


def pmf_from_cdf(cdf: list[float]) -> list[float]:
    if not cdf:
        return []
    return [cdf[0], *(curr - prev for prev, curr in zip(cdf, cdf[1:]))]


def cdf_from_pmf(pmf: list[float]) -> list[float]:
    total = 0.0
    cdf: list[float] = []
    for value in pmf:
        total += value
        cdf.append(total)
    return cdf


def ratio_series(numerator: list[float], denominator: list[float]) -> list[float]:
    align_lengths([numerator, denominator], "ratio")
    return [
        top / bottom if bottom > 0 else 0.0
        for top, bottom in zip(numerator, denominator)
    ]


def validate_pmf_nonnegative(pmf: list[float], label: str) -> None:
    for index, value in enumerate(pmf):
        if value < -CHECK_ATOL:
            raise SystemExit(
                f"Series {label!r} has negative PMF at t={index}: {value:.12g}."
            )


def validate_cdf_monotone(cdf: list[float], label: str) -> None:
    for index, (prev, curr) in enumerate(zip(cdf, cdf[1:]), start=1):
        if curr + CHECK_ATOL < prev:
            raise SystemExit(
                f"Series {label!r} has non-monotone CDF at t={index}: "
                f"previous={prev:.12g}, current={curr:.12g}."
            )


def validate_probability_bounds(cdf: list[float], label: str) -> None:
    for index, value in enumerate(cdf):
        if value < -CHECK_ATOL or value > 1.0 + CHECK_ATOL:
            raise SystemExit(
                f"Series {label!r} has CDF outside [0,1] at t={index}: {value:.12g}."
            )


def align_lengths(series: list[list[float]], name: str) -> None:
    lengths = {len(values) for values in series}
    if len(lengths) != 1:
        raise SystemExit(f"Derived series {name} has mismatched operand lengths: {sorted(lengths)}.")


def safe_series_name(name: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in name)


def derived_link_label(name: str, suffix: str) -> str | None:
    lowered = name.lower()
    lowered_suffix = suffix.lower()
    if not lowered.endswith(lowered_suffix):
        return None

    prefix = name[: -len(suffix)]
    prefix = prefix.rstrip("_- ")
    if not prefix:
        return None
    if len(prefix) == 2 and prefix.isalpha():
        return f"{prefix[0].upper()}~{prefix[1].upper()}"
    return prefix.replace("_", "~").replace("-", "~")


def latex_link(label: str) -> str:
    parts = label.replace("-", "~").split("~")
    if len(parts) == 2 and all(parts):
        return rf"{parts[0]}\sim {parts[1]}"
    return label.replace("_", r"\_").replace("~", r"\sim ")


def scheduler_latex(extremum: str) -> str:
    if extremum == "min":
        return r"\sigma_{\min}"
    if extremum == "max":
        return r"\sigma_{\max}"
    escaped = extremum.replace("_", r"\_")
    return rf"\sigma_{{\mathrm{{{escaped}}}}}"


def link_cdf_label(label: str, extremum: str) -> str:
    return rf"$\Pr^{{{scheduler_latex(extremum)}}}(T^{{{latex_link(label)}}}\leq t)$"


def link_werner_label(label: str, extremum: str) -> str:
    return rf"$W^{{{scheduler_latex(extremum)}}}_{{{latex_link(label)}}}(t)$"


def link_werner_cdf_label(label: str, extremum: str) -> str:
    return rf"$W^{{{scheduler_latex(extremum)}}}_{{{latex_link(label)}}}(T\leq t)$"


def style_axes(ax: Any) -> None:
    ax.grid(True, which="major", linestyle=":", linewidth=0.35, alpha=0.45)


def plot_derived_series(path: Path, values: list[float], *, ylabel: str, label: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"matplotlib is not available; skipping {path}.")
        return

    t = list(range(len(values)))
    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    ax.plot(t, values, color="#1b4f72", linewidth=1.6, label=label)
    ax.set_xlabel("$t$")
    ax.set_ylabel(ylabel)
    if t:
        ax.set_xlim(min(t), max(t))
    # if "Werner" in ylabel:
    #     ax.set_ylim(0.0, 1.0)
    style_axes(ax)
    ax.legend(frameon=False, loc="best", fontsize=8)
    fig.tight_layout(pad=0.25)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_link_cdf_summary(
    path: Path,
    link_pmfs: list[tuple[str, dict[str, list[float]]]],
) -> None:
    if not link_pmfs:
        return

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"matplotlib is not available; skipping {path}.")
        return

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    plotted = False
    for index, (label, extrema_values) in enumerate(link_pmfs):
        extrema = ordered_extrema(extrema_values)
        if not extrema:
            continue

        cdfs = {extremum: cdf_from_pmf(extrema_values[extremum]) for extremum in extrema}
        align_lengths(list(cdfs.values()), label)
        t = list(range(len(next(iter(cdfs.values())))))
        color = LINK_COLORS[index % len(LINK_COLORS)]

        # if "min" in cdfs and "max" in cdfs:
        #     ax.fill_between(t, cdfs["min"], cdfs["max"], color=color, alpha=0.12, linewidth=0)

        for extremum in extrema:
            ax.plot(
                t,
                cdfs[extremum],
                color=color,
                linestyle=extremum_linestyle(extremum),
                linewidth=2.0 if extremum == "max" else 1.5,
                label=link_cdf_label(label, extremum),
            )
            plotted = True
    
    ax.set_xlabel("$t$")
    ax.set_ylabel("Cumulative probability")
    # ax.set_ylim(0.0, 1.0)
    if link_pmfs:
        max_len = max(
            len(values)
            for _, extrema_values in link_pmfs
            for values in extrema_values.values()
        )
        if max_len > 0:
            ax.set_xlim(0, max_len - 1)
    style_axes(ax)
    if plotted:
        ax.legend(frameon=False, loc="lower right", fontsize=8)
    fig.tight_layout(pad=0.25)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_link_werner_summary(
    path: Path,
    link_werners: list[tuple[str, dict[str, list[float]]]],
) -> None:
    if not link_werners:
        return

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"matplotlib is not available; skipping {path}.")
        return

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    plotted = False
    for index, (label, extrema_values) in enumerate(link_werners):
        extrema = ordered_extrema(extrema_values)
        if not extrema:
            continue

        series = {extremum: extrema_values[extremum] for extremum in extrema}
        align_lengths(list(series.values()), label)
        full_t = list(range(len(next(iter(series.values())))))

        # Skip the initial points where all plotted Werner series are 0.
        keep_indexes = [
            index
            for index in full_t
            if any(series[extremum][index] > 0.0 for extremum in extrema)
        ]
        if not keep_indexes:
            continue

        t = keep_indexes
        color = LINK_COLORS[index % len(LINK_COLORS)]

        for extremum in extrema:
            ax.plot(
                t,
                [series[extremum][index] for index in keep_indexes],
                color=color,
                linestyle=extremum_linestyle(extremum),
                linewidth=2.0 if extremum == "max" else 1.5,
                label=link_werner_label(label, extremum),
            )
            plotted = True

    ax.set_xlabel("$t$")
    ax.set_ylabel("Werner parameter")
    # ax.set_ylim(0.0, 1.0)
    if link_werners:
        max_len = max(
            len(values)
            for _, extrema_values in link_werners
            for values in extrema_values.values()
        )
        if max_len > 0:
            ax.set_xlim(0, max_len - 1)
    style_axes(ax)
    if plotted:
        ax.legend(frameon=False, loc="best", fontsize=8)
    fig.tight_layout(pad=0.25)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_link_werner_cdf_summary(
    path: Path,
    link_werners: list[tuple[str, dict[str, list[float]]]],
) -> None:
    if not link_werners:
        return

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"matplotlib is not available; skipping {path}.")
        return

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    plotted = False
    for index, (label, extrema_values) in enumerate(link_werners):
        extrema = ordered_extrema(extrema_values)
        if not extrema:
            continue

        series = {extremum: extrema_values[extremum] for extremum in extrema}
        align_lengths(list(series.values()), label)
        full_t = list(range(len(next(iter(series.values())))))

        keep_indexes = [
            index
            for index in full_t
            if any(series[extremum][index] > 0.0 for extremum in extrema)
        ]
        if not keep_indexes:
            continue

        t = keep_indexes
        color = LINK_COLORS[index % len(LINK_COLORS)]

        for extremum in extrema:
            ax.plot(
                t,
                [series[extremum][index] for index in keep_indexes],
                color=color,
                linestyle=extremum_linestyle(extremum),
                linewidth=2.0 if extremum == "max" else 1.5,
                label=link_werner_cdf_label(label, extremum),
            )
            plotted = True

    ax.set_xlabel("$t$")
    ax.set_ylabel("Werner parameter")
    # ax.set_ylim(0.0, 1.0)
    if link_werners:
        max_len = max(
            len(values)
            for _, extrema_values in link_werners
            for values in extrema_values.values()
        )
        if max_len > 0:
            ax.set_xlim(0, max_len - 1)
    style_axes(ax)
    if plotted:
        ax.legend(frameon=False, loc="best", fontsize=8)
    fig.tight_layout(pad=0.25)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def ordered_extrema(extrema_values: dict[str, list[float]]) -> list[str]:
    preferred = [extremum for extremum in DEFAULT_EXTREMA if extremum in extrema_values]
    extra = sorted(extremum for extremum in extrema_values if extremum not in DEFAULT_EXTREMA)
    return preferred + extra


def extremum_linestyle(extremum: str) -> str:
    if extremum == "max":
        return "-"
    return "--"


def plot_derived_summaries(
    output_dir: Path,
    config: dict[str, Any],
    derived_values: dict[str, dict[str, list[float]]],
    derived_cdf_ratio_values: dict[str, dict[str, list[float]]],
) -> None:
    link_pmfs: list[tuple[str, dict[str, list[float]]]] = []
    link_werners: list[tuple[str, dict[str, list[float]]]] = []
    link_werner_cdfs: list[tuple[str, dict[str, list[float]]]] = []

    for spec in config.get("derived", []):
        name = spec["name"]
        if name not in derived_values:
            continue

        pmf_label = derived_link_label(name, "_pmf")
        if pmf_label is not None:
            link_pmfs.append((pmf_label, derived_values[name]))
            continue

        werner_label = derived_link_label(name, "_werner")
        if werner_label is not None:
            link_werners.append((werner_label, derived_values[name]))
            if name in derived_cdf_ratio_values:
                link_werner_cdfs.append((werner_label, derived_cdf_ratio_values[name]))

    plot_link_cdf_summary(output_dir / "links_cdf.pdf", link_pmfs)
    plot_link_werner_summary(output_dir / "links_werner.pdf", link_werners)
    plot_link_werner_cdf_summary(output_dir / "links_werner_cdf.pdf", link_werner_cdfs)


def event_check_target(event_spec: dict[str, Any]) -> str | None:
    if "compare_cdf_with" in event_spec:
        return str(event_spec["compare_cdf_with"])
    if "check_against" in event_spec:
        return str(event_spec["check_against"])

    name = str(event_spec.get("name", ""))
    suffix = "_check"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return None


def validate_derived_checks(
    config: dict[str, Any],
    output_dir: Path,
    cdfs: dict[str, dict[str, list[float]]],
    derived_values: dict[str, dict[str, list[float]]],
) -> None:
    reports: list[dict[str, Any]] = []

    for event_spec in config.get("checks", []):
        name = event_spec["name"]
        target_name = event_check_target(event_spec)
        if target_name is None:
            continue
        if target_name not in derived_values:
            raise SystemExit(
                f"Check event {name!r} targets derived series {target_name!r}, "
                "but no such derived series was computed."
            )

        extrema = event_spec.get("extrema", DEFAULT_EXTREMA)
        for extremum in extrema:
            if extremum not in cdfs.get(name, {}):
                raise SystemExit(f"Check event {name!r} is missing CDF for {extremum!r}.")
            if extremum not in derived_values[target_name]:
                raise SystemExit(
                    f"Derived check target {target_name!r} is missing {extremum!r}."
                )

            actual_cdf = cdfs[name][extremum]
            expected_cdf = cdf_from_pmf(derived_values[target_name][extremum])
            align_lengths([actual_cdf, expected_cdf], name)
            max_abs_error = max(
                (abs(actual - expected) for actual, expected in zip(actual_cdf, expected_cdf)),
                default=0.0,
            )
            report = {
                "name": name,
                "target": target_name,
                "extremum": extremum,
                "max_abs_error": max_abs_error,
                "atol": CHECK_ATOL,
            }
            reports.append(report)
            if max_abs_error > CHECK_ATOL:
                raise SystemExit(
                    f"Derived check {name!r} against {target_name!r} failed for "
                    f"{extremum}: max_abs_error={max_abs_error:.3e} > {CHECK_ATOL:.3e}."
                )

    if reports:
        (output_dir / "series_checks.json").write_text(
            json.dumps(reports, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        for report in reports:
            print(
                "Check passed: "
                f"{report['name']} vs {report['target']} ({report['extremum']}), "
                f"max_abs_error={report['max_abs_error']:.3e}."
            )


def evaluate_derived(
    config: dict[str, Any],
    output_dir: Path,
    pmfs: dict[str, dict[str, list[float]]],
    cdfs: dict[str, dict[str, list[float]]],
    *,
    make_plots: bool,
) -> dict[str, dict[str, list[float]]]:
    derived_values: dict[str, dict[str, list[float]]] = {}
    derived_cdf_ratio_values: dict[str, dict[str, list[float]]] = {}

    def get_pmf(name: str, extremum: str) -> list[float]:
        if name in derived_values and extremum in derived_values[name]:
            return derived_values[name][extremum]
        if name in pmfs and extremum in pmfs[name]:
            return pmfs[name][extremum]
        raise SystemExit(f"Unknown series {name!r} for scheduler extremum {extremum!r}.")

    for spec in config.get("derived", []):
        name = spec["name"]
        extrema = spec.get("extrema", DEFAULT_EXTREMA)
        derived_values.setdefault(name, {})

        for extremum in extrema:
            if "sum" in spec:
                operands = [get_pmf(operand, extremum) for operand in spec["sum"]]
                align_lengths(operands, name)
                values = [sum(parts) for parts in zip(*operands)]
                validate_pmf_nonnegative(values, f"{name}:{extremum}")
                validate_probability_bounds(cdf_from_pmf(values), f"{name}:{extremum}")
                ylabel = "Probability"
            elif "ratio" in spec:
                numerator_name, denominator_name = spec["ratio"]
                numerator = get_pmf(numerator_name, extremum)
                denominator = get_pmf(denominator_name, extremum)
                values = ratio_series(numerator, denominator)
                cumulative_values = ratio_series(
                    cdf_from_pmf(numerator),
                    cdf_from_pmf(denominator),
                )
                ylabel = "Werner parameter"
            else:
                raise SystemExit(f"Derived series {name!r} must specify either sum or ratio.")

            derived_values[name][extremum] = values
            stem = f"{safe_series_name(name)}_{extremum}"
            with (output_dir / f"{stem}.pkl").open("wb") as handle:
                pickle.dump(values, handle)
            (output_dir / f"{stem}.json").write_text(
                json.dumps(
                    {"name": name, "extremum": extremum, "series": values},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            if make_plots:
                plot_derived_series(
                    output_dir / f"{stem}.pdf",
                    values,
                    ylabel=ylabel,
                    label=f"{name} ({extremum})",
                )

            if "ratio" in spec:
                cdf_stem = f"{safe_series_name(name)}_cdf_{extremum}"
                derived_cdf_ratio_values.setdefault(name, {})[extremum] = cumulative_values
                with (output_dir / f"{cdf_stem}.pkl").open("wb") as handle:
                    pickle.dump(cumulative_values, handle)
                (output_dir / f"{cdf_stem}.json").write_text(
                    json.dumps(
                        {
                            "name": f"{name}_cdf",
                            "extremum": extremum,
                            "series": cumulative_values,
                        },
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                if make_plots:
                    plot_derived_series(
                        output_dir / f"{cdf_stem}.pdf",
                        cumulative_values,
                        ylabel="Cumulative Werner parameter",
                        label=f"{name} CDF ({extremum})",
                    )

    if make_plots:
        plot_derived_summaries(output_dir, config, derived_values, derived_cdf_ratio_values)

    validate_derived_checks(config, output_dir, cdfs, derived_values)

    return derived_values


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = resolve_run_config(args, load_config(Path(args.config)))
    mode = config["mode"]
    executable = args.executable or str(config.get("executable", "quantP_analyze_star"))

    truncation = config_truncation(config)
    coverage = config_coverage(config)

    # Step Ev_Eta: run with coverage to resolve truncation budget (if needed)
    if coverage is not None:
        eta_payload = run_solver(
            executable,
            mode,
            config["ev_eta"],
            output_dir / "coverage_eta.json",
            coverage=coverage,
        )
        truncation = extract_coverage_budget(eta_payload)
        print(f"Coverage truncation resolved to R={truncation}.")

    if truncation is None:
        raise SystemExit("Internal error: truncation was not resolved.")

    # Step Ev_Opt: run (with truncation) to get extremal schedulers
    opt_payload = run_solver(
        executable,
        mode,
        config["ev_opt"],
        output_dir / "ev_opt.json",
        truncation=truncation,
    )
    schedulers = opt_payload.get("extremal", {}).get("schedulers")
    if not isinstance(schedulers, dict):
        raise SystemExit("Optimization run did not return extremal.schedulers.")

    # Dump scheduler artifacts for all extrema 
    scheduler_paths: dict[str, Path] = {}
    for extremum in DEFAULT_EXTREMA:
        if extremum not in schedulers:
            raise SystemExit(f"Optimization run did not return {extremum} scheduler.")
        path = output_dir / f"scheduler_{extremum}.json"
        write_scheduler_artifact(path, schedulers[extremum])
        scheduler_paths[extremum] = path

    # Step Ev_Evs: run for all events with injected schedulers to get PMFs/CDFs
    pmfs: dict[str, dict[str, list[float]]] = {}
    cdfs: dict[str, dict[str, list[float]]] = {}
    for event_spec in [*config["evs"], *config.get("checks", [])]:
        name = event_spec["name"]
        event = event_spec["event"]
        extrema = event_spec.get("extrema", DEFAULT_EXTREMA)
        pmfs.setdefault(name, {})
        cdfs.setdefault(name, {})
        for extremum in extrema:
            payload = run_solver(
                executable,
                mode,
                event,
                output_dir / f"{safe_series_name(name)}_{extremum}.json",
                truncation=truncation,
                scheduler_path=scheduler_paths[extremum],
            )
            pmf, cdf = scheduled_series(payload, f"{name}:{extremum}")
            pmfs[name][extremum] = pmf
            cdfs[name][extremum] = cdf

    # Step Derived: compute derived series, save outputs, and make plots
    evaluate_derived(config, output_dir, pmfs, cdfs, make_plots=not args.no_plots)
    print(f"Done. Outputs are in {output_dir}.")


if __name__ == "__main__":
    main()
