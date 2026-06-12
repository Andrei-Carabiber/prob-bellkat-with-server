#!/usr/bin/env python3

from scripts.analysis.swap_comparison.common import ComparisonConfig, run_comparison


CONFIG = ComparisonConfig(
    description=(
        "Run the 5-node P-swap comparison protocols through the MDP and QMDP "
        "pipelines, then plot their reachability and Werner curves together."
    ),
    default_protocols=(
        "asap",
        "left-to-right",
        "right-to-left",
        "at-last",
        "doubling",
    ),
    executable="quantP_compare_swap_5",
    output_dir="output/pswap-comparison-5",
    figure_dir="output/pswap-comparison-5",
    file_prefix="pswap5",
    figure_prefix="pswap5",
)


if __name__ == "__main__":
    run_comparison(CONFIG)
