"""Poster / write-up figures 2, 3, 4, 6, 7 (M4_PLAN.md section 4).

Every number below is copied from RESULTS.md, with its section cited beside it.
That is deliberate: M4's rule is "nothing in the write-up cites a number that is
not in RESULTS.md", so the figures are generated FROM the compendium rather than
re-derived from CSVs that may have moved on. If RESULTS.md changes, change the
constants here and re-run; do not edit a PNG.

Not generated here: Figure 1 (threat model, hand-drawn) and Figure 5 (the attack
ladder -- its rungs mix units: counts, ratios and per-session ranges, so it is a
table/diagram, not a chart).

    python research/make_figures.py            # -> figures/fig*.png and .pdf

Design: one y-axis everywhere, thin marks, chance lines dashed and labelled,
CIs as whiskers, and the VALUE printed on every mark (the aqua series is below
3:1 contrast on the surface, so visible labels are mandatory, not decoration).
Categorical colours: blue, orange, aqua -- validated for colour-vision
deficiency (worst adjacent deltaE 9.2, deutan).
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent.parent / "figures"
OUT.mkdir(exist_ok=True)

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e3e2de", "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
    "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
    "font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.axisbelow": True, "savefig.dpi": 200, "savefig.bbox": "tight",
})


def save(fig, name):
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"{name}.{ext}")
    plt.close(fig)
    print("wrote", OUT / f"{name}.png")


def label(ax, x, y, text, dy=0.012, **kw):
    ax.text(x, y + dy, text, ha="center", va="bottom", fontsize=10, color=INK, **kw)


# ---------------------------------------------------------------- Figure 2 ---
# RESULTS.md section 3: balanced accuracy, 95% CI, chance, n.
# Harness CI is a one-sided rule-of-three bound (>= 0.984), so its whisker only
# extends downward and is drawn as such.
def fig2():
    axes_ = [  # name, n, point, lo, hi, chance
        ("Harness\n(4-class, n=190)", 1.000, 0.984, 1.000, 0.250),
        ("Model\n(2-class, n=86)", 0.895, 0.825, 0.953, 0.500),
        ("Perception: mechanism\n(dom vs vision, n=91)", 0.879, 0.810, 0.936, 0.500),
        ("Perception\n(3-class, n=138)", 0.860, 0.800, 0.913, 0.333),
    ]
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    for i, (name, p, lo, hi, ch) in enumerate(axes_):
        ax.bar(i, p, width=0.5, color=BLUE, zorder=3)
        ax.errorbar(i, p, yerr=[[p - lo], [hi - p]], color=INK, capsize=4,
                    linewidth=1.2, zorder=4)
        ax.plot([i - 0.32, i + 0.32], [ch, ch], color=ORANGE, linestyle=(0, (4, 3)),
                linewidth=1.6, zorder=5)
        label(ax, i, hi, f"{p:.3f}")
        ax.text(i + 0.34, ch, f"chance\n{ch:.3f}", color=ORANGE, fontsize=8.5,
                va="center", ha="left")
    ax.set_xticks(range(len(axes_)))
    ax.set_xticklabels([a[0] for a in axes_], fontsize=9.5)
    ax.set_ylim(0, 1.12)
    ax.set_xlim(-0.6, len(axes_) - 0.2)
    ax.set_ylabel("Balanced accuracy (out-of-fold)")
    ax.set_title("Which stack is browsing? Attribution on three axes", loc="left",
                 fontsize=13, fontweight="bold")
    fig.text(0.01, -0.04,
             "Random forest, 95% bootstrap CI. Harness bar: one-sided rule-of-three "
             "bound (>= 0.984). Every permutation p = 0.0050 = the B=200 floor, "
             "not a measured value.", fontsize=8.5, color=MUTED)
    save(fig, "fig2_three_axes")


# ---------------------------------------------------------------- Figure 3 ---
# RESULTS.md section 3.1: how much task diversity each axis needs.
def fig3():
    steps = ["One task\n(single-task mean)", "Unseen task\n(LOTO mean)",
             "All tasks pooled\n(in-distribution)"]
    harness = [0.916, 0.994, 0.995]
    model = [0.576, 0.837, 0.907]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    xs = range(3)
    ax.plot(xs, harness, color=BLUE, linewidth=2, marker="o", markersize=8,
            markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
    ax.plot(xs, model, color=ORANGE, linewidth=2, marker="o", markersize=8,
            markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
    for x, h, m in zip(xs, harness, model):
        ax.text(x, h + 0.02, f"{h:.3f}", color=INK, ha="center", fontsize=10)
        ax.text(x, m - 0.055, f"{m:.3f}", color=INK, ha="center", fontsize=10)
    ax.axhline(0.5, color=MUTED, linestyle=(0, (4, 3)), linewidth=1.2)
    ax.text(-0.15, 0.47, "model chance 0.500", color=MUTED, fontsize=8.5, va="top")
    ax.text(2.2, harness[-1], "Harness\n(+0.078 from pooling)", color=BLUE,
            fontsize=10, va="center", fontweight="bold")
    ax.text(2.2, model[-1], "Model\n(+0.261 from pooling)", color=ORANGE,
            fontsize=10, va="center", fontweight="bold")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(steps, fontsize=9.5)
    ax.set_xlim(-0.25, 3.1)
    ax.set_ylim(0.4, 1.08)
    ax.set_ylabel("Balanced accuracy")
    ax.set_title("Task diversity helps in proportion to how subtle the distinction is",
                 loc="left", fontsize=13, fontweight="bold")
    save(fig, "fig3_loto_contrast")


# ---------------------------------------------------------------- Figure 4 ---
# RESULTS.md section 5.2: what survives when the client is not trusted.
def fig4():
    names = ["Harness", "Model", "Perception\n(3-class)", "Mechanism\n(dom vs vision)"]
    chance = [0.250, 0.500, 0.333, 0.500]
    client = [1.000, 0.895, 0.860, 0.879]
    srv_all = [1.000, 0.859, 0.869, 0.890]
    srv_time = [0.994, 0.766, 0.702, 0.825]
    series = [("Client clock, all features", client, BLUE),
              ("Server clock, all features", srv_all, ORANGE),
              ("Server clock, timing only", srv_time, AQUA)]
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    w = 0.25
    for k, (nm, vals, col) in enumerate(series):
        xs = [i + (k - 1) * w for i in range(4)]
        ax.bar(xs, vals, width=w - 0.03, color=col, label=nm, zorder=3)
        for x, v in zip(xs, vals):
            ax.text(x, v + 0.012, f"{v:.3f}", ha="center", fontsize=8.5, color=INK)
    for i, ch in enumerate(chance):
        ax.plot([i - 0.42, i + 0.42], [ch, ch], color=MUTED, linestyle=(0, (4, 3)),
                linewidth=1.3, zorder=4)
        ax.text(i + 0.43, ch, f"{ch:.3f}", fontsize=8, color=MUTED, va="center")
    ax.set_xticks(range(4))
    ax.set_xticklabels(names)
    ax.set_xlim(-0.6, 3.75)
    ax.set_ylim(0, 1.14)
    ax.set_ylabel("Balanced accuracy")
    ax.legend(loc="upper right", ncols=3, frameon=False, fontsize=9,
              bbox_to_anchor=(1.0, 1.0), columnspacing=1.0)
    ax.set_title("An attacker who forges every client value is still attributed",
                 loc="left", fontsize=13, fontweight="bold", pad=24)
    fig.text(0.01, -0.04, "Dashed ticks: chance for that axis. Last bar of each "
             "group is the security claim: only server-anchored timing is used.",
             fontsize=8.5, color=MUTED)
    save(fig, "fig4_client_vs_server_trust")


# ---------------------------------------------------------------- Figure 6 ---
# RESULTS.md section 4.3: PRIMARY crossed 2x2 run (timeouts ungated, n=210).
# Shares of PC1, which itself explains 26.0% of total feature variance.
def fig6():
    terms = [("Instruction\ncondition", 0.480, 0.276, 0.629),
             ("Residual", 0.474, 0.284, 0.570),
             ("Harness", 0.064, 0.019, 0.151),
             ("Model", 0.012, 0.001, 0.125),
             ("Harness x\nModel", 0.007, 0.000, 0.043)]
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    for i, (nm, p, lo, hi) in enumerate(terms):
        col = ORANGE if "x" in nm else BLUE
        ax.bar(i, p, width=0.5, color=col, zorder=3)
        ax.errorbar(i, p, yerr=[[p - lo], [hi - p]], color=INK, capsize=4,
                    linewidth=1.2, zorder=4)
        label(ax, i, hi, f"{p:.3f}\n[{lo:.3f}, {hi:.3f}]", dy=0.01)
    ax.set_xticks(range(len(terms)))
    ax.set_xticklabels([t[0] for t in terms])
    ax.set_ylim(0, 0.78)
    ax.set_ylabel("Share of PC1 variance (Type-II SS)")
    ax.set_title("The axes behave additively: the interaction is bounded near zero",
                 loc="left", fontsize=13, fontweight="bold")
    fig.text(0.01, -0.07,
             "n = 210, crossed 2x2 (2 harnesses x 2 models); PC1 = 26.0% of total "
             "variance. Whiskers: 95% CI. The interaction's upper bound (0.043) "
             "exceeds the model main effect (0.012): a bound, not a proof of "
             "independence.", fontsize=8.5, color=MUTED, wrap=True)
    save(fig, "fig6_variance_decomposition")


# ---------------------------------------------------------------- Figure 7 ---
# RESULTS.md section 6: detection under a false-positive budget, per class.
def fig7():
    rows = [  # label, TPR@5%, TPR@10%, group colour
        ("llm_driven", 1.000, 1.000, BLUE), ("scripted_humanlike", 1.000, 1.000, BLUE),
        ("scripted_noisy", 1.000, 1.000, BLUE), ("scripted_plain", 1.000, 1.000, BLUE),
        ("llama-3.2-11b", 0.829, 0.878, ORANGE), ("gpt-oss-20b", 0.644, 0.867, ORANGE),
        ("percept_som", 0.894, 0.957, AQUA), ("percept_vision", 0.854, 0.896, AQUA),
        ("percept_dom", 0.628, 0.814, AQUA),
    ]
    fig, ax = plt.subplots(figsize=(9.5, 5))
    for i, (nm, t5, t10, col) in enumerate(rows):
        ax.bar(i - 0.19, t5, width=0.34, color=col, zorder=3)
        ax.bar(i + 0.19, t10, width=0.34, color=col, alpha=0.45, zorder=3)
        ax.text(i - 0.19, t5 + 0.012, f"{t5:.2f}", ha="center", fontsize=8.5)
        ax.text(i + 0.19, t10 + 0.012, f"{t10:.2f}", ha="center", fontsize=8.5)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([r[0] for r in rows], rotation=30, ha="right", fontsize=9)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("True-positive rate (one-vs-rest)")
    ax.set_title("Harness attribution survives a false-positive budget; model attribution does not",
                 loc="left", fontsize=12.5, fontweight="bold")
    for x, t, c in [(1.5, "Harness", BLUE), (4.5, "Model", ORANGE), (7, "Perception", AQUA)]:
        ax.text(x, 1.065, t, color=c, ha="center", fontweight="bold", fontsize=11)
    fig.text(0.01, -0.21,
             "Solid bar: TPR at FPR 5%. Faded bar: TPR at FPR 10%. At the ~2% FPR "
             "the data can resolve, model attribution catches 62-71% (gpt-oss "
             "0.622, llama 0.707). FPR 0.1% is not estimable at n_neg = 41-147.",
             fontsize=8.5, color=MUTED, wrap=True)
    save(fig, "fig7_tpr_at_fpr")


if __name__ == "__main__":
    for f in (fig2, fig3, fig4, fig6, fig7):
        f()
