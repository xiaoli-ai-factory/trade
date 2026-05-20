# S12 Forward Paper Cron

Install:

```bash
crontab -e
```

Add:

```cron
0 16 * * * /home/hyd/claude_code/trade/scripts/forward_paper_s12_daily.sh
```

The script writes stdout/stderr to `paper/logs/forward_YYYY-MM-DD.log`.
It checks the exchange trading calendar before running `paper.runner`; non-trading days are logged and skipped.
