# Plan: S/R Accuracy Evaluation

## Context
S/R zones are detected historically via a sliding window. We have 500 bars of data, with zones detected at various points. For any zone detected at bar N, bars N+1 onward serve as a forward test: did price actually respect (bounce) or violate (break) the zone? This plan adds Python-side accuracy evaluation in `run_pipeline()` and surfaces the results in the fullscreen chart view as a collapsible right-side stats panel.

## Critical File
- `C:\Users\avpei\UUDD\dashboard.py` — all changes are here (pipeline + template)

## Existing Data Structures (reused as-is)
Each zone in `all_results[symbol]["results"]` already has:
- `result`: `"support"` | `"resistance"`
- `price_range.low / .high`: zone bounds
- `detected_at`: unix timestamp (last bar of detection window)
- `start_time / end_time`: zone active span

The OHLCV dataframe `df` (500 bars, pandas, `df['time']` is tz-aware UTC) is already in scope inside `run_pipeline()` at evaluation time.

---

## Phase 1 — First-Touch Evaluation (implement now)

Scan forward bars after `detected_at`. Simulate real-world entry: the user reads the last closed bar and places a limit order at the zone boundary for the next bar.

**Support** (limit order at `z_high`):
- `bar['low'] > z_high` → price above zone, no interaction this bar, continue scanning
- `bar['high'] >= z_high AND bar['low'] <= z_high` → bar straddles `z_high`, limit order fills at `z_high`
  - `bar['low'] < z_low` → stop hit same bar → **break**
  - otherwise → **bounce**
- `bar['high'] < z_high` → gap-down past `z_high`, order cannot fill → **break** (no entry)

**Resistance** (limit order at `z_low`):
- `bar['high'] < z_low` → price below zone, no interaction this bar, continue scanning
- `bar['low'] <= z_low AND bar['high'] >= z_low` → bar straddles `z_low`, limit order fills at `z_low`
  - `bar['high'] > z_high` → stop hit same bar → **break**
  - otherwise → **bounce**
- `bar['low'] > z_low` → gap-up past `z_low`, order cannot fill → **break** (no entry)

If no qualifying bar found in remaining bars → `untested`.

### Python: `evaluate_zone_accuracy(zone, df)` function

```python
def evaluate_zone_accuracy(zone, df):
    """
    First-touch evaluation with realistic entry simulation.
    Entry requires bar to straddle zone boundary (limit order fillable at boundary price).
    Gap through zone without straddling = break with no valid entry.
    Break condition: support → low < z_low; resistance → high > z_high.
    """
    detected_at = zone['detected_at']
    zone_type   = zone['result']
    z_low  = zone['price_range']['low']
    z_high = zone['price_range']['high']

    future = df[df['time'] > pd.Timestamp(detected_at, unit='s', tz='UTC')]

    for _, bar in future.iterrows():
        if zone_type == 'resistance':
            if bar['high'] < z_low:
                continue                              # no interaction, price below zone
            elif bar['low'] > z_low:
                # gap-up: price jumped above z_low, limit at z_low cannot fill
                return {'outcome': 'break', 'test_bar_time': int(bar['time'].timestamp()), 'entry': 'gap'}
            else:  # high >= z_low AND low <= z_low: limit fills at z_low
                outcome = 'break' if bar['high'] > z_high else 'bounce'
                return {'outcome': outcome, 'test_bar_time': int(bar['time'].timestamp()), 'entry': 'valid'}
        else:  # support
            if bar['low'] > z_high:
                continue                              # no interaction, price above zone
            elif bar['high'] < z_high:
                # gap-down: price jumped below z_high, limit at z_high cannot fill
                return {'outcome': 'break', 'test_bar_time': int(bar['time'].timestamp()), 'entry': 'gap'}
            else:  # high >= z_high AND low <= z_high: limit fills at z_high
                outcome = 'break' if bar['low'] < z_low else 'bounce'
                return {'outcome': outcome, 'test_bar_time': int(bar['time'].timestamp()), 'entry': 'valid'}

    return {'outcome': 'untested', 'test_bar_time': None}
```

### Pipeline: call it for every zone in `run_pipeline()`
After zone detection loop, before `all_results[symbol]` is stored:
```python
for zone in symbol_results:
    zone['accuracy'] = evaluate_zone_accuracy(zone, df)
```

### JSON export: include accuracy in `formatted_all_results`
```python
zone_entry['accuracy'] = zone.get('accuracy', {
    'outcome': 'untested', 'test_bar_time': None
})
```

Per-symbol summary:
```python
acc_list   = [z['accuracy'] for z in zones]
bounces    = sum(1 for a in acc_list if a['outcome'] == 'bounce')
breaks     = sum(1 for a in acc_list if a['outcome'] == 'break')
untested   = sum(1 for a in acc_list if a['outcome'] == 'untested')
total_tested = bounces + breaks
formatted_all_results[symbol]['accuracy_summary'] = {
    'bounces': bounces, 'breaks': breaks, 'untested': untested,
    'hit_rate': round(bounces / total_tested * 100, 1) if total_tested else None
}
```

