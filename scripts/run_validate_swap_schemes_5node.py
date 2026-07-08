#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.plot.config import DEFAULT_PROFILE, PLOT_SETTINGS
from scripts.validate_swap_schemes_5node import PROTOCOLS, run_validation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the 5-node swap-scheme protocols with instantaneous swaps. "
            "swap-asap is checked against Goodenough et al. Appendix D.1 with n=4; "
            "doubling and sequential policies can be checked against external "
            "Li/La Corte pickle references when those files are available."
        )
    )
    parser.add_argument(
        "--protocol",
        action="append",
        choices=PROTOCOLS,
        help="Protocol to run. Can be passed more than once. Defaults to all four.",
    )
    parser.add_argument("--p-gen", type=float, default=9.187e-04)
    parser.add_argument("--t-coh", type=int, default=144000)
    parser.add_argument("--truncation", type=int, default=7500)
    parser.add_argument(
        "--executable",
        default="quantP_validate_swap_schemes",
        help="Cabal executable name or path to an already-built executable.",
    )
    parser.add_argument(
        "--output-dir",
        default="output/validation/infocom-swap-schemes-5node",
        help="Directory for generated QMDP JSON and validation_summary.csv.",
    )
    parser.add_argument(
        "--figure-dir",
        default="output/validation/infocom-swap-schemes-5node/figures",
        help="Directory for the combined validation figure.",
    )
    parser.add_argument(
        "--file-prefix",
        default="infocom_swap_schemes_5node",
        help="Prefix for generated JSON and figure names.",
    )
    parser.add_argument(
        "--li-reference",
        default="scripts/references/li_doubling_5node.pkl",
        help="Combined pickle containing Li et al. validation data for doubling.",
    )
    parser.add_argument(
        "--li-pmf-reference",
        default="scripts/tests/dump/infocom_doubling_pmf.pkl",
        help="PMF pickle containing Li et al. validation data for doubling.",
    )
    parser.add_argument(
        "--li-werner-reference",
        default="scripts/tests/dump/infocom_doubling_werner.pkl",
        help="Werner pickle containing Li et al. validation data for doubling.",
    )
    parser.add_argument(
        "--lacorte-reference",
        default="scripts/references/lacorte_sequential_5node.pkl",
        help="Combined pickle containing La Corte et al. data for left/right sequential protocols.",
    )
    parser.add_argument(
        "--lacorte-left-pmf-reference",
        default="scripts/tests/dump/infocom_left_to_right_pmf.pkl",
        help="PMF pickle containing La Corte et al. data for left-to-right.",
    )
    parser.add_argument(
        "--lacorte-left-werner-reference",
        default="scripts/tests/dump/infocom_left_to_right_werner.pkl",
        help="Werner pickle containing La Corte et al. data for left-to-right.",
    )
    parser.add_argument(
        "--lacorte-right-pmf-reference",
        default="scripts/tests/dump/infocom_right_to_left_pmf.pkl",
        help="PMF pickle containing La Corte et al. data for right-to-left.",
    )
    parser.add_argument(
        "--lacorte-right-werner-reference",
        default="scripts/tests/dump/infocom_right_to_left_werner.pkl",
        help="Werner pickle containing La Corte et al. data for right-to-left.",
    )
    parser.add_argument(
        "--goodenough-atol",
        type=float,
        default=1e-9,
        help="Absolute tolerance for swap-asap E[Lambda_4].",
    )
    parser.add_argument(
        "--reference-atol",
        type=float,
        default=1e-6,
        help="Absolute tolerance for optional pickle reference series.",
    )
    parser.add_argument(
        "--tail-tolerance",
        type=float,
        default=1e-9,
        help="Maximum tolerated unobserved completion mass at the truncation horizon.",
    )
    parser.add_argument(
        "--strict-goodenough",
        action="store_true",
        help="Require the truncation tail to be below --tail-tolerance before accepting the Goodenough comparison.",
    )
    parser.add_argument(
        "--plot-profile",
        choices=tuple(PLOT_SETTINGS),
        default=DEFAULT_PROFILE,
    )
    parser.add_argument(
        "--plots-only",
        action="store_true",
        help="Skip Cabal runs and validate/plot from existing JSON in --output-dir.",
    )
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="When running Cabal, skip any pure/mixed JSON file that already exists.",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip the initial cabal build step.",
    )
    args = parser.parse_args()
    validate_args(parser, args)
    return args


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not 0.0 < args.p_gen < 1.0:
        parser.error("--p-gen must be in (0, 1).")
    if args.t_coh <= 0:
        parser.error("--t-coh must be positive.")
    if args.truncation < 0:
        parser.error("--truncation must be non-negative.")
    if args.goodenough_atol < 0.0:
        parser.error("--goodenough-atol must be non-negative.")
    if args.reference_atol < 0.0:
        parser.error("--reference-atol must be non-negative.")
    if args.tail_tolerance < 0.0:
        parser.error("--tail-tolerance must be non-negative.")


if __name__ == "__main__":
    run_validation(parse_args())
