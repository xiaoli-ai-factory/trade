EXECUTED: qlib_version=0.9.7 alpha158_demo=pass a_share_data=partial

# A-W22-4-a Qlib Alpha158 Feasibility Probe

Probe date: 2026-05-21

Scope: feasibility probe only. No `strategies/s15_qlib_alpha158.py` was created.

## 1. Install Qlib + Minimum Demo

### Install result

实测:

```bash
python3 --version
# Python 3.12.3

python3 -m venv .venv_qlib_probe
.venv_qlib_probe/bin/python -m pip install pyqlib
# Successfully installed pyqlib-0.9.7 ...

/tmp/qlib_probe/venv/bin/python -c "import qlib; print(qlib.__version__)"
# 0.9.7
```

Notes:

- `pyqlib`, not `qlib`, installed successfully.
- The probe venv was moved out of the repo to `/tmp/qlib_probe/venv` after execution to avoid leaving a dirty worktree.
- Installed package included `lightgbm-4.6.0`.

### Official/minimum data + demo route

The exact command from the task does not exist in `pyqlib==0.9.7`:

```bash
/tmp/qlib_probe/venv/bin/python -m qlib.data.cli download_china_data
# No module named qlib.data.cli
```

The actual installed Qlib data CLI is:

```bash
/tmp/qlib_probe/venv/bin/python -m qlib.cli.data qlib_data ...
```

US simple data was attempted first and failed with upstream 404:

```text
https://github.com/SunsetWolf/qlib_dataset/releases/download/v2/qlib_data_simple_us_1d_latest.zip
404 Client Error: Not Found
```

CN official data worked, so the minimum demo was run on CN data instead.

### Alpha158 minimum demo result

Alpha158 feature config loads and exposes 158 factor names:

```text
factor_count = 158
first factors = ['KMID', 'KLEN', 'KMID2', 'KUP', 'KUP2', 'KLOW', 'KLOW2', 'KSFT', 'KSFT2', 'OPEN0']
last factors = ['VSUMN5', 'VSUMN10', 'VSUMN20', 'VSUMN30', 'VSUMN60', 'VSUMD5', 'VSUMD10', 'VSUMD20', 'VSUMD30', 'VSUMD60']
```

On official full CN data, CSI300 / 2019:

```text
features_shape = (73200, 158)
labels_shape = (73200, 1)
ic_nonnull_count = 157
ic_mean_abs = 0.016222308413990916
```

Top absolute mean Spearman IC factors from the demo:

```text
QTLD20     0.030053
VSUMD60   -0.029928
VSUMN60    0.029928
VSUMP60   -0.029928
BETA5     -0.029855
QTLD30     0.028596
VMA60      0.028181
VMA30      0.027790
MA30       0.026594
VSUMP20   -0.026424
```

Demo verdict: `pass`, with the data caveat in sections 2 and 3.

## 2. A 股 Data Availability

### Official Qlib CN data

Official full CN data downloaded via the actual installed CLI:

```bash
/tmp/qlib_probe/venv/bin/python -m qlib.cli.data qlib_data \
  --name qlib_data \
  --target_dir /tmp/qlib_probe/cn_full \
  --interval 1d \
  --region cn \
  --delete_old False \
  --exists_skip True
```

实测 coverage:

```text
calendar: 1999-11-10 to 2020-09-25, 4943 trading dates
feature directories: 3875 instruments
download zip: qlib_data_cn_1d_latest.zip, HEAD Content-Length 196549189 bytes
local extracted size: 521M
```

Major A-share codes exist:

```text
SH600000  1999-11-10  2020-09-25
SH600519  2001-08-27  2020-09-25
SZ000001  1999-11-10  2020-09-25
SZ000002  1999-11-10  2020-09-25
```

But the official full CN feature files for `SH600519` are only:

```text
change.day.bin
close.day.bin
factor.day.bin
high.day.bin
low.day.bin
open.day.bin
volume.day.bin
```

There is no `vwap.day.bin` and no `amount.day.bin`.

### Project data layer adaptation smoke test

Because default Alpha158 references `$vwap`, I tested a small local conversion using this project data layer:

- Source: `data.akshare_source.get_daily`
- Symbols: `600519`, `000001`, `600000`, `000002`
- Period: `2018-01-02` to `2020-12-31`
- Fields written to Qlib bin: `open`, `high`, `low`, `close`, `volume`, `vwap`
- `vwap = amount / vol`

Result:

```text
converted_dir = /tmp/qlib_probe/cn_project_adapted
calendar = 730 dates, 2018-01-02 to 2020-12-31
vwap_nonnull = {'600519': 730, '000001': 730, '600000': 730, '000002': 730}
```

A 股 data verdict: `partial`.

Reason: official Qlib CN data is downloadable and covers major A-shares, but is not sufficient for strict default Alpha158 because `vwap` is missing. Project data can fill it, but full-universe conversion and validation are still data engineering work.

## 3. Alpha158 on A 股

### Official full CN data result

Run: official full CN data, `csi300`, 2019.

```text
features_shape = (73200, 158)
labels_shape = (73200, 1)
factor_count = 158
nan_gt_50_count = 1
```

Dead factor:

```text
VWAP0    1.0
```

Top NaN ratios:

```text
VWAP0    1.000000
ROC60    0.027254
ROC30    0.024057
ROC20    0.023689
ROC10    0.023456
ROC5     0.022814
STD5     0.012268
BETA5    0.012268
VSTD5    0.012268
RESI5    0.012268
```

