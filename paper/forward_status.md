# Paper Forward Status

- date: 2026-05-19
- nav: 10000.00
- cash: 10000.00
- state_path: <PROJECT_ROOT>/paper/state/forward.json
- next_trade_date_for_new_orders: 2026-05-20
- S1 selected: 0
- S1 note: ok
- S1 queued buys: 0
- S1 same-day sells: 0
- trend queued orders: 1
- trend note: ok
- trend position: none
- due executions processed: 0

## Pending Orders
```json
[
  {
    "execute_date": "2026-05-20",
    "id": "s3b_trend:2026-05-19:2026-05-20:sh000300:buy:2",
    "lot_size": 1,
    "order": {
      "quantity": 2,
      "side": "buy",
      "submitted_date": "2026-05-19",
      "symbol": "sh000300"
    },
    "status": "pending",
    "strategy": "s3b_trend"
  }
]
```
