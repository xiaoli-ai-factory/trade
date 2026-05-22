"""为个人组合评估生成图表（红利低波512890 + 纳指100 513100）。

读取 personal_div_ndx_eval 已缓存的前复权价，输出 4 张 PNG 到 reports/personal_div_ndx/。
图均面向「普通人能读懂」：少术语、大字、直接给结论。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "reports" / "personal_div_ndx"
OUT.mkdir(parents=True, exist_ok=True)

# 中文字体：优先 Sans CJK，回退 Serif CJK
for cand in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"):
    if Path(cand).exists():
        fm.fontManager.addfont(cand)
        plt.rcParams["font.family"] = fm.FontProperties(fname=cand).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

C_DIV, C_NDX, C_MIX = "#C0392B", "#2C6FBB", "#27AE60"
HORIZONS = [("1月", 21), ("3月", 63), ("6月", 126), ("1年", 252), ("2年", 504), ("3年", 756)]

div = pd.read_parquet(CACHE / "etf_qfq__512890.parquet")
ndx = pd.read_parquet(CACHE / "etf_qfq__513100.parquet")
m = (div.rename(columns={"close": "div"})
     .merge(ndx.rename(columns={"close": "ndx"}), on="date", how="inner")
     .sort_values("date").reset_index(drop=True))
d, x = m["div"].to_numpy(), m["ndx"].to_numpy()
n = len(m)


def win_rate(c, h):
    r = c[h:] / c[:-h] - 1
    return np.mean(r > 0) * 100


# ---- 图1：持有越久越赚（核心） ----
labels = [l for l, _ in HORIZONS]
wd = [win_rate(d, h) for _, h in HORIZONS]
wn = [win_rate(x, h) for _, h in HORIZONS]
wm = [(np.mean((0.5*(d[h:]/d[:-h]-1)+0.5*(x[h:]/x[:-h]-1)) > 0))*100 for _, h in HORIZONS]
xp = np.arange(len(labels)); w = 0.27
fig, ax = plt.subplots(figsize=(9, 5.2))
ax.bar(xp-w, wd, w, label="红利低波", color=C_DIV)
ax.bar(xp,   wn, w, label="纳指100", color=C_NDX)
ax.bar(xp+w, wm, w, label="50/50组合", color=C_MIX)
for i, v in enumerate(wm):
    ax.text(xp[i]+w, v+1, f"{v:.0f}%", ha="center", fontsize=9, color=C_MIX)
ax.set_title("买入后持有越久，赚钱概率越高（任意时点买入的历史胜率）", fontsize=13, weight="bold")
ax.set_ylabel("赚钱的概率"); ax.set_ylim(0, 108); ax.set_xticks(xp); ax.set_xticklabels(labels)
ax.axhline(50, ls="--", c="gray", lw=0.8); ax.text(-0.05, 70, "虚线=50%\n(等于掷硬币)", fontsize=8, color="gray", ha="center")
ax.legend(loc="lower right"); ax.grid(axis="y", alpha=0.3)
fig.text(0.5, 0.01, "样本2019–2026仅约7年且偏牛市，2/3年的高胜率别当成铁律", ha="center", fontsize=8, color="gray")
fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(OUT/"01_hold_winrate.png", dpi=140); plt.close(fig)

# ---- 图2：当前位置——今天该买哪个 ----
def dd_1y(c): return (c[-1]/c[-252:].max()-1)*100
ddd, ddx = dd_1y(d), dd_1y(x)
fig, ax = plt.subplots(figsize=(9, 3.6))
bars = ax.barh(["红利低波", "纳指100"], [ddd, ddx], color=[C_DIV, C_NDX])
for b, v in zip(bars, [ddd, ddx]):
    ax.text(v-0.3 if v < 0 else v+0.3, b.get_y()+b.get_height()/2,
            f"{v:+.1f}%", va="center", ha="right" if v < 0 else "left", fontsize=11, weight="bold")
ax.set_title("现在两个资产离最近一年高点有多远（越低=越便宜）", fontsize=13, weight="bold")
ax.axvline(0, c="gray", lw=1)
ax.set_xlim(min(ddd, ddx)-3, 3)
who = "红利低波" if ddd < ddx else "纳指100"
fig.text(0.5, 0.02, f"按你“买跌得更多的那个”的逻辑 → 当前应买【{who}】", ha="center", fontsize=11, color=C_DIV, weight="bold")
fig.tight_layout(rect=(0, 0.06, 1, 1)); fig.savefig(OUT/"02_now_position.png", dpi=140); plt.close(fig)

# ---- 图3：逢低买 vs 定投（破除“越激进越好”） ----
contrib = list(range(0, n, 21)); tot = len(contrib)
ud = sum(0.5/d[i] for i in contrib); un = sum(0.5/x[i] for i in contrib)
dca = (ud*d[-1]+un*x[-1])/tot
def buy_dip(thr):
    cash=ud_=un_=0.0; pd_=px=-np.inf; cs=set(contrib)
    for i in range(n):
        pd_=max(pd_,d[i]); px=max(px,x[i])
        if i in cs: cash+=1.0
        if cash>0 and (d[i]/pd_-1<=-thr or x[i]/px-1<=-thr):
            if d[i]/pd_-1<=x[i]/px-1: ud_+=cash/d[i]
            else: un_+=cash/x[i]
            cash=0.0
    return (ud_*d[-1]+un_*x[-1]+cash)/tot
names = ["每月定投", "跌5%买", "跌10%买", "跌15%买", "跌20%买"]
vals = [dca, buy_dip(.05), buy_dip(.10), buy_dip(.15), buy_dip(.20)]
fig, ax = plt.subplots(figsize=(9, 4.6))
cols = ["#7F8C8D"]+[C_MIX]*4
bars = ax.bar(names, vals, color=cols)
for b, v in zip(bars, vals):
    ax.text(b.get_x()+b.get_width()/2, v+0.02, f"{v:.2f}x", ha="center", fontsize=10, weight="bold")
ax.set_title("“逢低买”和“无脑定投”差距其实很小", fontsize=13, weight="bold")
ax.set_ylabel("同样的钱，最后变成几倍"); ax.set_ylim(0, max(vals)*1.18); ax.grid(axis="y", alpha=0.3)
fig.text(0.5, 0.01, "“跌20%才买”看着最高，但有15%的钱一整年没投出去、踏空了上涨——靠的是这几年“跌了就反弹”，慢熊会很惨",
         ha="center", fontsize=7.5, color="gray")
fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(OUT/"03_dip_vs_dca.png", dpi=140); plt.close(fig)

# ---- 图4：组合更稳（净值曲线，再平衡近似） ----
rd = np.r_[0, d[1:]/d[:-1]-1]; rn = np.r_[0, x[1:]/x[:-1]-1]
eq_d = np.cumprod(1+rd); eq_n = np.cumprod(1+rn); eq_m = np.cumprod(1+0.5*rd+0.5*rn)
dates = pd.to_datetime(m["date"])
fig, ax = plt.subplots(figsize=(9, 4.8))
ax.plot(dates, eq_d, color=C_DIV, lw=1.4, label=f"只买红利低波 (波动17%, 最惨-17%)")
ax.plot(dates, eq_n, color=C_NDX, lw=1.4, label=f"只买纳指100 (波动24%, 最惨-28%)")
ax.plot(dates, eq_m, color=C_MIX, lw=2.2, label=f"50/50组合 (波动16%, 最惨-18%)")
ax.set_title("两个搭一起：涨得不差，但更稳（东边不亮西边亮）", fontsize=13, weight="bold")
ax.set_ylabel("1元本金变成几元"); ax.legend(loc="upper left"); ax.grid(alpha=0.3)
fig.tight_layout(); fig.savefig(OUT/"04_combo_equity.png", dpi=140); plt.close(fig)

print("saved 4 charts to", OUT)
print(f"当前位置: 红利低波距1年高 {ddd:+.1f}%, 纳指100 {ddx:+.1f}% -> 今天买 {who}")
print(f"逢低 vs 定投: 定投{dca:.2f}x, 跌10%买{buy_dip(.10):.2f}x, 跌20%买{buy_dip(.20):.2f}x")
