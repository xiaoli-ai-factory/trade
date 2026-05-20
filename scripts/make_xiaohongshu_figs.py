#!/usr/bin/env python3
"""Generate Xiaohongshu-style infographic PNGs for the S1 Gate1 report."""

from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle
from matplotlib import font_manager


# SOURCE: reports/s1_gate1.md
# - lines 3-4: fixed s1_tail rules and PIT cutoff.
# - lines 9-11: signal span, 1423 dates, 155 delisted symbols in prefilter.
# - lines 19-22: regime return/trades/profit_factor/win_rate.
# - lines 35-36: in-sample and OOS summary.
# - lines 41-56: S1/random/prefilter control-group returns and trades.
# - lines 72-88: Gate1 verdict rows.
# Viral claim overlays are from the user brief and are labeled as "网传", not
# as experiment output.

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "figs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H, DPI = 10.8, 13.5, 100

RED = "#FF2442"
YELLOW = "#FFD93D"
MINT = "#06D6A0"
INK = "#1B1B1B"
WHITE = "#FFFFFF"
BLUE = "#3B82F6"
GRAY = "#F4F4F5"
MID_GRAY = "#737373"
LIGHT_RED = "#FFF0F3"
LIGHT_YELLOW = "#FFF8D9"
LIGHT_MINT = "#E9FFF8"


DATA = {
    "span": "2020-07-01..2026-05-15",
    "trade_days": 1423,
    "delisted_symbols": 155,
    "prefilter_rows": 319181,
    "symbols": 4803,
    "regimes": ["bull", "bear", "range", "oos"],
    "regime_cn": ["牛市", "熊市", "震荡", "样本外"],
    "returns": [-2.12, -6.18, -6.11, -7.52],
    "trades": [3, 5, 9, 19],
    "win_rates": [0.00, 0.00, 11.11, 31.58],
    "profit_factors": [0.0000, 0.0000, 0.0095, 0.2026],
    "random_returns": [-2.12, -6.18, -6.11, -7.52],
    "prefilter_returns": [-72.22, -85.20, -95.40, -90.99],
}

TOTAL_TRADES = sum(DATA["trades"])
TOTAL_WINS = 7
TOTAL_WIN_RATE = TOTAL_WINS / TOTAL_TRADES * 100
assert TOTAL_TRADES == 36

VIRAL = {
    "daily_return": 1.45,
    "winrate_claim": 96.0,
    "bull_probability_claim": 70.0,
    "trade_days_claim": DATA["trade_days"],
}

GATE_ROWS = [
    ("A/bull", "expectancy_after_cost", "-7073.3636", "0.0000", "FAIL"),
    ("A/bull", "profit_factor", "0.0000", "1.3000", "FAIL"),
    ("A/bull", "max_drawdown", "0.0212", "0.2000", "PASS"),
    ("A/bear", "expectancy_after_cost", "-12354.6665", "0.0000", "FAIL"),
    ("A/bear", "profit_factor", "0.0000", "1.3000", "FAIL"),
    ("A/bear", "max_drawdown", "0.0618", "0.2000", "PASS"),
    ("A/range", "expectancy_after_cost", "-6789.8181", "0.0000", "FAIL"),
    ("A/range", "profit_factor", "0.0095", "1.3000", "FAIL"),
    ("A/range", "max_drawdown", "0.0677", "0.2000", "PASS"),
    ("B/merged", "trades", "36.0000", "200.0000", "FAIL"),
    ("B/merged", "expectancy_after_cost", "-6091.5778", "0.0000", "FAIL"),
    ("B/merged", "profit_factor", "0.0824", "1.3000", "FAIL"),
    ("C/oos", "expectancy_after_cost", "-3957.6324", "0.0000", "FAIL"),
    ("C/oos", "profit_factor", "0.2026", "1.3000", "FAIL"),
    ("C/oos", "max_drawdown", "0.0814", "0.2000", "PASS"),
    ("C/oos", "trades", "19.0000", "60.0000", "FAIL"),
    ("TOTAL", "A+B+C", "-", "-", "FAIL"),
]


