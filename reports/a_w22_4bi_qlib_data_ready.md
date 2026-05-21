EXECUTED: bin_path=/home/hyd/claude_code/trade/data/qlib_data/cn_data symbols=5420 trade_days=1601 vwap_filled=yes delisted_included=yes

# A-W22-4-b-i S15 Qlib Data Ready

Scope: data preparation only. No strategy/model/backtest was run.

## Inputs

Source panel:

```text
data/cache/s2_panel_v2pit_2019-10-01_2026-05-15.parquet
rows = 7,528,942
symbols in parquet = 5,420
trade_days = 1,601
date range = 2019-10-08 to 2026-05-15
delisted symbols = 216
```

Note: the task text said 5,413 symbols, but the actual panel metadata and parquet scan both show 5,420 symbols. I converted all 5,420 and skipped none.

## Output

Qlib data path:

```text
data/qlib_data/cn_data/
```

Generated files:

```text
calendars/day.txt                 1,601 lines
instruments/all.txt               5,420 lines
features/<symbol>/*.day.bin        43,360 feature bin files
vwap.day.bin files                 5,420
disk size                          334M
```

Fields written per symbol:

```text
open.day.bin
high.day.bin
low.day.bin
close.day.bin
volume.day.bin
money.day.bin
vwap.day.bin
factor.day.bin
```

`csi300.txt` was not generated. It is optional in the task, and the source PIT panel does not contain true CSI300 membership history. I did not synthesize a fake CSI300 constituent file.

The generated data directory is ignored via `.gitignore`:

```text
data/qlib_data/
```

## Conversion Script

Script:

```text
scripts/convert_baostock_to_qlib.py
```

Run command used:

```bash
python3 scripts/convert_baostock_to_qlib.py --force --log-every 250
```

Completion line:

```text
EXECUTED: bin_path=/home/hyd/claude_code/trade/data/qlib_data/cn_data symbols=5420 trade_days=1601 vwap_filled=yes delisted_included=yes
```

I checked for installed official dump helpers in the available `pyqlib==0.9.7` environment:

```text
qlib.utils.dump_bin missing: ModuleNotFoundError No module named 'qlib.utils.dump_bin'
no packaged dump_bin.py / dump.py scripts found in the wheel
```

So the script writes Qlib's documented local bin layout directly. The writer follows Qlib `FileFeatureStorage`: first float32 value is the start calendar index, followed by float32 feature values.

## VWAP

Rule implemented:

```text
vwap = money / volume, when volume > 0
vwap = close, when volume <= 0
```

Zero-volume fallback rows:

```text
20,996
```

600519 vwap validation through Qlib:

```text
D.features(["sh600519"], ["$close", "$vwap", "Ref($close, 1)", "$money", "$volume"],
           "2024-01-01", "2024-12-31")

shape = (242, 5)
NaN ratio for $close/$vwap/Ref($close,1)/$money/$volume = 0.0
```

Sample:

```text
                            $close        $vwap  Ref($close, 1)        $money    $volume
instrument datetime
sh600519   2024-01-02  1685.010010  1691.755249     1726.000000  5.440082e+09  3215644.0
           2024-01-03  1694.000000  1686.366943     1685.010010  3.411401e+09  2022929.0
           2024-01-04  1668.999878  1672.292847     1694.000000  3.603970e+09  2155107.0
           2024-01-05  1663.359985  1666.343384     1668.999878  3.373156e+09  2024286.0
           2024-01-08  1643.989990  1646.168091     1663.359985  4.211919e+09  2558620.0
```

Direct check:

```text
                             $vwap  money_div_volume       $close
instrument datetime
sh600519   2024-01-02  1691.755249       1691.755249  1685.010010
           2024-01-03  1686.366943       1686.366943  1694.000000
           2024-01-04  1672.292847       1672.292847  1668.999878
```

## Delisted Symbols

`instruments/all.txt` includes the panel's 216 delisted symbols with end dates capped at the last available data date or `delist_date`, whichever is earlier.

Examples:

```text
SH601558    2019-10-08    2020-07-03
SZ002477    2019-10-08    2019-10-16
```

The requested example `SH600001` is not present in the supplied S2 PIT panel:

```text
has_sh600001_dir = False
```

I therefore validated a real delisted symbol from the source panel: `SH601558` / 退市锐电.

Qlib instrument-span validation:

```text
during 2020-06-01..2020-07-03:
SH601558 -> [(Timestamp('2020-06-01 00:00:00'), Timestamp('2020-07-03 00:00:00'))]

after 2020-07-06..2020-12-31:
SH601558 -> None
```

Raw delisted read:

```text
                       $close  $vwap  $money  $volume
instrument datetime
sh601558   2020-06-29    0.25   0.25     0.0      0.0
           2020-06-30    0.25   0.25     0.0      0.0
           2020-07-01    0.25   0.25     0.0      0.0
           2020-07-02    0.25   0.25     0.0      0.0
           2020-07-03    0.25   0.25     0.0      0.0
```

Alpha158 on `sh601558`:

```text
alpha_shape = (181, 158)
alpha_date_min_max = 2019-10-08 to 2020-07-03
alpha_nan_gt50 = 0
```

Tail sample:

```text
                       KMID  KLEN  KMID2  KUP  KUP2  KLOW  KLOW2  KSFT  KSFT2  OPEN0  HIGH0  LOW0
datetime   instrument
2020-07-01 sh601558     0.0   0.0    0.0  0.0   0.0   0.0    0.0   0.0    0.0    1.0    1.0   1.0
2020-07-02 sh601558     0.0   0.0    0.0  0.0   0.0   0.0    0.0   0.0    0.0    1.0    1.0   1.0
2020-07-03 sh601558     0.0   0.0    0.0  0.0   0.0   0.0    0.0   0.0    0.0    1.0    1.0   1.0
```

Risk note for b-ii: zero-volume delisted rows are readable and non-NaN, but volume-normalized Alpha158 factors can become extreme when `$volume == 0` because Qlib formulas divide by `($volume + 1e-12)`. The model/backtest stage should filter suspended or zero-volume rows, or explicitly winsorize volume factors.

## Factor / Adjustment

`factor.day.bin` is written as constant `1.0`.

Reason: per task instruction, the upstream S2 PIT panel is treated as already adjusted for this probe. The converter does not fetch or infer separate corporate-action adjustment factors.

Validation:

```text
                       $factor
instrument datetime
sh600519   2024-01-02      1.0
           2024-01-03      1.0
           2024-01-04      1.0
           2024-01-05      1.0
           2024-01-08      1.0
```

## Metadata

Generated metadata file:

```text
data/qlib_data/cn_data/metadata.json
```

Key values:

```text
rows = 7,528,942
symbols = 5,420
skipped_symbols = 0
trade_days = 1,601
start_date = 2019-10-08
end_date = 2026-05-15
delisted_symbols = 216
vwap_zero_volume_fallback_rows = 20,996
elapsed_seconds = 49.496
```
