"""个人持仓评估：红利低波(512890) + 纳指100(513100)，A 股场内 ETF，前复权。

目标（按用户需求）：
1. 不同持有时间的「赚钱概率」——任意时点买入、持有 N 久后盈利的概率与收益分布。
2. 双资产组合是否合理：相关性 + 分散化降波动效果。
3. 评估「逢低大量买入、平时不动」这一习惯：buy-the-dip vs 定投 vs 一次性，
   在相同现金流时间表下谁更优（如实呈现，不为迎合假设美化）。

口径与诚实声明：
- 前复权(qfq)价，近似「分红再投」的长期总回报。
- 持有期盈利概率是历史 rolling 分布，不是对未来的预测；窗口重叠 → 长持有期独立样本少。
- 组合重叠期仅 2019-01 起（红利低波 ETF 上市日），约 7 年，未覆盖完整牛熊轮回，
  尤其 2/3 年持有期独立窗口极少，统计意义弱，结论需保守。
"""

from __future__ import annotations

import time
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"

ASSETS = {"512890": "红利低波", "513100": "纳指100"}
HORIZONS = [("1月", 21), ("3月", 63), ("6月", 126), ("1年", 252), ("2年", 504), ("3年", 756)]
TRADING_DAYS = 252


def fetch(code: str, adjust: str = "qfq", attempts: int = 6) -> pd.DataFrame:
    path = CACHE / f"etf_qfq__{code}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            raw = ak.fund_etf_hist_em(symbol=code, period="daily", adjust=adjust)
            time.sleep(0.3)
            out = pd.DataFrame(
                {"date": pd.to_datetime(raw["日期"]), "close": pd.to_numeric(raw["收盘"])}
            ).sort_values("date").reset_index(drop=True)
            CACHE.mkdir(parents=True, exist_ok=True)
            out.to_parquet(path, index=False)
            return out
        except Exception as exc:  # 网络/代理偶发断连，指数退避重试
            last = exc
            time.sleep(1.2 * i)
    raise RuntimeError(f"fetch failed {code}: {last}")


def holding_stats(close: np.ndarray, h: int) -> dict | None:
    if len(close) <= h:
        return None
    r = close[h:] / close[:-h] - 1.0
    return {
        "n": int(len(r)),
        "win": float(np.mean(r > 0)),
        "med": float(np.median(r)),
        "mean": float(np.mean(r)),
        "p5": float(np.percentile(r, 5)),
        "worst": float(r.min()),
        "best": float(r.max()),
    }


def max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1.0).min())


