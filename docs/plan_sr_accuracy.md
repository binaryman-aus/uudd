# Plan: S/R Accuracy Evaluation

## Context
S/R zones are detected historically via a sliding window across 500 bars of data. For any zone detected at bar N, bars N+1 onward serve as a forward test: did price actually respect (bounce) or violate (break) the zone?

This plan adds accuracy evaluation to the pipeline and surfaces the results in the fullscreen chart view as a right-side stats panel.

**Critical file:** `dashboard.py` — all changes are here (pipeline + template)

## Zone Data Available
Each zone already has: `result` (support/resistance), `price_range.low` (`z_low`) / `price_range.high` (`z_high`), `detected_at` (timestamp of last detection bar). The full OHLCV dataframe (500 bars) is available at evaluation time.

---

## Entry & Break Logic (shared across phases)

The user places a **limit order at the zone boundary** for the next bar after detection. Entry requires the bar to straddle the boundary — if price jumps past without touching it, no fill occurs.

### Support zone — limit buy at `z_high` (top of zone)

| Bar behaviour | Outcome |
|---|---|
| Low stays above `z_high` | No interaction — skip bar |
| Bar straddles `z_high` (low ≤ z_high ≤ high) | **Limit fills at z_high** → if low < z_low same bar: **break**; else **bounce** |
| Bar gaps through entire zone (high < z_high AND low < z_low) | **Break** — no fill, zone violated |
| Bar gaps into zone but not through (high < z_high AND low ≥ z_low) | No fill — zone still alive, continue scanning |

### Resistance zone — limit sell at `z_low` (bottom of zone)

| Bar behaviour | Outcome |
|---|---|
| High stays below `z_low` | No interaction — skip bar |
| Bar straddles `z_low` (low ≤ z_low ≤ high) | **Limit fills at z_low** → if high > z_high same bar: **break**; else **bounce** |
| Bar gaps through entire zone (low > z_low AND high > z_high) | **Break** — no fill, zone violated |
| Bar gaps into zone but not through (low > z_low AND high ≤ z_high) | No fill — zone still alive, continue scanning |

If no qualifying bar is found in remaining bars → **untested**.

---

## Phase 1 — First-Touch Evaluation *(implement now)*

The simplest question: what happened the **first time** price came back to the zone after detection?

Scan forward bars using the entry logic above. Stop at the first result (bounce, break, or end of data).

Each zone gets one of three outcomes: `bounce` / `break` / `untested`.

Per-symbol summary: bounce count, break count, untested count, hit rate (bounces ÷ tested).

### Result Presentation

Right-side panel (230px) in fullscreen view only:

```
85.0% hit rate (17↑ / 3✗)
🟢 17 bounce   🔴 3 break   ⚪ 5 untested

🟢 ▼ R  1.2340–1.2360       ← highlighted when cursor is inside zone
🔴 ▲ S  1.2280–1.2300
⚪ ▼ R  1.2410–1.2430
...
```

One badge per zone. No magnitude, no test history.

Hovering over a zone box on the chart highlights the corresponding sidebar entry (yellow background) — checking if cursor price falls within `z_low–z_high`. Clears when cursor leaves the zone.

---

## Phase 2 — Single-Trade Simulation *(future enhancement)*

Phase 1 answers "did it bounce or break on first touch?" Phase 2 answers "if I had held the trade, how far could it have gone — and did it eventually stop out?"

### How it works

1. **Entry** — same logic as Phase 1. If no fill is obtained, outcome is `untested`.
2. **Hold** — once filled, hold the position with SL fixed at the zone's far boundary:
   - Support (long): SL at `z_low`
   - Resistance (short): SL at `z_high`
3. **Track** — from the fill bar onward, record the maximum favorable excursion:
   - Support: highest `high` reached above `z_high`
   - Resistance: lowest `low` reached below `z_low`
4. **Terminate** — when SL is hit (`low < z_low` for support, `high > z_high` for resistance) or data ends

### Magnitude

Max favorable excursion expressed as a multiple of zone width (`z_high − z_low`):

- Support: `(max high above z_high − z_high) ÷ zone width`
- Resistance: `(z_low − min low below z_low) ÷ zone width`

A magnitude of `1.0x` means price moved one full zone-width in the favorable direction. Higher = more potential reward.

For scenario 3 (SL hit), magnitude is the best excursion reached *before* the stop was triggered.

### Zone outcomes

| # | Outcome | Description |
|---|---|---|
| 1 | **Untested** | No fill obtained (price never straddled boundary) |
| 2 | **Active** | Filled, SL not yet hit — show max magnitude reached so far |
| 3 | **Broken** | Filled, SL eventually hit — show max magnitude before stop |

### Result Presentation

**Right-side panel (300px)** — same as Phase 1, upgraded:

```
🟢 5 active   🔴 3 broken   ⚪ 2 untested

🟢 ▼ R  1.2340–1.2360   max 2.3x
   Mar 29 14:00
🔴 ▲ S  1.2280–1.2300   max 1.1x  ✗
   Mar 29 10:00
⚪ ▼ R  1.2410–1.2430
   Mar 28 22:00
...
```