def setup_fonts() -> None:
    font_candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]
    for candidate in font_candidates:
        path = Path(candidate)
        if path.exists():
            font_manager.fontManager.addfont(str(path))
    mpl.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Microsoft YaHei",
        "PingFang SC",
        "DejaVu Sans",
    ]
    mpl.rcParams["axes.unicode_minus"] = False


setup_fonts()


def new_canvas():
    fig = plt.figure(figsize=(W, H), dpi=DPI, facecolor=WHITE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


def save(fig, name: str) -> Path:
    path = OUT_DIR / name
    fig.savefig(path, dpi=DPI, facecolor=WHITE)
    plt.close(fig)
    return path


def rounded(ax, x, y, w, h, fc, ec="none", lw=1.5, r=0.035, alpha=1.0, z=1):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0.012,rounding_size={r}",
        linewidth=lw,
        facecolor=fc,
        edgecolor=ec,
        alpha=alpha,
        transform=ax.transAxes,
        zorder=z,
    )
    ax.add_patch(patch)
    return patch


def label(ax, x, y, s, size=32, color=INK, weight="normal", ha="left", va="center", **kw):
    return ax.text(
        x,
        y,
        s,
        transform=ax.transAxes,
        ha=ha,
        va=va,
        fontsize=size,
        color=color,
        fontweight=weight,
        linespacing=1.12,
        **kw,
    )


def pill(ax, x, y, text, fc, color=INK, size=24, w=None):
    width = w if w is not None else max(0.18, len(text) * 0.018)
    rounded(ax, x, y - 0.03, width, 0.06, fc=fc, r=0.03)
    label(ax, x + width / 2, y, text, size=size, color=color, weight="bold", ha="center")


def source_note(ax, text):
    rounded(ax, 0.07, 0.035, 0.86, 0.05, fc=GRAY, r=0.02)
    label(ax, 0.5, 0.06, text, size=17, color=MID_GRAY, ha="center")


def fig1_cover():
    fig, ax = new_canvas()
    rounded(ax, 0.055, 0.055, 0.89, 0.89, fc=WHITE, ec="#F0F0F0", lw=2, r=0.04)
    rounded(ax, 0.055, 0.69, 0.89, 0.255, fc=LIGHT_RED, r=0.04)
    pill(ax, 0.11, 0.885, "AI 量化实测·非营销", RED, WHITE, size=25, w=0.34)

    label(ax, 0.11, 0.785, "杨永兴尾盘法", size=66, weight="bold")
    label(ax, 0.11, 0.69, "实测翻车", size=106, color=RED, weight="black")
    label(ax, 0.11, 0.595, "1423天 / 36笔 / 全段亏", size=45, weight="bold")
    label(ax, 0.11, 0.535, "BaoStock 5min PIT 逐行守住无未来函数", size=25, color=MID_GRAY)

    stats = [
        ("实验区间", DATA["span"]),
        ("触发次数", "bull 3 / bear 5 / range 9 / oos 19"),
        ("反幸存者", f"含退市股 {DATA['delisted_symbols']} 只"),
        ("最终裁决", "Gate1 = FAIL"),
    ]
    y0 = 0.41
    for i, (k, v) in enumerate(stats):
        y = y0 - i * 0.09
        rounded(ax, 0.11, y - 0.035, 0.78, 0.07, fc=GRAY if i != 3 else LIGHT_YELLOW, r=0.025)
        label(ax, 0.15, y, k, size=25, color=MID_GRAY, weight="bold")
        label(ax, 0.86, y, v, size=25 if i != 1 else 22, color=RED if i == 3 else INK, weight="bold", ha="right")

    source_note(ax, "数据源：reports/s1_gate1.md lines 3-22, 72-90")
    return save(fig, "fig1_cover.png")