### Fullscreen UI: right-side accuracy panel
Add inside `#fs-overlay` as a sibling to `#fs-chart-container`:

```html
<div id="fs-accuracy-panel" style="width:230px;overflow-y:auto;border-left:1px solid #ddd;background:#fafafa;padding:8px;font-size:0.8em;flex-shrink:0;">
    <div id="fs-accuracy-summary" style="margin-bottom:8px;border-bottom:1px solid #ddd;padding-bottom:6px;"></div>
    <div id="fs-accuracy-list"></div>
</div>
```

JavaScript — populate in `openFullscreen()`:
```js
function renderAccuracyPanel(symbol) {
    const data    = allResults[symbol];
    const summary = data.accuracy_summary;
    const zones   = (data.results || []).filter(z => z.result !== 'nil');

    // Summary
    const hr = summary.hit_rate !== null
        ? `<b>${summary.hit_rate}%</b> hit rate (${summary.bounces}↑ / ${summary.breaks}✗)`
        : 'No tests yet';
    document.getElementById('fs-accuracy-summary').innerHTML =
        `${hr}<br>🟢 ${summary.bounces} bounce &nbsp; 🔴 ${summary.breaks} break &nbsp; ⚪ ${summary.untested} untested`;

    // Per-zone list — most recent first
    const STATUS = { bounce: '🟢', break: '🔴', untested: '⚪' };
    document.getElementById('fs-accuracy-list').innerHTML = [...zones].reverse().map(z => {
        const acc   = z.accuracy;
        const label = z.result === 'support' ? '▲ S' : '▼ R';
        const price = `${z.price_range.low.toFixed(2)}–${z.price_range.high.toFixed(2)}`;
        const icon  = STATUS[acc.outcome] || '⚪';
        return `<div style="padding:4px 0;border-bottom:1px solid #eee">
            ${icon} ${label} ${price}
        </div>`;
    }).join('');
}
```

Call `renderAccuracyPanel(symbol)` in `openFullscreen()` after building the chart.

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

Hovering over a zone box on the chart highlights the corresponding sidebar entry (yellow background) via `chart.subscribeCrosshairMove` — checking if cursor price falls within `price_range.low–price_range.high`. Clears when cursor leaves the zone.

---

## Phase 2 — Multi-Touch State Machine (future enhancement)

A zone can bounce multiple times before eventually breaking. Track ALL distinct tests via a state machine:
`AWAY → IN_ZONE → AWAY → IN_ZONE → ...`
Each AWAY→IN_ZONE transition = one test. A break terminates tracking.

"Away" is determined by close: resistance → `close < z_low`; support → `close > z_high`.
"In zone" trigger (entry): resistance → `low <= z_low AND high >= z_low`; support → `high >= z_high AND low <= z_high` (bar straddles boundary = limit order fillable).
Gap through boundary without straddling = break with no valid entry.
Break condition: resistance → `high > z_high`; support → `low < z_low`.

Bounce magnitude is measured relative to **zone range** (`z_high - z_low`), giving a dimensionless ratio:
- Resistance: `magnitude = (z_low - min_excursion) / (z_high - z_low)`
- Support: `magnitude = (max_excursion - z_high) / (z_high - z_low)`

A magnitude of `1.0` means price moved one full zone-width away before returning.

### Python: `evaluate_zone_accuracy(zone, df)` — upgraded

