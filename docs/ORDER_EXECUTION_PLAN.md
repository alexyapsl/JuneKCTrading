# JuneKCTrading – Order Execution Implementation Plan

**Version**: 1.0  
**Date**: 2026-07-11  
**Status**: Approved – Phase 1 to start immediately

---

## 1. Overview

This document describes the 4-phase roadmap for adding **order execution** capability to the JuneKCTrading system using the IG REST Trading API.

The system must eventually support:
- Multiple concurrent experiments
- Multiple IG accounts running simultaneously
- Some accounts in demo mode, some in live mode
- Full auditability and state persistence

---

## 2. Phased Roadmap

### Phase 1 – Order Placement Foundation (MVP)

**Objective**  
When a valid trading signal is generated, place a **real working order** on the correct IG account (demo or live) with proper risk management and duplicate prevention.

**Key Requirements**
- Risk-Reward ratio must be ≥ 1.5 (calculated only at signal time).
- Place a **working order** (limit order) at the signal’s `entry_price`.
- Attach the pre-calculated `stop_loss` from the signal.
- Set initial `limitLevel` (target) to the opposite KC band at the time of the signal.
- Support multiple credential files in the format:
  - `account1.env.demo`
  - `account1.env.live`
  - `account2.env.demo`
  - `account2.env.live`
  - etc.
- `paper_trading=True` → use `.demo` credentials and place real demo orders.
- `paper_trading=False` → use `.live` credentials and place real live orders.
- Prevent duplicate orders for the same `signal_id` (persists across restarts).
- Log every order attempt + IG response into the experiment folder.

**Modules to Implement**
| Module | Responsibility |
|--------|----------------|
| `src/account_resolver.py` | Resolve `account_name` + `paper_trading` flag → correct credential file |
| `src/ig_rest_client.py` | Reusable IG REST client (login, create/amend/cancel working orders, close positions) |
| `src/order_manager.py` | High-level service: RR validation, duplicate check, call IG client, persist state |
| `config.py` (update) | Add `account_name`, `paper_trading`, `size`, `min_risk_reward`, timeout values |
| `run_kc_live.py` (update) | Wire `OrderManager` after `SignalDetector` |

**Deliverables**
- End-to-end working order placement on valid signals.
- Clean demo/live credential switching.
- Basic `processed_signal_ids` tracking.
- Full audit trail of all order activity.

**Usability After Phase 1**
- You can run the system and receive real working orders on demo or live.
- **No automatic management** of open orders or positions (manual monitoring required).
- Safe foundation for initial live testing.

---

### Phase 2 – ActiveOrderManager & Dynamic Lifecycle Management

**Objective**  
Add intelligent, bar-by-bar management of pending working orders and filled positions.

**Confirmed Business Rules**

| Rule | Description |
|------|-------------|
| **Working order timeout** | Cancel if still open after **3 completed 3-minute bars** since placement |
| **Position timeout** | Close at market if still open after **10 completed 3-minute bars** since fill |
| **Target update** | Always amend `limitLevel` to the current opposite KC band on every new completed bar (even if worse) |
| **Dynamic SL – Long** | If previous bar’s `low > entry` → move SL to entry (breakeven). Else if bar crossed KC mid **and** target > KC mid → move SL to KC mid |
| **Dynamic SL – Short** | If previous bar’s `high < entry` → move SL to entry (breakeven). Else if bar crossed KC mid **and** target < KC mid → move SL to KC mid |
| **Risk-Reward** | Evaluated **only at signal time** using the KC values at that moment |

**Core Module**
- `src/active_order_manager.py`

**Responsibilities on Every Completed Bar**
1. Load current state from `active_orders.json`.
2. **Pending working orders**:
   - Update target (`limitLevel`) to opposite KC band (always).
   - Update stop loss according to Long/Short rules.
   - If still open after 3 bars → cancel order.
3. **Filled positions**:
   - If still open after 10 bars → close at market.