def fig2_return_by_regime():
    fig, ax = new_canvas()
    label(ax, 0.07, 0.93, "实测收益：四段全在水下", size=46, weight="bold")
    label(ax, 0.07, 0.885, "网传日均 +1.45% 放在同一张图里看，反差很直接", size=24, color=MID_GRAY)

    chart = fig.add_axes([0.11, 0.31, 0.82, 0.47])
    x = range(len(DATA["regime_cn"]))
    bars = chart.bar(x, DATA["returns"], color=RED, width=0.58, edgecolor="none")
    chart.axhline(0, color=INK, lw=2)
    chart.axhline(VIRAL["daily_return"], color=YELLOW, lw=4, linestyle=(0, (9, 6)))
    chart.text(
        3.45,
        VIRAL["daily_return"] + 0.15,
        "网传日均 +1.45%",
        color=INK,
        fontsize=22,
        fontweight="bold",
        ha="right",
        va="bottom",
    )
    chart.set_ylim(-9.2, 2.4)
    chart.set_xlim(-0.55, 3.55)
    chart.set_xticks(list(x), [f"{cn}\n{en}" for cn, en in zip(DATA["regime_cn"], DATA["regimes"])], fontsize=24, fontweight="bold")
    chart.set_yticks([-8, -6, -4, -2, 0, 1.45], ["-8%", "-6%", "-4%", "-2%", "0", "+1.45%"], fontsize=18)
    chart.grid(axis="y", color="#E8E8E8", linewidth=1.3)
    chart.set_axisbelow(True)
    for spine in chart.spines.values():
        spine.set_visible(False)
    for bar, value in zip(bars, DATA["returns"]):
        chart.text(
            bar.get_x() + bar.get_width() / 2,
            value - 0.35,
            f"{value:.2f}%",
            ha="center",
            va="top",
            fontsize=26,
            color=RED,
            fontweight="bold",
        )

    rounded(ax, 0.09, 0.105, 0.82, 0.07, fc=LIGHT_RED, r=0.025)
    label(ax, 0.5, 0.14, "结论：bull / bear / range / oos 没有一段转正", size=24, color=RED, weight="bold", ha="center")
    source_note(ax, "数据源：reports/s1_gate1.md lines 19-22；网传对比来自任务说明")
    return save(fig, "fig2_return_by_regime.png")


def fig3_winrate_compare():
    fig, ax = new_canvas()
    label(ax, 0.07, 0.93, "胜率神话 vs 实测胜率", size=48, weight="bold")
    label(ax, 0.07, 0.885, "96% / 70% 的话术，落到这次 PIT 实测只有 7/36", size=24, color=MID_GRAY)

    chart = fig.add_axes([0.28, 0.27, 0.65, 0.5])
    names = ["网传 96%", "牛市 >70%", "实测 7/36"]
    values = [VIRAL["winrate_claim"], VIRAL["bull_probability_claim"], TOTAL_WIN_RATE]
    colors = [BLUE, BLUE, RED]
    y = [2, 1, 0]
    bars = chart.barh(y, values, color=colors, height=0.5, edgecolor="none")
    chart.set_xlim(0, 100)
    chart.set_yticks(y, names, fontsize=25, fontweight="bold")
    chart.set_xticks([0, 20, 40, 60, 80, 100], ["0", "20%", "40%", "60%", "80%", "100%"], fontsize=18)
    chart.grid(axis="x", color="#E8E8E8", linewidth=1.3)
    chart.set_axisbelow(True)
    for spine in chart.spines.values():
        spine.set_visible(False)
    for bar, value, color in zip(bars, values, colors):
        chart.text(
            value + 2 if value < 88 else value - 3,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.1f}%",
            ha="left" if value < 88 else "right",
            va="center",
            fontsize=30,
            color=color if value < 88 else WHITE,
            fontweight="black",
        )

    rounded(ax, 0.12, 0.105, 0.76, 0.075, fc=LIGHT_RED, r=0.03)
    label(ax, 0.5, 0.143, "实测总胜率 ≈ 19.4%，不是“高胜率提款机”", size=25, color=RED, weight="bold", ha="center")
    source_note(ax, "数据源：reports/s1_gate1.md lines 19-22；7/36 为分段胜率与笔数还原")
    return save(fig, "fig3_winrate_compare.png")