def main() -> None:
    data = {c: fetch(c) for c in ASSETS}
    for c, name in ASSETS.items():
        d = data[c]
        print(f"[数据] {c} {name}: {d['date'].iloc[0].date()}..{d['date'].iloc[-1].date()}  n={len(d)}")

    # ---- 1. 单资产持有期盈利概率（各自完整历史） ----
    print("\n=== 1. 单资产：任意时点买入、持有 N 久的结果 (rolling 全部起点) ===")
    print(f"{'资产':<8}{'持有期':<6}{'样本':>6}{'胜率':>8}{'中位收益':>10}{'平均收益':>10}{'5%最差':>9}{'极端最差':>9}")
    single = {}
    for c, name in ASSETS.items():
        close = data[c]["close"].to_numpy()
        single[c] = {}
        for label, h in HORIZONS:
            s = holding_stats(close, h)
            if s is None:
                continue
            single[c][label] = s
            print(f"{name:<8}{label:<7}{s['n']:>6}{s['win']*100:>7.1f}%{s['med']*100:>9.1f}%"
                  f"{s['mean']*100:>9.1f}%{s['p5']*100:>8.1f}%{s['worst']*100:>8.1f}%")

    # ---- 对齐重叠期，构造组合 ----
    merged = data["512890"].rename(columns={"close": "div"}).merge(
        data["513100"].rename(columns={"close": "ndx"}), on="date", how="inner"
    ).sort_values("date").reset_index(drop=True)
    print(f"\n[组合重叠期] {merged['date'].iloc[0].date()}..{merged['date'].iloc[-1].date()}  n={len(merged)}")

    div = merged["div"].to_numpy()
    ndx = merged["ndx"].to_numpy()

    # 50/50 买入持有：到期组合收益 = 0.5*ret_div + 0.5*ret_ndx（初始等权，不再平衡）
    print("\n=== 2. 50/50 组合：任意时点买入、持有 N 久 (买入持有, 不调仓) ===")
    print(f"{'持有期':<6}{'样本':>6}{'胜率':>8}{'中位收益':>10}{'平均收益':>10}{'5%最差':>9}{'极端最差':>9}")
    for label, h in HORIZONS:
        if len(merged) <= h:
            continue
        r = 0.5 * (div[h:] / div[:-h] - 1.0) + 0.5 * (ndx[h:] / ndx[:-h] - 1.0)
        print(f"{label:<7}{len(r):>6}{np.mean(r>0)*100:>7.1f}%{np.median(r)*100:>9.1f}%"
              f"{np.mean(r)*100:>9.1f}%{np.percentile(r,5)*100:>8.1f}%{r.min()*100:>8.1f}%")

    # ---- 3. 相关性 + 分散化降波动 ----
    rd = pd.Series(div).pct_change().dropna()
    rn = pd.Series(ndx).pct_change().dropna()
    corr = float(np.corrcoef(rd, rn)[0, 1])
    roll = pd.Series(div).pct_change().rolling(63).corr(pd.Series(ndx).pct_change())
    vol_div = rd.std() * np.sqrt(TRADING_DAYS)
    vol_ndx = rn.std() * np.sqrt(TRADING_DAYS)
    r_combo = 0.5 * rd.values + 0.5 * rn.values  # 每日再平衡近似
    vol_combo = r_combo.std() * np.sqrt(TRADING_DAYS)
    print("\n=== 3. 分散化 ===")
    print(f"日收益相关性(全期): {corr:.3f}   滚动63日相关性区间: [{roll.min():.2f}, {roll.max():.2f}]")
    print(f"年化波动率: 红利低波 {vol_div*100:.1f}%  纳指100 {vol_ndx*100:.1f}%  50/50组合 {vol_combo*100:.1f}%")
    print(f"分散收益: 组合波动比两资产均值低 {(1-vol_combo/((vol_div+vol_ndx)/2))*100:.1f}%")
    # 各自最大回撤
    print(f"历史最大回撤: 红利低波 {max_drawdown(div)*100:.1f}%  纳指100 {max_drawdown(ndx)*100:.1f}%  "
          f"50/50日再平衡 {max_drawdown(np.cumprod(1+np.r_[0,r_combo]))*100:.1f}%")

    # ---- 4. 逢低买入 vs 定投 vs 一次性（相同现金流时间表） ----
    # 现金流：从重叠期首日起，每 21 个交易日「到账」1 份预算。
    # (a) 一次性：首日把所有未来预算贴现忽略，简单起见用「首日全投」对照（起点风险）。
    # (b) 定投 DCA：每到账日立即按 50/50 投入。
    # (c) 逢低 buy-the-dip：到账资金存现金；当某资产较其过去 252 日高点回撤 > thr 时，
    #     把当前现金全投入「回撤更深」的那个；否则继续持币。
    print("\n=== 4. 你的「逢低买入」习惯 vs 定投 vs 一次性 (相同节奏注入资金) ===")
    n = len(merged)
    contrib_idx = list(range(0, n, 21))
    total_budget = len(contrib_idx)  # 每份预算=1，总投入=份数

    def final_value(units_div, units_ndx):
        return units_div * div[-1] + units_ndx * ndx[-1]

    # (b) DCA
    u_d = u_n = 0.0
    for i in contrib_idx:
        u_d += 0.5 / div[i]
        u_n += 0.5 / ndx[i]
    dca_val = final_value(u_d, u_n)

    # (a) 一次性首日全投 50/50
    ls_val = total_budget * (0.5 * div[-1] / div[0] + 0.5 * ndx[-1] / ndx[0])

    # (c) buy-the-dip，敏感性扫阈值（不挑最优，全列出）
    def buy_dip(thr: float) -> tuple[float, float]:
        cash = 0.0
        u_d = u_n = 0.0
        peak_d = peak_n = -np.inf
        contrib_set = set(contrib_idx)
        deployed_budget = 0.0
        for i in range(n):
            peak_d = max(peak_d, div[i])
            peak_n = max(peak_n, ndx[i])
            if i in contrib_set:
                cash += 1.0
                deployed_budget += 1.0
            dd_d = div[i] / peak_d - 1.0
            dd_n = ndx[i] / peak_n - 1.0
            if cash > 0 and (dd_d <= -thr or dd_n <= -thr):
                if dd_d <= dd_n:  # 红利低波跌得更深
                    u_d += cash / div[i]
                else:
                    u_n += cash / ndx[i]
                cash = 0.0
        # 期末仍未投出的现金按 1:1 计入价值（持币不亏不赚）
        return final_value(u_d, u_n) + cash, deployed_budget

    print(f"总投入 = {total_budget:.0f} 份 (每 21 交易日 1 份)，比较期末总市值 / 总投入 = 倍数")
    print(f"  一次性首日全投 50/50      : {ls_val/total_budget:.3f}x   (注: 占用首日起全部资金, 不可比性最强)")
    print(f"  定投 DCA (每月投 50/50)   : {dca_val/total_budget:.3f}x")
    for thr in (0.05, 0.10, 0.15, 0.20):
        v, _ = buy_dip(thr)
        print(f"  逢低买入 (回撤>{thr*100:.0f}% 投跌深者): {v/total_budget:.3f}x")
    print("  注: DCA 与逢低法资金注入节奏相同; 逢低法平时持币 → 牛市有现金拖累, 熊市买在更低位.")


if __name__ == "__main__":
    main()