4. Persist updated state to `active_orders.json`.

**State File** (`active_orders.json`)
Located in each experiment folder (`logs/experiments/<config_id>/`).

Example structure:
```json
{
  "pending": [
    {
      "signal_id": "sig_20260710_1718_short",
      "deal_reference": "...",
      "placed_bar_time": "2026-07-10T17:18:00Z",
      "bars_since_placement": 2,
      "direction": "SHORT",
      "entry": 52623.2,
      "stop": 52657.3,
      "target": 52644.8
    }
  ],
  "filled": [...],
  "processed_signal_ids": [...]
}
```

**Deliverables**
- Full bar-by-bar lifecycle management.
- Automatic target and stop-loss amendments.
- Timeout enforcement with market-close fallback.
- Persistent state that survives restarts.

---

### Phase 3 – Multi-Account Runner & Operational Monitoring

**Objective**  
Support running multiple concurrent experiments on different accounts (demo + live mixed) with operational visibility.

**Planned Features**
- Lightweight runner manager to orchestrate multiple `run_kc_live.py` instances.
- Each experiment bound to its own `account_name` + `paper_trading` setting.
- `scripts/status.py` command showing across all experiments:
  - Recently fired signals
  - Current pending working orders
  - Open positions + bars since fill
  - Errors and triggered timeouts
- Improved audit logging and optional alerting (Telegram / email).
- Clear separation between signal generation and order execution engines.

**Deliverables**
- Concurrent multi-account operation.
- Operational dashboard / status view.
- Better error isolation between experiments.

---

### Phase 4 – Risk, Analytics & Advanced Features (Optional)

**Objective**  
Higher-level risk management and performance analytics.

**Possible Features**
- Dynamic position sizing (ATR-based or % risk per trade).
- Per-experiment performance tracking (win rate, realized RR, P&L).
- Automated reporting and visualization of trade outcomes.
- Notification channel integration for critical events.

**Deliverables**
- Sizing engine ready for multi-account scaling.
- Analytics layer for strategy evaluation.
- Optional alerting infrastructure.

---

## 3. Credential Management Strategy

- Multiple credential files supported.
- Naming convention: `{account_name}.env.{demo|live}`
- Example files:
  - `account1.env.demo`
  - `account1.env.live`
  - `account2.env.demo`
  - `account2.env.live`
- Location: to be decided (recommended: `accounts/` folder at project root).
- The system loads the correct file based on `account_name` and `paper_trading` flag.

---

## 4. Risk-Reward Calculation (Confirmed)

**Short Signal**
- Risk = `stop_loss - entry`
- Reward = `entry - target` (target = current KC lower at signal time)

**Long Signal**
- Risk = `entry - stop_loss`
- Reward = `target - entry` (target = current KC upper at signal time)

**Filter**: Only place order if `Reward / Risk >= 1.5`

---

## 5. Summary of Expected Deliverables per Phase

| Phase | Deliverable | Usable for Live Trading? |
|-------|-------------|--------------------------|
| 1 | Real working orders on valid signals + credential switching + duplicate prevention | Yes (manual monitoring required) |
| 2 | Dynamic SL/target management + 3-bar / 10-bar timeouts | Yes (semi-automated) |
| 3 | Multi-account concurrent runner + operational dashboard | Yes (production-grade) |
| 4 | Dynamic sizing + analytics + alerting | Yes (advanced) |

---

## 6. Next Steps

1. User confirms readiness.
2. Begin **Phase 1** implementation in the following order:
   - `src/account_resolver.py`
   - `src/ig_rest_client.py`
   - Update `config.py`
   - `src/order_manager.py`
   - Wire into `run_kc_live.py`
3. Test on demo account (`paper_trading=True`).
4. Move to Phase 2 only after Phase 1 is stable.

---

*This document is the single source of truth for the order execution implementation plan. It is also mirrored in `MEMORY.md` for long-term project continuity.*