def fig4_trades_frequency():
    fig, ax = new_canvas()
    label(ax, 0.07, 0.93, "频率先塌：不是每天都有戏", size=47, weight="bold")
    label(ax, 0.07, 0.885, "1423 个交易日里，固定规则只触发 36 笔", size=25, color=MID_GRAY)

    chart = fig.add_axes([0.14, 0.31, 0.74, 0.47])
    names = ["网传：每天可做", "实测：S1触发"]
    values = [VIRAL["trade_days_claim"], TOTAL_TRADES]
    colors = [YELLOW, RED]
    bars = chart.bar(names, values, color=colors, width=0.48, edgecolor="none")
    chart.set_ylim(0, 1520)
    chart.set_yticks([0, 300, 600, 900, 1200, 1423], ["0", "300", "600", "900", "1200", "1423天"], fontsize=18)
    chart.tick_params(axis="x", labelsize=25)
    chart.grid(axis="y", color="#E8E8E8", linewidth=1.3)
    chart.set_axisbelow(True)
    for spine in chart.spines.values():
        spine.set_visible(False)
    for bar, value, color in zip(bars, values, colors):
        chart.text(
            bar.get_x() + bar.get_width() / 2,
            value + 35,
            f"{value}",
            ha="center",
            va="bottom",
            fontsize=42 if value > 100 else 38,
            color=INK if color == YELLOW else RED,
            fontweight="black",
        )

    rounded(ax, 0.105, 0.115, 0.79, 0.075, fc=LIGHT_YELLOW, r=0.03)
    label(ax, 0.5, 0.153, "分段触发：bull 3 / bear 5 / range 9 / oos 19", size=25, color=INK, weight="bold", ha="center")
    source_note(ax, "数据源：reports/s1_gate1.md lines 10, 19-22")
    return save(fig, "fig4_trades_frequency.png")


def draw_return_panel(ax, x, y, w, h, title, values, fc, title_color):
    rounded(ax, x, y, w, h, fc=fc, r=0.03)
    label(ax, x + w / 2, y + h - 0.055, title, size=25, color=title_color, weight="bold", ha="center")
    inner_left = x + 0.055
    zero_x = x + w - 0.045
    scale_w = zero_x - inner_left
    row_gap = (h - 0.16) / 4
    for i, (name, value) in enumerate(zip(DATA["regime_cn"], values)):
        yy = y + h - 0.13 - i * row_gap
        label(ax, inner_left, yy + 0.018, name, size=18, color=MID_GRAY, weight="bold")
        ax.add_patch(Rectangle((inner_left, yy - 0.013), scale_w, 0.026, transform=ax.transAxes, facecolor="#E8E8E8", edgecolor="none"))
        length = min(abs(value) / 100 * scale_w, scale_w)
        ax.add_patch(Rectangle((zero_x - length, yy - 0.013), length, 0.026, transform=ax.transAxes, facecolor=RED, edgecolor="none"))
        label(ax, zero_x, yy + 0.026, f"{value:.2f}%", size=18, color=RED, weight="bold", ha="right")


def fig5_control_group():
    fig, ax = new_canvas()
    label(ax, 0.07, 0.93, "对照组扒穿：S1 没有选股 alpha", size=43, weight="bold")
    label(ax, 0.07, 0.885, "同合格池随机选 2 只，结果与 S1 完全一致", size=25, color=MID_GRAY)

    panel_y, panel_h, panel_w = 0.27, 0.52, 0.275
    xs = [0.065, 0.362, 0.659]
    draw_return_panel(ax, xs[0], panel_y, panel_w, panel_h, "S1 信号", DATA["returns"], LIGHT_RED, RED)
    draw_return_panel(ax, xs[1], panel_y, panel_w, panel_h, "随机2只", DATA["random_returns"], LIGHT_MINT, "#058B68")
    draw_return_panel(ax, xs[2], panel_y, panel_w, panel_h, "预筛池等权", DATA["prefilter_returns"], LIGHT_YELLOW, INK)

    rounded(ax, 0.08, 0.145, 0.84, 0.085, fc=LIGHT_RED, r=0.03)
    label(ax, 0.5, 0.188, "S1 / 随机 = 1.0000；真正危险的是“尾盘追涨停隔夜”这个池", size=23, color=RED, weight="bold", ha="center")
    source_note(ax, "数据源：reports/s1_gate1.md lines 41-56, 62")
    return save(fig, "fig5_control_group.png")