```python
def evaluate_zone_accuracy(zone, df):
    """
    Multi-touch state machine with bounce magnitude normalised by zone range.
    State: AWAY <-> IN_ZONE. Each AWAY->IN_ZONE = one test.
    Entry requires bar to straddle zone boundary (same rule as Phase 1).
    Break condition: support → low < z_low; resistance → high > z_high.
    Gap through zone (no straddle) counts as break with no valid entry.
    Bounce magnitude = price excursion / zone range (dimensionless ratio).
    """
    detected_at = zone['detected_at']
    zone_type   = zone['result']
    z_low  = zone['price_range']['low']
    z_high = zone['price_range']['high']
    z_range = z_high - z_low

    future = df[df['time'] > pd.Timestamp(detected_at, unit='s', tz='UTC')]
    tests      = []
    in_contact = True   # detection bar already touching zone
    excursion  = None   # track extreme price while AWAY

    for _, bar in future.iterrows():
        if zone_type == 'resistance':
            straddles = bar['high'] >= z_low and bar['low'] <= z_low
            gap_up    = bar['low'] > z_low             # gapped above z_low, no fill
            if in_contact:
                if bar['close'] < z_low:               # left the zone
                    in_contact = False
                    excursion  = bar['low']
            else:
                excursion = min(excursion, bar['low']) if excursion is not None else bar['low']
                if gap_up or straddles:
                    magnitude = round((z_low - excursion) / z_range, 3) if excursion is not None else 0
                    if gap_up or bar['high'] > z_high:
                        tests.append({'outcome': 'break',  'bar_time': int(bar['time'].timestamp()), 'bounce_magnitude': magnitude, 'entry': 'gap' if gap_up else 'valid'})
                        break
                    else:
                        tests.append({'outcome': 'bounce', 'bar_time': int(bar['time'].timestamp()), 'bounce_magnitude': magnitude, 'entry': 'valid'})
                        in_contact = True
                        excursion  = None
        else:  # support
            straddles = bar['low'] <= z_high and bar['high'] >= z_high
            gap_down  = bar['high'] < z_high           # gapped below z_high, no fill
            if in_contact:
                if bar['close'] > z_high:              # left the zone
                    in_contact = False
                    excursion  = bar['high']
            else:
                excursion = max(excursion, bar['high']) if excursion is not None else bar['high']
                if gap_down or straddles:
                    magnitude = round((excursion - z_high) / z_range, 3) if excursion is not None else 0
                    if gap_down or bar['low'] < z_low:
                        tests.append({'outcome': 'break',  'bar_time': int(bar['time'].timestamp()), 'bounce_magnitude': magnitude, 'entry': 'gap' if gap_down else 'valid'})
                        break
                    else:
                        tests.append({'outcome': 'bounce', 'bar_time': int(bar['time'].timestamp()), 'bounce_magnitude': magnitude, 'entry': 'valid'})
                        in_contact = True
                        excursion  = None

    bounce_count = sum(1 for t in tests if t['outcome'] == 'bounce')
    break_count  = sum(1 for t in tests if t['outcome'] == 'break')
    bounce_magnitudes = [t['bounce_magnitude'] for t in tests if t['outcome'] == 'bounce' and t['bounce_magnitude'] > 0]

    if not tests:                            final_status = 'untested'
    elif tests[-1]['outcome'] == 'break':    final_status = 'broken'
    else:                                    final_status = 'active'

    return {
        'tests': tests,
        'bounce_count': bounce_count,
        'break_count': break_count,
        'final_status': final_status,
        'avg_bounce_magnitude': round(sum(bounce_magnitudes) / len(bounce_magnitudes), 3) if bounce_magnitudes else None
    }
```

**Example**: resistance zone with z_range=10. Price drops 15pts away (mag=1.5x) then 8pts (mag=0.8x) then breaks:
`tests=[{bounce, mag=1.5}, {bounce, mag=0.8}, {break, mag=0}]`, `avg_bounce_magnitude=1.15`, `final_status='broken'`

UI upgrade: replace badge-only display with test history sequence per zone:
`B(1.5x) B(0.8x) X` — and summary switches from `hit_rate` to `survival_rate`.

### Result Presentation

Same right-side panel, upgraded:

```
85.0% survival (17↑ / 3✗)
🟢 5 active   🔴 3 broken   ⚪ 2 untested

🟢 ▼ R  1.2340–1.2360
     B(1.5x) B(0.8x)   (2↑ 0✗)
🔴 ▲ S  1.2280–1.2300
     B(2.1x) X          (1↑ 1✗)
⚪ ▼ R  1.2410–1.2430
     no tests
...
```

Magnitude suffix `x` = multiples of zone range. Summary metric changes from hit rate to survival rate.

Hover behaviour carried forward from Phase 1. Additionally, the highlighted sidebar entry expands to show the full test sequence inline when the cursor is inside the zone.

---

## Phase 3 — Advanced: Zone strength score (future enhancement)
Composite score per zone combining:
- Touch count before detection (from `prev_matches` length — already available)
- Average bounce magnitude in zone-range units
- Time-to-first-test (fast retest = high-demand zone)
- Multi-test survival rate

### Result Presentation

```
85.0% survival (17↑ / 3✗)
🟢 5 active   🔴 3 broken   ⚪ 2 untested

⭐⭐⭐⭐⭐ 🟢 ▼ R  1.2340–1.2360   score: 92
          B(1.5x) B(0.8x)   (2↑ 0✗)
⭐⭐⭐ 🔴 ▲ S  1.2280–1.2300   score: 61
          B(2.1x) X          (1↑ 1✗)
⭐⭐ ⚪ ▼ R  1.2410–1.2430   score: 34
          no tests
...
```

Zones sorted by score descending. The highest-scoring active zone is also highlighted in the grid chart header.

Hover behaviour carried forward from Phase 2. The highlighted entry additionally shows the composite score breakdown (touch count, avg magnitude, time-to-first-test, survival rate) as a tooltip-style expansion.

---

## Implementation Order
1. Add `evaluate_zone_accuracy()` function
2. Call it in the zone-building loop inside `run_pipeline()`
3. Include `accuracy` and `accuracy_summary` in `formatted_all_results`
4. Add `#fs-accuracy-panel` HTML to fullscreen overlay
5. Add `renderAccuracyPanel()` JS and call it from `openFullscreen()`
6. Run `python dashboard.py` to verify

## Verification
1. `python dashboard.py` — runs without error
2. Open `dashboard.html`, click any chart to fullscreen
3. Right-side panel shows: hit rate summary + per-zone bounce/break/untested badges
4. Zones detected at the last bar show "untested" (no future bars)
5. Confirm `pipeline_cache.json` contains `accuracy` keys per zone