`max Nx` = max favorable excursion in zone-widths. `✗` = SL was hit.

**Scatter chart** — below the candlestick chart, shares the same time scale (scrolls and zooms together):

- **x-axis**: time, same scale as the candlestick above
- **y-axis**: magnitude in zone-widths
  - Positive = support (long) — favorable excursion above entry
  - Negative = resistance (short) — favorable excursion below entry
- **Zero line**: dashed baseline (no movement from entry)
- **Colours**: green (active), red (broken)
- Untested zones (no fill) are not plotted

Example: a resistance zone where price moved 2.0 zone-widths below entry before the SL was hit would appear as a red bar at y = −2.0 at the detection time.

Hover behaviour carried forward from Phase 1. The highlighted sidebar entry expands to show entry price, max excursion, and SL hit time (if applicable) when cursor is inside the zone.

---

## Phase 3 — Multi-TP Strategy Simulator *(future enhancement)*

Simulate a real trading strategy by splitting position size across multiple take-profit levels. Calculates total P&L across all evaluated zones without any backend changes — all data needed is already available from Phase 2.

### Why frontend-only works

Phase 2 produces `max_magnitude` per zone — the maximum favorable excursion (in zone-widths) before the SL was hit or data ended. Because price moves continuously, if `max_magnitude >= TP_level` then that TP was necessarily reached on the path up/down. No bar-by-bar replay is needed.

### User input

The user configures a TP table in the accuracy panel. Each row has:
- **Level** — target excursion in zone-widths (e.g. `1.5x`)
- **Size** — percentage of position closed at this level (e.g. `40%`)

Constraint: all sizes must sum to exactly **100%**. The UI enforces this with live validation and shows the running total. Rows can be added or removed freely.

Example configuration:

| TP | Level | Size |
|----|-------|------|
| 1  | 1.0x  | 50%  |
| 2  | 2.0x  | 30%  |
| 3  | 4.0x  | 20%  |

### P&L calculation

For each zone with a fill (outcome ≠ untested):

- **Risk** = 1 zone-width (distance from entry to SL), used as the common unit
- For each TP row: if `max_magnitude >= tp_level` → that slice was closed at profit `+tp_level`
- For each TP row: if `max_magnitude < tp_level` AND zone is **broken** → that slice was closed at SL = `−1.0x`
- For each TP row: if `max_magnitude < tp_level` AND zone is **active** → position still open, excluded from realised P&L

P&L per zone (broken example with config above, max_magnitude = 1.3x):
```
TP1 hit (1.0x ≤ 1.3x):  +1.0 × 50% = +0.50x
TP2 miss (2.0x > 1.3x): −1.0 × 30% = −0.30x
TP3 miss (4.0x > 1.3x): −1.0 × 20% = −0.20x
Zone P&L = +0.50 − 0.30 − 0.20 = 0.00x
```

### Summary metrics

Across all filled zones:
- **Total P&L** — sum of all zone P&Ls (in zone-widths per trade, averaged)
- **Win zones** — zones where net P&L > 0
- **Loss zones** — zones where net P&L ≤ 0
- **Excluded** — active zones with unrealised TPs (counted separately)

### Result Presentation

The TP table and summary appear at the top of the accuracy panel, above the zone list:

```
┌─────────────────────────────────┐
│ TP Settings          [+ Add TP] │
│ TP1  1.0x   50%  [−]            │
│ TP2  2.0x   30%  [−]            │
│ TP3  4.0x   20%  [−]            │
│ Total: 100% ✓                   │
├─────────────────────────────────┤
│ Avg P&L: +0.42x/trade           │
│ 🟢 Win: 112  🔴 Loss: 71  ⚫ 6  │
└─────────────────────────────────┘

🟢 ▼ R  1.2340–1.2360   +0.80x
🔴 ▲ S  1.2280–1.2300   −0.20x  ✗
⚪ ▼ R  1.2410–1.2430   —
...
```

The zone list switches from showing `max Nx` to showing the net P&L for that zone under the active TP configuration. Updates live as the user edits the TP table.

Hover behaviour carried forward from Phase 2.

---

## Implementation Order (Phase 1)
1. Add `evaluate_zone_accuracy(zone, df)` function using Phase 1 entry logic
2. Call it for every zone in the zone-building loop inside `run_pipeline()`
3. Include `accuracy` per zone and `accuracy_summary` per symbol in `formatted_all_results`
4. Add `#fs-accuracy-panel` HTML to fullscreen overlay (right of chart, 230px)
5. Add `renderAccuracyPanel()` JS and call it from `openFullscreen()`

## Verification (Phase 1)
1. `python dashboard.py` — runs without error
2. Open `dashboard.html`, click any chart to fullscreen
3. Right panel shows hit rate summary + per-zone bounce/break/untested badges
4. Zones detected at the last bar show "untested" (no future bars to test against)
5. Confirm `pipeline_cache.json` contains `accuracy` keys per zone