def status_icon(ax, x, y, status):
    color = RED if status == "FAIL" else MINT
    ax.add_patch(Circle((x, y), 0.016, transform=ax.transAxes, facecolor=color, edgecolor="none", zorder=5))
    glyph = "×" if status == "FAIL" else "✓"
    label(ax, x, y - 0.001, glyph, size=18, color=WHITE, weight="black", ha="center", va="center", zorder=6)


def fig6_gate1_verdict():
    fig, ax = new_canvas()
    fail_count = sum(1 for row in GATE_ROWS if row[-1] == "FAIL")
    pass_count = sum(1 for row in GATE_ROWS if row[-1] == "PASS")
    label(ax, 0.07, 0.93, "Gate1 裁决：FAIL", size=54, color=RED, weight="black")
    label(ax, 0.07, 0.885, f"报告原表：{fail_count} 个 FAIL / {pass_count} 个 PASS，PASS 主要是回撤阈值未触发", size=22, color=MID_GRAY)

    x0, y0, w, h = 0.055, 0.19, 0.89, 0.64
    rounded(ax, x0, y0, w, h, fc=WHITE, ec="#EFEFEF", lw=2, r=0.03)
    header_y = y0 + h - 0.035
    label(ax, x0 + 0.045, header_y, "判定", size=17, color=MID_GRAY, weight="bold")
    label(ax, x0 + 0.135, header_y, "组别", size=17, color=MID_GRAY, weight="bold")
    label(ax, x0 + 0.29, header_y, "指标", size=17, color=MID_GRAY, weight="bold")
    label(ax, x0 + 0.63, header_y, "实际 / 阈值", size=17, color=MID_GRAY, weight="bold")

    row_top = y0 + h - 0.072
    row_h = 0.033
    for i, (group, metric, actual, threshold, status) in enumerate(GATE_ROWS):
        yy = row_top - i * row_h
        bg = LIGHT_RED if status == "FAIL" else LIGHT_MINT
        if group == "TOTAL":
            bg = RED
        rounded(ax, x0 + 0.018, yy - 0.014, w - 0.036, 0.026, fc=bg, r=0.012)
        status_icon(ax, x0 + 0.055, yy, status)
        text_color = WHITE if group == "TOTAL" else INK
        metric_color = WHITE if group == "TOTAL" else MID_GRAY
        label(ax, x0 + 0.105, yy, group, size=15, color=text_color, weight="bold")
        label(ax, x0 + 0.255, yy, metric, size=14, color=metric_color, weight="bold")
        label(ax, x0 + 0.865, yy, f"{actual} / {threshold}", size=14, color=text_color, weight="bold", ha="right")

    rounded(ax, 0.11, 0.085, 0.78, 0.075, fc=RED, r=0.03)
    label(ax, 0.5, 0.122, "最终判定：FAIL", size=38, color=WHITE, weight="black", ha="center")
    source_note(ax, "数据源：reports/s1_gate1.md lines 72-90；使用报告原表行")
    return save(fig, "fig6_gate1_verdict.png")


def main():
    paths = [
        fig1_cover(),
        fig2_return_by_regime(),
        fig3_winrate_compare(),
        fig4_trades_frequency(),
        fig5_control_group(),
        fig6_gate1_verdict(),
    ]
    print("Generated:")
    for path in paths:
        print(f"- {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
