# SOURCE: backtest/personal_div_ndx_eval.py 输出 (持有胜率/逢低vs定投) + personal_div_ndx_charts.py (当前位置)
# SOURCE: QDII 溢价 = 2026-05 华夏/富国/招商/嘉实/华泰柏瑞 等纳指100 QDII 溢价风险提示公告 (sse.com.cn 等)
"""小红书「避坑帖」5 张图 (1080×1350)。数据全部来自已验证脚本/公开公告，禁伪造。"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

OUT = Path(__file__).resolve().parents[1] / "reports" / "figs" / "div_ndx"
OUT.mkdir(parents=True, exist_ok=True)

for cand in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"):
    if Path(cand).exists():
        fm.fontManager.addfont(cand)
        plt.rcParams["font.family"] = fm.FontProperties(fname=cand).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

RED, YEL, MINT = "#FF2442", "#FFD93D", "#06D6A0"
INK, GRAY = "#1B1B1B", "#666666"
SOFT_RED, SOFT_YEL, SOFT_MINT = "#FFF0F3", "#FFF8D9", "#E9FFF8"
W, H = 10.8, 13.5  # 1080×1350 @dpi100


def canvas(bg="#FFFFFF"):
    fig = plt.figure(figsize=(W, H), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1]); ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.add_patch(plt.Rectangle((0, 0), 1, 1, color=bg, zorder=-10))
    return fig, ax


def card(ax, x, y, w, h, fc, ec="none", lw=0):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.03",
                                fc=fc, ec=ec, lw=lw, mutation_aspect=W/H, zorder=1))


def stamp(ax):
    ax.text(0.5, 0.045, "AI 量化实测 · 非营销 · 数字皆可溯源", ha="center", va="center",
            fontsize=12, color="#fff", weight="bold",
            bbox=dict(boxstyle="round,pad=0.5", fc=RED, ec="none"))


def foot(ax, txt):
    ax.text(0.5, 0.012, txt, ha="center", va="center", fontsize=8.5, color=GRAY)


import re
# Noto CJK 不含彩色 emoji，会渲染成豆腐块 → 统一剔除 (保留 ①②③ ≈ 「」 等 CJK 字形)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def t(ax, x, y, s, fs, color=INK, weight="bold", ha="left", va="center"):
    s = _EMOJI.sub("", s).strip()
    ax.text(x, y, s, fontsize=fs, color=color, weight=weight, ha=ha, va=va)


# ---------- fig1 封面 ----------
fig, ax = canvas(SOFT_RED)
t(ax, 0.5, 0.93, "都在劝你「躺赚」", 30, GRAY, ha="center")
t(ax, 0.5, 0.85, "红利低波 + 纳指100", 40, INK, ha="center")
t(ax, 0.5, 0.775, "我回测后发现 2 个坑", 40, RED, ha="center")
card(ax, 0.1, 0.40, 0.8, 0.30, "#fff")
t(ax, 0.5, 0.645, "我也在定投这套「极简组合」", 22, INK, ha="center")
t(ax, 0.5, 0.585, "全网都说它稳、躺着赚", 19, GRAY, ha="center")
t(ax, 0.5, 0.515, "但用 7 年真实数据跑完", 22, INK, ha="center")
t(ax, 0.5, 0.455, "这 2 个坑差点让我多亏钱 👇", 22, RED, ha="center")
t(ax, 0.5, 0.31, "坑① 「稳赚」是假象", 26, INK, ha="center")
t(ax, 0.5, 0.245, "坑② 现在买纳指要看「溢价」", 26, INK, ha="center")
t(ax, 0.5, 0.16, "（附数据 · 普通人也能看懂）", 18, GRAY, ha="center")
stamp(ax)
foot(ax, "数据源：自有回测 backtest/personal_div_ndx_eval.py · 2019–2026 前复权")
fig.savefig(OUT / "fig1_cover.png", dpi=100); plt.close(fig)

# ---------- fig2 坑1 ----------
fig, ax = canvas("#fff")
t(ax, 0.5, 0.945, "坑 ①", 30, "#fff", ha="center")
ax.add_patch(plt.Rectangle((0.38, 0.915), 0.24, 0.06, color=RED, zorder=0))
t(ax, 0.5, 0.86, "「持有2年100%赚」别当真", 27, INK, ha="center")
# 胜率柱
card(ax, 0.08, 0.50, 0.84, 0.31, SOFT_MINT)
bars = [("持有1年", 91), ("持有2年", 100), ("持有3年", 100)]
bx = [0.22, 0.5, 0.78]
for (lab, v), cx in zip(bars, bx):
    bh = 0.18 * v / 100
    ax.add_patch(plt.Rectangle((cx - 0.085, 0.55), 0.17, bh, color=MINT, zorder=2))
    t(ax, cx, 0.55 + bh + 0.018, f"{v}%", 24, RED, ha="center")
    t(ax, cx, 0.525, lab, 16, INK, ha="center")
t(ax, 0.5, 0.77, "回测胜率（任意时点买入）", 18, GRAY, ha="center")
card(ax, 0.08, 0.16, 0.84, 0.30, SOFT_YEL)
t(ax, 0.5, 0.415, "⚠️ 但数据只有 7 年、还全是牛市", 21, INK, ha="center")
t(ax, 0.5, 0.355, "「独立的 3 年时间段」只有 2–3 个", 19, RED, ha="center")
t(ax, 0.5, 0.285, "意思：过去没遇到过长期大熊市", 19, INK, ha="center")
t(ax, 0.5, 0.225, "≠ 未来一定 100% 赚", 22, RED, ha="center")
t(ax, 0.5, 0.185, "「长期大概率赚」可以，「稳赚」不行", 16, GRAY, ha="center")
foot(ax, "数据源：backtest/personal_div_ndx_eval.py 持有期胜率统计 · 红利低波512890+纳指100 513100")
fig.savefig(OUT / "fig2_pit1.png", dpi=100); plt.close(fig)

# ---------- fig3 坑2 (蹭热点) ----------
fig, ax = canvas("#fff")
t(ax, 0.5, 0.945, "坑 ②", 30, "#fff", ha="center")
ax.add_patch(plt.Rectangle((0.38, 0.915), 0.24, 0.06, color=RED, zorder=0))
t(ax, 0.5, 0.86, "现在买纳指100，先看「溢价」", 25, INK, ha="center")
card(ax, 0.08, 0.55, 0.84, 0.27, SOFT_RED)
t(ax, 0.5, 0.775, "🔥 就在 2026 年 5 月", 22, INK, ha="center")
t(ax, 0.5, 0.71, "华夏 / 富国 / 招商 / 嘉实 / 华泰柏瑞", 19, INK, ha="center")
t(ax, 0.5, 0.655, "多只纳指100 QDII 接连发", 19, INK, ha="center")
t(ax, 0.5, 0.595, "「溢价风险提示公告」溢价率 10%+", 21, RED, ha="center")
card(ax, 0.08, 0.20, 0.84, 0.31, SOFT_YEL)
t(ax, 0.5, 0.465, "溢价 = 场内价比真实净值贵的部分", 19, INK, ha="center")
t(ax, 0.5, 0.40, "买在高溢价 = 多付一笔「智商税」", 21, RED, ha="center")
t(ax, 0.5, 0.335, "将来溢价回落，会吃掉你的收益", 19, INK, ha="center")
t(ax, 0.5, 0.26, "✅ 买前看当天溢价率", 21, INK, ha="center")
t(ax, 0.5, 0.21, ">3~5% 就少买 / 换一只额度充足的", 18, GRAY, ha="center")
foot(ax, "数据源：2026-05 各基金公司纳指100 QDII 溢价风险提示公告 (sse.com.cn 等公开披露)")
fig.savefig(OUT / "fig3_pit2.png", dpi=100); plt.close(fig)

# ---------- fig4 正确做法 ----------
fig, ax = canvas("#fff")
t(ax, 0.5, 0.94, "那到底怎么买？", 30, INK, ha="center")
card(ax, 0.08, 0.58, 0.84, 0.27, SOFT_MINT)
t(ax, 0.5, 0.79, "真相：逢低买 ≈ 无脑定投", 23, INK, ha="center")
t(ax, 0.28, 0.71, "1.86x", 26, INK, ha="center"); t(ax, 0.28, 0.66, "每月定投", 16, GRAY, ha="center")
t(ax, 0.5, 0.685, "≈", 30, GRAY, ha="center")
t(ax, 0.72, 0.71, "1.92x", 26, MINT, ha="center"); t(ax, 0.72, 0.66, "跌10%才买", 16, GRAY, ha="center")
t(ax, 0.5, 0.605, "差距很小，别神化「抄底」", 18, RED, ha="center")
t(ax, 0.5, 0.53, "❌ 别攒钱赌「更大的跌」", 20, INK, ha="center")
t(ax, 0.5, 0.485, "跌20%才买那种：15%的钱一年没投出去、踏空了", 15, GRAY, ha="center")
card(ax, 0.08, 0.10, 0.84, 0.33, "#fff", ec=RED, lw=2)
t(ax, 0.5, 0.40, "✅ 记住 3 步", 22, RED, ha="center")
t(ax, 0.12, 0.33, "① 定投打底：每月固定一笔，50/50，别停别择时", 17, INK)
t(ax, 0.12, 0.255, "② 大跌加码：跌超10%再额外加，买跌更深的那个", 17, INK)
t(ax, 0.12, 0.18, "③ 买纳指看溢价，长期持有、平时别瞎操作", 17, INK)
foot(ax, "数据源：backtest/personal_div_ndx_eval.py 逢低买 vs 定投 同节奏现金流回测")
fig.savefig(OUT / "fig4_howto.png", dpi=100); plt.close(fig)

# ---------- fig5 总结 + 作者卡 ----------
fig, ax = canvas(SOFT_RED)
t(ax, 0.5, 0.93, "一句话总结", 30, INK, ha="center")
card(ax, 0.08, 0.55, 0.84, 0.31, "#fff")
t(ax, 0.5, 0.80, "时间是朋友，但别当「稳赚」", 21, INK, ha="center")
t(ax, 0.5, 0.735, "持有1年 91% 赚，但样本只有7年牛市", 16, GRAY, ha="center")
t(ax, 0.5, 0.665, "买纳指先看溢价，别多交智商税", 21, INK, ha="center")
t(ax, 0.5, 0.60, "2026年5月多只QDII正溢价10%+", 16, GRAY, ha="center")
card(ax, 0.08, 0.16, 0.84, 0.33, "#fff", ec=RED, lw=2)
t(ax, 0.5, 0.44, "关注我，能得到 👇", 22, RED, ha="center")
t(ax, 0.12, 0.375, "· 每周用真实数据扒一个「理财神话」", 16, INK)
t(ax, 0.12, 0.315, "· 普通人能看懂的量化避坑（不卖课不荐股）", 16, INK)
t(ax, 0.12, 0.255, "· 回测代码开源，数字都能自己验", 16, INK)
t(ax, 0.5, 0.195, "💡 全网同名 · 评论区扣「数据」我私信你", 17, RED, ha="center")
stamp(ax)
foot(ax, "数据源：自有回测，非投资建议 · 历史不代表未来")
fig.savefig(OUT / "fig5_verdict.png", dpi=100); plt.close(fig)

print("saved 5 xhs figures to", OUT)
for p in sorted(OUT.glob("fig*.png")):
    print(" ", p.name)
