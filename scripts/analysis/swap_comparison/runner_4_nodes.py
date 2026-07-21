#!/usr/bin/env python3

from scripts.analysis.swap_comparison.common import ComparisonConfig, run_comparison


CONFIG = ComparisonConfig(
    description=(
        "Run the 4-node P-swap comparison protocols through the MDP and QMDP "
        "pipelines, then plot their reachability and Werner curves together."
    ),
    default_protocols=("asap", "left-to-right", "right-to-left", "at-last"),
    executable="quantP_compare_swap_4",
    output_dir="output/pswap-comparison",
    figure_dir="output/pswap-comparison",
    file_prefix="pswap",
    figure_prefix="pswap",
)


if __name__ == "__main__":
    run_comparison(CONFIG)
