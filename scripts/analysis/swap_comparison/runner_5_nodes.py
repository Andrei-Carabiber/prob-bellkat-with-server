#!/usr/bin/env python3

from scripts.analysis.swap_comparison.common import ComparisonConfig, run_comparison


CONFIG = ComparisonConfig(
    description=(
        "Run the 5-node topology-aware swap-scheme comparison through the MDP "
        "and QMDP pipelines, then plot reachability and Werner curves together."
    ),
    default_protocols=(
        "doubling",
        "left-to-right",
        "swap-asap",
        "right-to-left",
    ),
    executable="quantP_compare_swap_schemes",
    output_dir="output/swap-schemes",
    figure_dir="output/swap-schemes",
    file_prefix="swap-schemes",
    figure_prefix="swap-schemes",
)


if __name__ == "__main__":
    run_comparison(CONFIG)
