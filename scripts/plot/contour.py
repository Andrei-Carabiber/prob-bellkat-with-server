from __future__ import annotations

import numpy as np

from scripts.plot.config import style_axes


RATIO_CONTOUR_LEVELS = 101


def tick_label(value: float | int) -> str:
    return f"{float(value):.12g}"


def draw_ratio_contour(
    fig,
    ax,
    x_values,
    y_values,
    ratios,
    *,
    cmap: str,
    colorbar_label: str,
    xlabel: str,
    ylabel: str,
    log_x: bool = False,
    log_y: bool = False,
    show_xlabel: bool = True,
    levels: int = RATIO_CONTOUR_LEVELS,
):
    """Draw a consistently styled ratio contour centered on equality when possible."""
    from matplotlib.colors import TwoSlopeNorm
    from matplotlib.ticker import NullLocator

    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    ratio = np.asarray(ratios, dtype=float)

    finite_ratio = ratio[np.isfinite(ratio)]
    contour_kwargs = {"levels": levels, "cmap": cmap}
    if finite_ratio.size > 0:
        ratio_min = float(np.nanmin(finite_ratio))
        ratio_max = float(np.nanmax(finite_ratio))
        if ratio_min < 1.0 < ratio_max:
            contour_kwargs["norm"] = TwoSlopeNorm(
                vmin=ratio_min,
                vcenter=1.0,
                vmax=ratio_max,
            )

    heatmap = ax.contourf(
        x,
        y,
        np.ma.masked_invalid(ratio),
        **contour_kwargs,
    )

    if log_x:
        ax.set_xscale("log")
        ax.xaxis.set_minor_locator(NullLocator())
    if log_y:
        ax.set_yscale("log")
        ax.yaxis.set_minor_locator(NullLocator())

    ax.set_xlabel(xlabel if show_xlabel else "")
    ax.set_ylabel(ylabel)
    ax.set_xticks(x)
    ax.set_yticks(y)
    ax.set_xticklabels(
        [tick_label(value) for value in x],
        rotation=45,
        ha="right",
        rotation_mode="anchor",
    )
    ax.set_yticklabels([tick_label(value) for value in y])
    style_axes(ax)
    return fig.colorbar(heatmap, ax=ax, label=colorbar_label)
