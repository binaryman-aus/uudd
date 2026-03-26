# Telegram Notification Enhancement — Design Spec

**Date:** 2026-03-26
**Status:** Approved

---

## Overview

Enhance the consolidated Telegram S/R alert message to replace price range data with a compact 10-bar detection history per symbol, giving the user an immediate sense of the recent S/R situation at a glance.

---

## Requirements

- Only symbols where the **latest bar has an active S/R detection** are included (existing behaviour, unchanged).
- Price range (`range_low`, `range_high`) is **removed** from the message entirely.
- Each symbol shows a **10-character history string** representing the last 10 bars:
  - `S` — support detected on that bar
  - `R` — resistance detected on that bar
  - `~` — no detection on that bar
  - Left = oldest bar, right = most recent (current) bar.
- A **trailing emoji** shows the current detection type: ⬆️ for support, ⬇️ for resistance.
- Symbol names are **padded to 6 characters** so the history columns align in monospace.

---

## Message Format

```
🚨 S/R ALERT 🚨

`US500  ~~SSS~~RRR` ⬇️
`GER40  ~SSSSSSS~~` ⬆️
`XAUUSD RRRR~~SSSS` ⬆️

[View Dashboard](https://binaryman-aus.github.io/uudd/)
```

Each symbol row is a single inline code span so Telegram renders it in monospace, ensuring alignment.

---

## Data Flow Changes

### `run_pipeline` in `dashboard.py`

For each symbol with an active detection, build a 10-character history string:

1. Take the last 10 bar timestamps from `df['time'].iloc[-10:]`.
2. Build a lookup dict from `symbol_results`: `{detected_at: result}`.
3. For each of the 10 timestamps (oldest → newest), emit:
   - `'S'` if `detected_at` matches and `result == 'support'`
   - `'R'` if `detected_at` matches and `result == 'resistance'`
   - `'~'` if no match
4. Add `history` field to the `active_detections` entry for this symbol.

The `active_detections` dict shape changes from:
```python
{"symbol": str, "result": str, "range_low": float, "range_high": float}
```
to:
```python
{"symbol": str, "result": str, "history": str}  # history is 10 chars
```

### `send_consolidated_telegram` in `dashboard.py`

Rewrite message construction:
- Header: `🚨 S/R ALERT 🚨\n\n`
- Per symbol: `` `{symbol:<6} {det['history']}` {emoji}\n ``
  - emoji = ⬆️ if `result == 'support'` else ⬇️
- Footer: `\n[View Dashboard](link)`

Remove all references to `range_low` and `range_high`.

---

## Scope

| File | Change |
|------|--------|
| `dashboard.py` | Build `history` string in `run_pipeline`; rewrite message in `send_consolidated_telegram` |
| `sr_detect.py` | No change |
| `dashboard.html` (template) | No change |
| `dashboard.yml` (workflow) | No change |

---

## Edge Cases

- **Fewer than 10 bars of data**: Pad the left side with `~` so the string is always exactly 10 chars.
- **Symbol name longer than 6 chars**: No current symbols exceed 6 chars (`XAUUSD`, `BTCUSD`, etc.), but use `ljust(6)` to be safe — longer names will simply not align.
- **No active detections**: Existing guard (`if not detections: return`) remains unchanged — no message is sent.
