# JuneKCTrading – Project Memory

**Last updated**: 2026-07-11 21:27 HKT  
**Purpose**: Long-term architecture, planning, and decision records for the JuneKCTrading project.

---

## Current Status (2026-07-11)

- Working 3-minute Dow Jones Keltner Channel streamer with incremental KC calculation.
- Signal detection logic implemented (`SignalDetector`) following breakout-reversion rules.
- Visualization (`plot_kc.py`) updated to show SHORT/LONG signals with clean marker placement.
- Experiment tracking system in place (per-config_id folders under `logs/experiments/` and `results/experiments/`).
- Next major milestone: **Order execution layer** using IG REST API.

---

## Roadmap: 4-Phase Implementation Plan

### Phase 1 – Order Placement Foundation (MVP)

**Goal**  
When a valid trading signal fires, the system places a **real working order** on the correct IG account (demo or live) with proper risk-reward filtering and duplicate prevention.

**Key Requirements**
- Risk-Reward ratio ≥ 1.5 calculated at signal time only.
- Working order placed at `entry_price` with pre-calculated `stop_loss` and initial `limitLevel` = opposite KC band.
- Credential management via multiple `.env` files:
  - `account1.env.demo`
  - `account1.env.live`
  - `account2.env.demo`
  - `account2.env.live`
  - etc.
- `paper_trading=True` → use demo credentials + place real demo orders.
- `paper_trading=False` → use live credentials + place real live orders.
- Duplicate `signal_id` prevention persists across restarts.
- All order attempts and IG responses logged in the experiment folder.

**Modules to Build**
- `src/account_resolver.py` – resolves `account_name` + `paper_trading` flag to the correct credential file.
- `src/ig_rest_client.py` – thin, reusable IG REST wrapper (login, create/amend/cancel working orders, close positions).
- Update `config.py` with:
  - `account_name`
  - `paper_trading`
  - `size` (£ per point)
  - `min_risk_reward`
  - `pending_bar_timeout`
  - `filled_bar_timeout`
- `src/order_manager.py` – high-level service that validates RR, checks duplicates, and calls `IGRestClient`.
- Minor updates to `run_kc_live.py` to wire the order manager after signal detection.

**Deliverables**
- End-to-end working order placement on valid signals.
- Clean separation between demo and live accounts.
- Processed `signal_id` tracking (basic `processed_signal_ids` list in `active_orders.json`).
- Full audit trail of every order attempt.

**Usability After Phase 1**
- You can run the system and receive real working orders.
- **No automatic management** of open orders or positions (you monitor manually).
- Safe foundation for live testing on demo first.

---

### Phase 2 – ActiveOrderManager & Dynamic Lifecycle

**Goal**  
Add intelligent, bar-by-bar management of both pending working orders and filled positions.

**Key Rules (Confirmed)**
- **Working order timeout**: Cancel if still open after **3 completed bars** since placement.
- **Position timeout**: Close at market if still open after **10 completed bars** since fill.
- **Target update**: Always amend `limitLevel` to the opposite KC band on every new completed bar (even if worse).
- **Dynamic stop loss – Long**:
  - If previous bar’s `low > entry` → move SL to entry (breakeven).
  - Else if previous bar crossed KC mid **and** current target > KC mid → move SL to KC mid.
- **Dynamic stop loss – Short**:
  - If previous bar’s `high < entry` → move SL to entry (breakeven).
  - Else if previous bar crossed KC mid **and** current target < KC mid → move SL to KC mid.
- Risk-Reward filter evaluated only at signal time.

**Core Module**
- `src/active_order_manager.py`
  - Maintains `active_orders.json` (or `state.json`) in the experiment folder.
  - On every completed bar:
    - Loads pending + filled items.
    - Updates target (always).
    - Updates stop loss per Long/Short rules.
    - Enforces 3-bar pending timeout → cancel.
    - Enforces 10-bar filled timeout → market close.
    - Persists updated state.

**File Format Example** (`active_orders.json`)
```json
{
  "pending": [...],
  "filled": [...],
  "processed_signal_ids": [...]
}
```

**Deliverables**
- Full bar-by-bar lifecycle management.
- Automatic target and stop-loss amendments.
- Timeout enforcement with market-close fallback.
- Persistent state that survives restarts.

**When to Implement**
- After Phase 1 is stable and you are satisfied with signal quality/frequency.

---

### Phase 3 – Multi-Account Runner & Monitoring

**Goal**  
Support running multiple concurrent experiments on different accounts (demo + live mixed) with visibility and operational tooling.

**Planned Features**
- Lightweight runner manager that can orchestrate multiple `run_kc_live.py` instances.
- Each experiment bound to its own `account_name` + `paper_trading` setting.
- `scripts/status.py` command that shows across all experiments:
  - Recently fired signals
  - Current pending working orders
  - Open positions + bars since fill
  - Any errors or timeouts triggered
- Improved audit logging and optional alerting (Telegram / email).
- Clear separation between “signal generation engine” and “order execution engine”.

**Deliverables**
- Concurrent multi-account operation.
- Operational dashboard / status view.
- Better error isolation between experiments.

**When to Implement**
- After Phase 2 is working and you want to run 2+ experiments simultaneously.

---

### Phase 4 – Risk, Analytics & Advanced Features (Optional)

**Goal**  
Add higher-level risk management, performance analytics, and automated decision layers.

**Possible Features**
- Dynamic position sizing (ATR-based or % risk per trade).
- Per-experiment performance tracking (win rate, RR achieved, P&L).
- Automated reporting and visualization of trade outcomes.
- Optional integration with notification channels for critical events.

**Deliverables**
- Sizing engine ready for multi-account scaling.
- Analytics layer for strategy evaluation.
- Optional alerting infrastructure.

**When to Implement**
- Only after the core order execution pipeline (Phase 1 + 2) is proven in live conditions.

---

## Open Decisions / Notes

- Credential files will be stored in a dedicated `accounts/` folder (to be confirmed).
- `active_orders.json` will be a single combined state file per experiment.
- Bar counting is based on **completed 3-minute bars** after the relevant event.
- Phase 1 will be implemented first; Phases 2–4 are planned but sequenced.

---

## Next Immediate Action

**Start Phase 1 implementation** once the user confirms readiness. All architectural decisions above are now documented and committed.