Interpretation: official Qlib CN data alone fails strict default Alpha158 completeness because `VWAP0` is 100% NaN.

### Project-adapted small A-share sample

Run: project-adapted 4-stock Qlib bin dataset, 2019.

```text
features_shape = (976, 158)
labels_shape = (976, 1)
factor_count = 158
nan_gt_50_count = 0
vwap0_nan_ratio = 0.0
nonnull_factors = 158
```

This confirms that Alpha158 can compute non-empty A-share factors if the data layer supplies `vwap`.

### 600519 Alpha158 first 5 rows

Below is the printed first 5 rows from the project-adapted dataset. The table is split because 158 columns are too wide for a readable Markdown report.

First 12 columns:

```text
                KMID      KLEN     KMID2       KUP      KUP2      KLOW     KLOW2      KSFT     KSFT2     OPEN0     HIGH0      LOW0
datetime
2019-01-02 -0.018033  0.027853 -0.647440  0.003312  0.118895  0.006508  0.233665 -0.014837 -0.532669  1.018365  1.021737  0.993372
2019-01-03 -0.016617  0.026435 -0.628624  0.002817  0.106558  0.007000  0.264818 -0.012434 -0.470364  1.016898  1.019763  0.992881
2019-01-04  0.025065  0.043676  0.573879  0.009655  0.221052  0.008957  0.205069  0.024367  0.557895  0.975548  1.009419  0.966811
2019-01-07 -0.004128  0.016086 -0.256646  0.006579  0.408997  0.005378  0.334357 -0.005329 -0.331286  1.004145  1.010752  0.994599
2019-01-08 -0.001173  0.019356 -0.060582  0.010735  0.554609  0.007448  0.384809 -0.004459 -0.230382  1.001174  1.011922  0.992543
```

`VWAP0` plus last 5 columns:

```text
               VWAP0    VSUMD5   VSUMD10   VSUMD20   VSUMD30   VSUMD60
datetime
2019-01-02  1.006313  0.680872  0.406260 -0.096198  0.086645 -0.019214
2019-01-03  1.005754  0.050258  0.099898 -0.002495  0.012475 -0.008776
2019-01-04  0.995573  0.232544  0.101519 -0.019964  0.032056 -0.024499
2019-01-07  1.000901  0.068690  0.065298 -0.015449  0.041401 -0.056168
2019-01-08  1.003619 -0.722040 -0.014797  0.010525  0.026257 -0.026141
```

## 4. Anti-Survivorship Support

Qlib's local instrument file format supports instrument validity spans:

```text
INSTRUMENT    start_datetime    end_datetime
```

实测 with a real delisted project-data sample:

- Symbol: `SZ000003`
- Project data layer returned `is_delisted=True`
- Available rows: `2001-01-02` to `2002-06-14`, 341 rows
- Source: `tencent_stock_zh_a_hist_tx`

Converted this one symbol into Qlib format with the instrument end date set to `2002-06-14`.

Qlib instrument filtering result:

```text
listed_2002_full = {'SZ000003': [(Timestamp('2002-01-01 00:00:00'), Timestamp('2002-06-14 00:00:00'))]}
listed_after_delist = {}
```

Alpha158 handler on that dataset:

```text
features_shape = (119, 158)
date_min_max = 2002-01-01 to 2002-06-14
```

So Qlib's mechanics can compute factors before the delist date and exclude the stock after delist.

But official Qlib CN data did not include known delisted examples checked in this probe:

```text
SZ000003: not found
SH600001: not found
SZ000013: not found
```

Also, old delisted project data can have weaker field coverage. For `000003`, `vwap_nonnull = 94 / 341`, so strict default Alpha158 still needs field-quality handling for delisted histories.

Anti-survivorship verdict: Qlib span mechanics are usable, but anti-survivor data is not native-complete. 反幸存者需自行扩展。

## Go / No-Go裁决

Verdict: 🟡 **PARTIAL**

理由:

- `pyqlib==0.9.7` installs and imports on Python 3.12.
- Alpha158 handler runs and produces factor tables / labels / IC on official CN data.
- Official Qlib CN data is downloadable and contains major A-share codes.
- Official Qlib CN data misses `vwap`, causing default Alpha158 `VWAP0` to be 100% NaN.
- A small project-data adapter with `vwap=amount/vol` makes all 158 factors non-empty on active A-share samples.
- Qlib supports instrument validity spans, but official data does not provide a complete anti-survivor universe; delisted support requires project-side extension and validation.

Strict reading:

- Official Qlib data alone: not enough for full default Alpha158 Gate1.
- Project-adapted data path: feasible, but not yet full-universe validated.

Recommendation: do not start full A-W22-4-b until approving a 3-5 day data adapter spike. If that spike passes, full Gate1 is likely 5-8 working days.

Main engineering difficulties:

- Build a repeatable BaoStock/AkShare to Qlib bin converter for full A-share universe.
- Produce `vwap` consistently from `amount / volume`; verify units and missing old-history amount fields.
- Encode active and delisted instrument spans without survivorship leakage.
- Align calendars, suspended days, adjusted vs unadjusted prices, and labels.
- Add NaN/dead-factor audits before every Gate1 run.
- Decide whether to use default Alpha158 with `VWAP0`, or a documented Alpha158-minus-VWAP variant if historical `amount` is too sparse for delisted names.
