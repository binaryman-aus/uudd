# Consolidation Zone Highlighter (CZH) — Indicator Specification

**Version:** 1.5  
**Platform:** cTrader / cAlgo (C#)  
**Date:** 2026-04-07 (revised post-review)  
**Type:** Overlay Indicator (renders directly on the price chart)  
**Direction:** Agnostic — detects compression, does not predict breakout direction

---

## Table of Contents

1. [Purpose and Philosophy](#1-purpose-and-philosophy)
2. [Detection Window](#2-detection-window)
3. [Criterion 1 — Volatility Compression (ATR Ratio)](#3-criterion-1--volatility-compression-atr-ratio)
4. [Criterion 2 — Price Range Tightness (N-bar Range Ratio)](#4-criterion-2--price-range-tightness-n-bar-range-ratio)
5. [Criterion 3 — Bar Overlap Score (Structural Congestion)](#5-criterion-3--bar-overlap-score-structural-congestion)
6. [Criterion 4 — Directional Balance (Bull/Bear Equilibrium)](#6-criterion-4--directional-balance-bullbear-equilibrium)
7. [Criterion 5 — Close Price Clustering (Statistical Tightness)](#7-criterion-5--close-price-clustering-statistical-tightness)
8. [Zone Boundary Calculation](#8-zone-boundary-calculation)
9. [Scoring System](#9-scoring-system)
10. [Zone Lifecycle](#10-zone-lifecycle)
11. [Visual Design](#11-visual-design)
12. [Configurable Parameters](#12-configurable-parameters)
13. [Entry Timing Usage](#13-entry-timing-usage)
14. [cTrader C# Implementation Notes](#14-ctrader-c-implementation-notes)
15. [Limitations and False Signal Conditions](#15-limitations-and-false-signal-conditions)
16. [Appendix A — Default Parameter Summary](#appendix-a--default-parameter-summary-table)
17. [Appendix B — Criterion Quick Reference](#appendix-b--quick-reference-criterion-passfail-summary)

---

## 1. Purpose and Philosophy

The Consolidation Zone Highlighter (CZH) identifies price areas where the market has entered a state of equilibrium: neither buyers nor sellers are in control, volatility has contracted relative to its recent history, and bars are overlapping structurally. These zones are graphically highlighted as shaded rectangles directly on the price chart.

The indicator is explicitly **direction-agnostic**. It does not attempt to forecast whether price will break upward or downward. Its sole function is to identify that a significant move is likely imminent and to mark the precise price zone from which that move will originate.

### Core Design Principle

Consolidation is confirmed when multiple independent measurement systems agree simultaneously. No single signal is sufficient. A multi-criteria scoring system is used: each of five independent criteria contributes one point, and a minimum score threshold must be met before a zone is drawn.

---

## 2. Detection Window

### What the Window Is

All five criteria operate over a shared **sliding window** of the last **N bars** (the "detection window"). At each closed bar `index`, the window covers bars `[index − N + 1 … index]` — always exactly N bars, always ending at the most recently closed candle.

```
Bar index:    ...  95   96   97   98   99  100  101  102  103  104  105
                                                 ↑
                              Window at index=105, N=10: [96 … 105]
                                               └────── 10 bars ──────┘
```

On the next closed bar (index = 106), the window shifts right by one:

```
Bar index:    ...  96   97   98   99  100  101  102  103  104  105  106
                                                  ↑
                              Window at index=106, N=10: [97 … 106]
                                                └────── 10 bars ──────┘
```

Bar 96 falls out; bar 106 enters. Every criterion is recalculated from scratch on the new window.

### What the Window Controls

| Window role | Effect |
|-------------|--------|
| **Score evaluation** | All 5 criteria are calculated against the current N bars every bar |
| **Zone start** | When score first crosses MinScore, a zone rectangle is drawn spanning the window |
| **Zone extension** | When score stays above MinScore, only the right edge of the rectangle advances — the window recalculates but zone height is fixed at creation |
| **Zone expiry** | When score drops below MinScore, the zone closes and turns grey |

### Important: Window vs Zone Width

The sliding window is **not** the same as the drawn zone rectangle:

- The **window** always has exactly N bars. It is a detection mechanism.
- The **zone rectangle** starts when detection first triggers and extends its right edge every bar that consolidation continues. A zone can span many more bars than N if the market consolidates for a long time.

```
Window (N=20):   [────────────── 20 bars ──────────────]
                      ↕ recalculates every bar

Zone rectangle:  [════════════════════════════════════════ ...extending...
                  (started when score first hit MinScore)
```

### Live Bar Exclusion

The window **never includes the currently forming (live) bar**. The indicator evaluates only on confirmed closed bars. In cTrader this is enforced by:

```csharp
if (IsLastBar) return;
```

This prevents the rectangle from repainting as price moves within an open candle.

### Warmup Period

Because C1 requires `LongATR` (default period = 50) and the window itself needs N bars, the indicator requires at least `LongAtrPeriod + N` closed bars before drawing anything. With defaults (LongAtrPeriod = 50, N = 20), the first possible detection is at bar 70.

### Window Length Tuning

The window length `N` is the most important single parameter and should be tuned to the timeframe:

| Timeframe | Recommended N | Rationale |
|-----------|--------------|-----------|
| M1        | 30–50 bars   | Fast compression, smaller windows |
| M5        | 20–30 bars   | |
| M15       | 15–25 bars   | |
| H1        | 15–20 bars   | Default design target |
| H4        | 12–18 bars   | |
| D1        | 10–15 bars   | Fewer bars needed; each bar carries more information |

**Default:** N = 20 bars (optimised for H1).

A larger N catches longer consolidations but is slower to react. A smaller N is more responsive but generates more false positives on noisy timeframes.

---

## 3. Criterion 1 — Volatility Compression (ATR Ratio)

### Concept

Short-term ATR relative to long-term ATR measures whether recent volatility has contracted compared to the market's historical volatility baseline. When the market is coiling, the short-period ATR will be significantly smaller than the long-period ATR.

### Computation

```
ShortATR  = ATR(ShortPeriod)   // computed over last ShortPeriod bars
LongATR   = ATR(LongPeriod)    // computed over last LongPeriod bars
ATR_Ratio = ShortATR / LongATR
```

ATR is the Wilder smoothed Average True Range, where each bar's True Range is:

```
TrueRange[i] = max(High[i] - Low[i],
                   abs(High[i] - Close[i-1]),
                   abs(Low[i]  - Close[i-1]))
```

### Gap-Induced Distortion and the Capped TR Baseline

Instruments with session breaks (indices, equities, FX over weekends) produce gap bars where `abs(High - Close[i-1])` or `abs(Low - Close[i-1])` can be 5–20× a normal bar's True Range. Because Wilder smoothing carries this spike for many subsequent bars, the LongATR baseline becomes artificially inflated. All three criteria that use LongATR as a denominator (C1, C2, C5) then produce ratios that are artificially low — generating false "compression detected" signals for normal price action in the days following a gap.

**Fix: Cap each bar's True Range before passing into the Wilder smoother.**

```
// Compute a short-term reference range using only intrabar range (gap-immune)
SMA_IntraRange = SMA(High[i] - Low[i], GapCapPeriod)   // default GapCapPeriod = 20

// Cap the True Range contribution
CappedTR[i] = min(TrueRange[i], GapCapMultiplier × SMA_IntraRange)
// default GapCapMultiplier = 3.0

// Feed CappedTR into Wilder smoothing for LongATR
LongATR = WilderSmooth(CappedTR, LongAtrPeriod)
```

`SMA(High - Low)` is gap-immune by definition: it only measures intrabar movement. Setting the cap at `3 × SMA(H-L)` allows large-but-legitimate bars (e.g. news bars) to contribute fully while preventing a single gap bar from poisoning the baseline for weeks.

ShortATR uses the same capped TR to keep the ratio meaningful.

**Parameter additions:**

| Parameter | cAlgo Name | Default | Description |
|-----------|------------|---------|-------------|
| Gap Cap Multiplier | `GapCapMultiplier` | 3.0 | TR is capped at this multiple of SMA(H-L) before ATR smoothing |
| Gap Cap Period | `GapCapPeriod` | 20 | SMA period for the intrabar reference range |
| Apply Gap Cap | `EnableGapCap` | true | Set false for 24/7 instruments (crypto) where gaps are rare |

For 24/7 instruments (e.g. `BTCUSD`) where gaps are negligible, set `EnableGapCap = false` to skip the cap computation entirely.

### Threshold

| Condition | ATR_Ratio Value | Interpretation |
|-----------|----------------|----------------|
| Normal volatility | 0.80 – 1.20 | No compression |
| Mild compression | 0.60 – 0.80 | Noteworthy but not sufficient alone |
| **Compression (criterion met)** | **≤ 0.65** | Recent bars are 35%+ quieter than history |
| Extreme coil | ≤ 0.45 | Severe compression — rare |

**Default threshold:** ATR_Ratio ≤ 0.65

### Parameter Sensitivity by Timeframe

- **M1/M5:** ShortPeriod = 5, LongPeriod = 50. Consider smoothing the ratio over 3 bars before applying threshold.
- **H4/D1:** ShortPeriod = 7, LongPeriod = 100. Ratio is more stable.
- **Default (H1):** ShortPeriod = 7, LongPeriod = 50.

### Pseudocode

```
function CriterionATRRatio(bars, shortPeriod, longPeriod, threshold):
    shortATR = CalculateATR(bars, shortPeriod)
    longATR  = CalculateATR(bars, longPeriod)
    if longATR == 0: return false
    ratio = shortATR / longATR
    return ratio <= threshold
```

---

## 4. Criterion 2 — Price Range Tightness (N-bar Range Ratio)

### Concept

The absolute range from the Highest High to the Lowest Low over the detection window, normalised by the current ATR. When the entire N-bar swing fits within a small multiple of ATR, price is confined to a tight band.

### Computation

```
WindowHigh      = max(High[i])  for i in [0 .. N-1]
WindowLow       = min(Low[i])   for i in [0 .. N-1]
WindowRange     = WindowHigh - WindowLow
NormalizedRange = WindowRange / LongATR
```

### Threshold by Timeframe

| Timeframe | Threshold (criterion met when NormalizedRange ≤) | Rationale |
|-----------|--------------------------------------------------|-----------|
| M1–M5     | 1.2 | Very tight on faster timeframes |
| M15       | 1.5 | |
| **H1**    | **2.0** (default) | 20 bars fitting within 2 ATR |
| H4        | 2.5 | More room for intraday cycles |
| D1        | 3.0 | |

**Interpretation:** On H1 with ATR = 20 pips, a threshold of 2.0 means the entire N-bar window spans ≤ 40 pips. A healthy trending market typically produces N-bar ranges of 4–8 × ATR.

### Pseudocode

```
function CriterionRangeTightness(bars, N, longATR, threshold):
    windowHigh = max(bars[0..N-1].High)
    windowLow  = min(bars[0..N-1].Low)
    windowRange = windowHigh - windowLow
    if longATR == 0: return false
    normalizedRange = windowRange / longATR
    return normalizedRange <= threshold
```

---

## 5. Criterion 3 — Bar Overlap Score (Structural Congestion)

### Concept

Counts what percentage of the last N bars overlap each other structurally. "Overlap" is defined as: bar A and bar B overlap if their High-Low ranges share at least some common price space. Specifically, bar A overlaps bar B if `A.Low < B.High AND A.High > B.Low`.

When most bars overlap the same zone, price is physically congested at that level — this is structural consolidation, not merely low volatility.

### Computation

For each bar `i` in the window, compute how many other bars in the window it overlaps:

```
overlapCount[i] = count of bars j (j != i) where:
    bars[i].Low  <= bars[j].High  AND   // inclusive: exact High == Low counts as overlap
    bars[i].High >= bars[j].Low

overlapRatio[i] = overlapCount[i] / (N - 1)
```

Then compute the overall overlap score as the fraction of bars that overlap with at least a given proportion of the window:

```
OverlapScore = count(overlapRatio[i] >= MinPairwiseOverlap) / N
```

Where `MinPairwiseOverlap` is a secondary threshold: a bar "participates in congestion" if it overlaps at least 50% of the other bars in the window.

### Primary Threshold

**Criterion met when OverlapScore ≥ 0.70** (70% or more of bars are participating in the congestion zone).

| OverlapScore | Interpretation |
|-------------|----------------|
| < 0.50 | Trending or scattered price action |
| 0.50 – 0.70 | Mild overlapping, not definitive |
| **≥ 0.70** | Structural congestion confirmed |
| ≥ 0.85 | Very tight physical congestion |

### Pseudocode

```
function CriterionBarOverlap(bars, N, minPairwiseOverlap, zoneThreshold):
    qualifying = 0
    for i in 0..N-1:
        overlapping = 0
        for j in 0..N-1:
            if i == j: continue
            if bars[i].Low <= bars[j].High AND bars[i].High >= bars[j].Low:
                overlapping++
        ratio = overlapping / (N - 1)
        if ratio >= minPairwiseOverlap:
            qualifying++
    overlapScore = qualifying / N
    return overlapScore >= zoneThreshold
```

**Complexity note:** O(N²) per bar. With N ≤ 30 (900 comparisons per bar) this is negligible in cTrader's Calculate() loop.

---

## 6. Criterion 4 — Directional Balance (Bull/Bear Equilibrium)

### Concept

A coiling market has neither bulls nor bears in control. Two complementary sub-signals are measured and **both** must pass:

- **Sub-signal A — Bar Balance:** The ratio of up-bars to down-bars in the window should be close to 0.5 (equal split).
- **Sub-signal B — Choppiness Index:** The net price movement over the window is small relative to the sum of individual bar moves.

### Sub-signal A: Body-Weighted Directional Balance

A binary bar count (up-bar vs down-bar) fails in tight coils where many doji bars close fractionally above their open — technically "up" bars but carrying no directional significance. Instead, weight each bar's directional contribution by its **body size relative to its True Range**. A doji with a 0.1-pip body on a 10-pip range contributes almost nothing; a full-bodied candle contributes nearly its full weight.

```
bullWeight = 0.0
bearWeight = 0.0

for each bar i in window:
    body      = abs(Close[i] - Open[i])
    trueRange = max(High[i] - Low[i],
                    abs(High[i] - Close[i-1]),
                    abs(Low[i]  - Close[i-1]))
    if trueRange == 0: continue
    bodyRatio = body / trueRange          // 0.0 = pure doji, 1.0 = full-body bar

    if Close[i] > Open[i]:
        bullWeight += bodyRatio
    elif Close[i] < Open[i]:
        bearWeight += bodyRatio
    // neutral bars contribute 0 to both sides

totalWeight = bullWeight + bearWeight
if totalWeight == 0: return false        // all dojis — treat as balanced by default → sub-signal passes

BalanceRatio = min(bullWeight, bearWeight) / max(bullWeight, bearWeight)
// 0.0 = all weight on one side, 1.0 = perfectly balanced
```

**Sub-signal A threshold:** BalanceRatio ≥ 0.60

This correctly handles coil environments: a window of tight dojis produces near-zero total weight and a BalanceRatio that trivially passes, while a window with a few large-bodied bars in one direction correctly fails the balance test.

### Sub-signal B: Choppiness Index

The Choppiness Index (CI) measures how non-directional recent price action is:

```
SumTrueRanges = sum of TrueRange[i] for i in window
WindowRange   = max(High) - min(Low) over window

CI = log10(SumTrueRanges / WindowRange) / log10(N)
// Normalised to [0, 1]
// CI near 1.0 = maximum choppiness (perfectly sideways)
// CI near 0.0 = maximum trending (straight line)
```

**Sub-signal B threshold:** CI ≥ 0.618 (the "golden ratio" threshold commonly used in standard CI implementations; values above 0.618 indicate choppy/ranging conditions)

### Combined Logic

```
function CriterionDirectionalBalance(bars, N):
    // Sub-signal A
    upBars      = count(Close[i] > Open[i])
    downBars    = count(Close[i] < Open[i])
    neutralBars = N - upBars - downBars
    adjUp   = upBars   + neutralBars * 0.5
    adjDown = downBars + neutralBars * 0.5
    balanceRatio = min(adjUp, adjDown) / max(adjUp, adjDown)
    subA = (balanceRatio >= 0.60)

    // Sub-signal B
    sumTR  = sum(TrueRange[i]) for i in window
    wRange = max(High) - min(Low) over window
    if wRange == 0 OR sumTR == 0: return false
    CI = log10(sumTR / wRange) / log10(N)
    subB = (CI >= 0.618)

    return subA AND subB
```

Requiring both sub-signals to agree prevents false positives from e.g. a one-sided squeeze where all bars are up-bars but volatility has contracted (a bullish flag, not a neutral coil).

---

## 7. Criterion 5 — Close Price Clustering (Statistical Tightness)

### Concept

Even if the High-Low ranges are scattered, if closing prices are tightly clustered, the market is settling at an equilibrium price on a bar-by-bar basis. Standard deviation of closes, normalised by ATR, captures this.

### Computation

```
MeanClose   = mean(Close[i]) for i in window
StdDevClose = sqrt(mean((Close[i] - MeanClose)^2))

ClusterRatio = StdDevClose / LongATR
```

### Threshold

| ClusterRatio | Interpretation |
|-------------|----------------|
| > 0.60 | Closes are widely scattered — trending or volatile |
| 0.30 – 0.60 | Moderate clustering |
| **≤ 0.30** | Tight close clustering — consolidation confirmed |
| ≤ 0.15 | Extremely tight — rare; often precedes violent move |

**Default threshold:** ClusterRatio ≤ 0.30

### Timeframe Tuning

| Timeframe | Recommended Threshold |
|-----------|----------------------|
| M1–M5     | ≤ 0.35 (noisier closes) |
| M15–H1    | ≤ 0.30 (default) |
| H4–D1     | ≤ 0.25 |

### Pseudocode

```
function CriterionCloseClustering(bars, N, longATR, threshold):
    closes    = [bars[i].Close for i in 0..N-1]
    meanClose = average(closes)
    stdDev    = sqrt(average((c - meanClose)^2 for c in closes))
    if longATR == 0: return false
    return (stdDev / longATR) <= threshold
```

---

## 8. Zone Boundary Calculation

Zone boundaries are computed fresh for each bar using the **80th/20th percentile** of bar highs and lows in the N-bar lookback window.

```
SortedHighs      = sort(bars.High[0..N-1])   // ascending
SortedLows       = sort(bars.Low[0..N-1])    // ascending

ZoneUpper = SortedHighs[floor(N * 0.80)]     // 80th percentile of highs
ZoneLower = SortedLows[floor(N * 0.20)]      // 20th percentile of lows
```

**Why percentile instead of raw max/min:**  
A single spike wick in the lookback window inflates the raw `max(High)` / `min(Low)` far beyond the actual consolidation range. Trimming the top and bottom 20% of extremes eliminates wick outliers while preserving the structural price band that the majority of bars actually traded within.

**Pseudocode:**

```
function PercentileBounds(bars, N):
    highs = [bars[i].High for i in 0..N-1]
    lows  = [bars[i].Low  for i in 0..N-1]
    sort(highs), sort(lows)
    p80 = min(floor(N * 0.80), N - 1)
    p20 = floor(N * 0.20)
    return highs[p80], lows[p20]
```

Boundaries are recalculated independently for every bar — they are not locked or carried forward from bar to bar.

---

## 9. Scoring System

### Score Computation

Each criterion returns a boolean (met = 1 point, not met = 0 points):

| # | Criterion | Points |
|---|-----------|--------|
| 1 | ATR Ratio ≤ threshold | 0 or 1 |
| 2 | N-bar Range Ratio ≤ threshold | 0 or 1 |
| 3 | Bar Overlap Score ≥ threshold | 0 or 1 |
| 4 | Directional Balance (both sub-signals met) | 0 or 1 |
| 5 | Close Clustering Ratio ≤ threshold | 0 or 1 |
| **Total** | **CZH Score** | **0 – 5** |

### Score Interpretation Table

| Score | Zone Classification | Rendering | Rationale |
|-------|--------------------|-----------|----|
| 0 – 2 | No zone | Nothing drawn | Insufficient evidence |
| 3 | Weak Consolidation Zone | Faint shaded rectangle, dashed border | Possible coil, not confirmed |
| 4 | Strong Consolidation Zone | Solid shaded rectangle, solid border | Clear consolidation — tradeable setup |
| 5 | Extreme Coil | Bright filled rectangle, thick border, label | All signals agree — high-probability move pending |

---

## 10. Zone Lifecycle

### Per-Bar Evaluation Model

Each closed bar is evaluated **independently**. There is no zone state carried between bars. On every closed bar:

```
1. Compute LongATR, ShortATR at this bar index
2. Evaluate all enabled criteria against the N-bar lookback window
3. Sum score
4. If score >= MinScore:
       compute PercentileBounds(index)
       draw a rectangle ID="CZH_B{index}" spanning:
           Time1 = OpenTime[index - N + 1]
           Time2 = OpenTime[index + 1]
           Y1    = ZoneUpper (80th percentile of window highs)
           Y2    = ZoneLower (20th percentile of window lows)
   Else:
       draw nothing for this bar
```

No state variables are needed. No zone is "extended" — every bar draws its own rectangle or nothing.

### How Consolidation Periods Appear Visually

Because consecutive qualifying bars each draw their own rectangle (with boundaries derived from their respective lookback windows), a run of consolidating bars produces a contiguous shaded band on the chart. When the market breaks out and bars stop qualifying, the shading simply stops — no explicit expiry step is needed.

```
Bar:    ...  102  103  104  105  106  107  108  109  110  ...
Score:  ...    1    2    3    4    4    3    2    1    1   ...
Drawn:  ...    ·    ·    ■    ■    ■    ■    ·    ·    ·   ...
                         └────── contiguous band ──────┘
```

A single bar with a score below MinScore in the middle of an otherwise qualifying run leaves a 1-bar gap in the shading.

### Historical Zones

Past qualifying bars' rectangles remain on the chart permanently. They serve as historical S/R reference levels — areas where consolidation occurred previously provide price memory for future reactions. These are simply the rectangles drawn for past bars; no special historical state or opacity change is applied.

---

## 11. Visual Design

### Rectangle Fill

| Score | Fill Color | Fill Opacity | Border Color | Border Style | Thickness |
|-------|-----------|-------------|--------------|--------------|-----------|
| 3 (Weak) | Yellow `#FFD700` | 12% | Yellow | Dashed | 1px |
| 4 (Strong) | Orange `#FFA500` | 22% | Orange | Solid | 1px |
| 5 (Extreme) | Red `#FF4444` | 35% | Red | Solid | 2px |
| Historical | Grey `#888888` | 8% | None | None | 0 |

All colors are configurable parameters. Opacity values are chosen to remain visible without obscuring the candles underneath.

### Labels

Labels appear on Score = 5 zones always, and Score = 4 zones optionally (`ShowStrongLabels` parameter). Label is placed at the right edge of the rectangle, vertically centred:

```
"CZH [Score]/5  |  [N] bars  |  [ZoneWidth] pip zone"

Example: "CZH 5/5  |  20 bars  |  24.3 pip zone"
```

Font: 8pt bold, same color as zone border, transparent background.

### Visual States Summary

```
[ Bar does not qualify (score < MinScore) ]
  → Nothing drawn for that bar.

[ Bar qualifies (score >= MinScore) ]
  → A shaded rectangle is drawn for that bar only.
  → Rectangle spans from OpenTime[index - N + 1] to OpenTime[index + 1].
  → Height = 80th/20th percentile of window highs/lows.
  → Color reflects the score of this specific bar.

[ Consecutive qualifying bars ]
  → Adjacent rectangles naturally form a contiguous shaded band.

[ Historical (past qualifying bars) ]
  → Rectangles remain on chart as S/R reference.
  → Color/opacity unchanged — same as when they were drawn.
```

---

## 12. Configurable Parameters

Parameters are grouped in the cTrader indicator settings panel. Each criterion has its own group containing an enable toggle and its threshold, so criteria can be switched on/off individually during tuning.

### Detection Group

| cAlgo Name | Default | Min | Max | Description |
|------------|---------|-----|-----|-------------|
| `LookbackBars` | 20 | 5 | 60 | Sliding evaluation window size (N) |
| `ShortAtrPeriod` | 7 | 2 | 30 | ATR period for short-term volatility (C1) |
| `LongAtrPeriod` | 50 | 10 | 200 | ATR period for baseline volatility (C1, C2, C5) |
| `MinScore` | 3 | 1 | 5 | Minimum enabled-criteria score to draw a zone |

### C1 ATR Compression Group

| cAlgo Name | Default | Description |
|------------|---------|-------------|
| `C1Enable` | true | Enable/disable this criterion |
| `AtrRatioThreshold` | 0.65 | ShortATR / LongATR must be ≤ this (tighter = stricter) |

### C2 Range Tightness Group

| cAlgo Name | Default | Description |
|------------|---------|-------------|
| `C2Enable` | true | Enable/disable this criterion |
| `RangeRatioThreshold` | 1.5 | WindowRange / LongATR must be ≤ this |

### C3 Bar Overlap Group

| cAlgo Name | Default | Description |
|------------|---------|-------------|
| `C3Enable` | true | Enable/disable this criterion |
| `OverlapPct` | 70 | % of bars that must overlap ≥50% of peers |

### C4 Choppiness Group

| cAlgo Name | Default | Description |
|------------|---------|-------------|
| `C4Enable` | true | Enable/disable this criterion |
| `ChoppinessThreshold` | 0.618 | Choppiness Index must be ≥ this |

### C5 Close Clustering Group

| cAlgo Name | Default | Description |
|------------|---------|-------------|
| `C5Enable` | true | Enable/disable this criterion |
| `ClusterThreshold` | 0.30 | StdDev(Close) / LongATR must be ≤ this |

### Visual Group

| cAlgo Name | Default | Description |
|------------|---------|-------------|
| `FillOpacity` | 60 | Rectangle fill opacity (0–255); applies to all score levels |
| `ShowDebug` | true | Show live score label in top-right corner |

### Score Colours (not user-configurable, hardcoded)

| Score | Colour |
|-------|--------|
| 3 | Orange `RGB(255, 165, 0)` |
| 4 | Dark Orange `RGB(255, 120, 0)` |
| 5 | Red-Orange `RGB(220, 60, 0)` |

### Tuning Workflow

1. Enable all criteria, set MinScore = 3. Observe where zones appear.
2. Toggle individual criteria off one at a time using the enable flags. The debug label shows `"off"` for disabled criteria and adjusts the score denominator (e.g. `2/4` if one is off).
3. Tighten thresholds for criteria that trigger too easily. Loosen thresholds for criteria that never pass.
4. Re-enable all criteria once thresholds are calibrated. Raise MinScore to 4 for stricter detection.

### Recommended Starting Settings by Timeframe

| Timeframe | LookbackBars | AtrRatioThreshold | RangeRatioThreshold | ClusterThreshold |
|-----------|-------------|-------------------|---------------------|-----------------|
| M5        | 25 | 0.60 | 1.2 | 0.25 |
| M15       | 20 | 0.63 | 1.5 | 0.28 |
| **H1**    | **20** | **0.65** | **1.5** | **0.30** |
| H4        | 15 | 0.65 | 2.0 | 0.30 |
| D1        | 12 | 0.70 | 2.5 | 0.35 |

---

## 13. Entry Timing Usage

The CZH indicator does not generate entry signals. It provides contextual zone awareness. The following setups describe how a discretionary trader uses the zones.

### Setup 1 — Breakout Confirmation Entry

**When to use:** A Score ≥ 4 zone has been forming for 10+ bars. Price begins to push toward one edge.

**Entry trigger:** Wait for a candle to close cleanly outside the zone boundary by at least `BreakThreshold × ATR`. The zone will begin fading as you enter.

**Stop loss:** Opposite zone boundary (zone's full width = risk unit).

**Target:** Minimum 1:1.5 risk/reward from the zone boundary.

**Filter:** Reject if the breakout bar's range is less than 0.5 × ATR (no conviction), or if the breakout occurs immediately before a major news event.

### Setup 2 — Zone Boundary Fade (Mean Reversion)

**When to use:** Price probes the upper or lower boundary of an active Score = 5 zone but fails to close outside it. A pin bar or engulfing candle forms at the boundary.

**Entry trigger:** Enter in the direction back toward zone midpoint on the first candle that closes back inside the zone after the boundary probe.

**Stop loss:** Outside the zone boundary by 0.3 × ATR.

**Target:** Zone midpoint (50% of zone width), or opposite boundary if momentum is strong.

**Rationale:** A Score = 5 zone has high structural support/resistance at both boundaries. A failed breakout attempt is a high-probability rejection trade.

### Setup 3 — Multi-Timeframe Confluence Entry

**When to use:** A CZH zone is active on H4. On H1, a Score ≥ 4 zone forms entirely within the H4 zone boundaries. Both timeframes signal extreme compression simultaneously.

**Entry trigger:** Watch for the H1 zone to break while the H4 zone remains intact. The H1 breakout direction becomes the trade direction.

**Stop loss:** Below the H1 zone boundary on the breakout bar.

**Target:** H4 zone boundary on the breakout side.

**Rationale:** Nested consolidation zones across timeframes indicate compression operating at multiple structural levels simultaneously — among the strongest pre-move signals available.

---

## 14. cTrader C# Implementation Notes

The following covers the critical API patterns for a cAlgo 4.x implementation.

### Class Declaration

```csharp
using cAlgo.API;
using cAlgo.API.Internals;
using cAlgo.API.Indicators;
using System;
using System.Collections.Generic;
using System.Linq;

[Indicator(
    Name         = "Consolidation Zone Highlighter",
    ShortName    = "CZH",
    IsOverlay    = true,          // CRITICAL: renders on price chart
    AutoRescale  = false,
    AccessRights = AccessRights.None
)]
public class ConsolidationZoneHighlighter : Indicator
{
    [Parameter("Lookback Bars", DefaultValue = 20, MinValue = 8, MaxValue = 60)]
    public int LookbackBars { get; set; }

    [Parameter("Min Score to Draw", DefaultValue = 3, MinValue = 1, MaxValue = 5)]
    public int MinScore { get; set; }

    // ... all other parameters ...

    private AverageTrueRange _shortAtr;
    private AverageTrueRange _longAtr;
    private string _activeZoneId;
    private int _activeZoneScore;
    private List<string> _historicalZoneIds = new List<string>();
}
```

`IsOverlay = true` is mandatory — without it the indicator opens in a sub-panel, not on the candle chart.

### Initialization

```csharp
protected override void Initialize()
{
    _shortAtr = Indicators.AverageTrueRange(ShortAtrPeriod, MovingAverageType.Wilders);
    _longAtr  = Indicators.AverageTrueRange(LongAtrPeriod,  MovingAverageType.Wilders);
    // Note: if EnableGapCap is true, a custom Wilder smoother over CappedTR is required
    // (see Gap-Capped ATR section). The built-in AverageTrueRange cannot accept a
    // pre-capped series — maintain a manual Wilder accumulator for this case.
}
```

Use `MovingAverageType.Wilders` for standard Wilder smoothing. Do not reimplement ATR manually unless using the gap cap.

### Wilder Warm-up: Separate the Smoothing Loop from the Scoring Guard

Wilder's smoothing is an EMA. A 50-period Wilder ATR requires approximately 150–200 bars to shed the weight of its starting value and converge on accurate output. If the scoring guard (`if (index < LongAtrPeriod + LookbackBars) return`) is applied to the ATR accumulation as well as the scoring logic, the ATR calculation restarts from bar 70 with no warm-up history, producing inaccurate baselines for the first few hundred bars of any chart load.

**Rule: Let ATR accumulation run from bar 0. Apply the scoring guard only to zone detection.**

```csharp
public override void Calculate(int index)
{
    // Step 1: Always update gap-capped ATR accumulators — runs from bar 0
    if (EnableGapCap)
        UpdateCappedATR(index);   // maintains _customLongATR, _customShortATR

    bool isLiveBar = (index == Bars.Count - 1);

    // Step 2: Apply scoring guard AFTER ATR is updated
    if (index < LongAtrPeriod * 3 + LookbackBars)
    {
        // ATR has updated but zone scoring is suppressed during warm-up
        // Using 3× LongAtrPeriod gives the Wilder smoother time to converge
        return;
    }

    if (isLiveBar)
    {
        // ... live bar probe/breakout logic ...
        return;
    }

    // Closed bar: scoring and zone management
    // ...
}

private void UpdateCappedATR(int index)
{
    // Run this even during warm-up; only the result is used post-guard
    double intraRange = Bars.HighPrices[index] - Bars.LowPrices[index];

    // SMA of intrabar range (gap-immune reference)
    _smaIntraRangeSum += intraRange;
    if (index >= GapCapPeriod)
        _smaIntraRangeSum -= (Bars.HighPrices[index - GapCapPeriod]
                              - Bars.LowPrices[index - GapCapPeriod]);
    double smaIntraRange = _smaIntraRangeSum / Math.Min(index + 1, GapCapPeriod);

    // True Range
    double prevClose = index > 0 ? Bars.ClosePrices[index - 1] : Bars.ClosePrices[index];
    double trueRange = Math.Max(intraRange,
                       Math.Max(Math.Abs(Bars.HighPrices[index] - prevClose),
                                Math.Abs(Bars.LowPrices[index]  - prevClose)));

    // Cap the TR
    double cappedTR = Math.Min(trueRange, GapCapMultiplier * smaIntraRange);

    // Wilder smoothing: ATR[i] = (ATR[i-1] * (n-1) + TR[i]) / n
    if (index == 0)
    {
        _customLongATR  = cappedTR;
        _customShortATR = cappedTR;
    }
    else
    {
        _customLongATR  = (_customLongATR  * (LongAtrPeriod  - 1) + cappedTR) / LongAtrPeriod;
        _customShortATR = (_customShortATR * (ShortAtrPeriod - 1) + cappedTR) / ShortAtrPeriod;
    }
}
```

**Corresponding state variables:**

```csharp
private double _customLongATR;
private double _customShortATR;
private double _smaIntraRangeSum;   // running sum for SMA(H-L, GapCapPeriod)
```

When `EnableGapCap = false`, use `_longAtr.Result[index]` and `_shortAtr.Result[index]` from the built-in indicators directly. The warm-up guard still applies: use `LongAtrPeriod * 3` as the minimum bar count before scoring begins.

### Calculate Loop (Split Closed-Bar Scoring / Live-Bar Breakout)

A single blanket guard (`if (index == Bars.Count - 1) return;`) prevents repainting but also blinds the indicator to live price action. On higher timeframes (H1, H4) this means a breakout from a zone may be half complete before the indicator registers it — destroying the R/R on Setup 1 entries.

The solution is a **split evaluation**:
- **Zone scoring** (C1–C5, boundary calculation) runs only on closed bars — no repainting risk.
- **Breakout detection** runs on the live bar using the *locked* zone boundaries from the last completed bar — no scoring recalculation, so no repainting.

```csharp
public override void Calculate(int index)
{
    if (index < LongAtrPeriod + LookbackBars) return;

    bool isLiveBar = (index == Bars.Count - 1);

    if (isLiveBar)
    {
        // Live bar: only evaluate breakout/probe logic — no scoring, no chart object updates.
        // Chart object updates (Time2 extension) happen ONCE per new bar, not per tick.
        if (_activeZoneId != null && _lockedZoneUpper > 0)
        {
            // Extend the rectangle right edge only on the tick that opens a new bar
            if (_lastUpdatedBarCount != Bars.Count)
            {
                _lastUpdatedBarCount = Bars.Count;
                if (_activeRect != null)
                    _activeRect.Time2 = Bars.OpenTimes[index] + TimeFrame.ToTimeSpan();
            }

            // Probe/breakout check runs every tick (read-only on price, no chart writes)
            double liveClose = Bars.ClosePrices[index];  // updates tick-by-tick
            double longATR   = _longAtr.Result[index - 1]; // use last closed bar's ATR
            EvaluateLiveBreakout(liveClose, longATR);
        }
        return;
    }

    // Closed bar: full score computation
    double closedLongATR  = _longAtr.Result[index];
    double closedShortATR = _shortAtr.Result[index];
    if (closedLongATR == 0) return;

    // Always check for boundary breakout BEFORE computing new boundaries
    if (_activeZoneId != null)
        CheckClosedBarBreakout(index, closedLongATR);

    // Compute new zone boundaries from the current N-bar window
    (double newUpper, double newLower) = ComputeZoneBoundaries(index, closedLongATR);

    int score = ComputeScore(index, closedLongATR, closedShortATR);

    if (score >= MinScore)
        HandleActiveZone(index, score, newUpper, newLower);
    else
        HandleScoreDecay(index, score);

    // CRITICAL: Always update locked boundaries at end of each closed bar
    // while zone is Active (not Warning). The live bar reads these on every tick.
    // If in Warning or no zone, locked values remain frozen from when Warning began.
    if (_activeZoneId != null && !_zoneInWarning)
    {
        _lockedZoneUpper = newUpper;
        _lockedZoneLower = newLower;
    }
}

private void EvaluateLiveBreakout(double livePrice, double atr)
{
    bool piercingUpper = livePrice > _lockedZoneUpper + BreakThresholdATR * atr;
    bool piercingLower = livePrice < _lockedZoneLower - BreakThresholdATR * atr;

    if (piercingUpper || piercingLower)
    {
        // Enter Intrabar Probe state — DO NOT expire the zone yet.
        // Zone only dies on a confirmed bar close outside the boundary (see closed-bar path).
        if (!_zoneInProbe)
        {
            _zoneInProbe = true;
            // Visual: flash border to a distinct probe color (e.g. bright white/cyan)
            // to signal traders to watch for rejection or confirmation close
            SetProbeVisual();
        }
    }
    else if (_zoneInProbe)
    {
        // Price pulled back inside — cancel probe state, restore active visual
        _zoneInProbe = false;
        SetActiveVisual(_activeZoneScore);
    }
}

// Called on closed bars only — this is the authoritative expiry check
private void CheckClosedBarBreakout(int index, double longATR)
{
    double closedClose = Bars.ClosePrices[index];
    if (closedClose > _lockedZoneUpper + BreakThresholdATR * longATR ||
        closedClose < _lockedZoneLower - BreakThresholdATR * longATR)
    {
        ExpireActiveZone(broken: true);
    }
    // Whether or not the closed bar broke, reset probe state — bar is closed
    _zoneInProbe = false;
}
```

**State variables added:**

```csharp
private bool _zoneInProbe;   // true when live price is outside boundary but bar not yet closed
```

**What this achieves:**

| Scenario | Behaviour |
|----------|-----------|
| Live tick pierces boundary | Zone enters Probe state — border flashes, **zone stays alive** |
| Bar closes back inside | Probe cancelled, zone returns to Active visual |
| Bar closes outside boundary | `CheckClosedBarBreakout` fires → `ExpireActiveZone(broken: true)` |
| Setup 2 pin bar at boundary | Zone survives the wick test and remains active for the rejection trade |

This directly preserves Setup 2 (Zone Boundary Fade): market maker stop-hunts that pierce and reject will show the Probe visual without killing the zone, and traders see the rejection candle form against an intact active zone.

**Caveat:** Traders who prefer the aggressive intrabar expiry can set `LiveBreakoutEnabled = false`, which skips `EvaluateLiveBreakout()` entirely and relies solely on `CheckClosedBarBreakout()`. The Probe state is then never entered.

### Drawing Rectangles

```csharp
private void DrawOrUpdateZone(int startIndex, int endIndex,
                               double upper, double lower,
                               int score, string zoneId)
{
    var fillColor   = GetFillColor(score);
    var borderColor = GetBorderColor(score);

    var rect = Chart.DrawRectangle(
        zoneId,
        Bars.OpenTimes[startIndex],
        upper,
        Bars.OpenTimes[endIndex],
        lower,
        borderColor
    );

    rect.IsFilled  = true;
    rect.Color     = fillColor;
    rect.Thickness = (score == 5) ? 2 : 1;
    rect.LineStyle = (score == 3) ? LineStyle.Dots : LineStyle.Solid;
}
```

Calling `Chart.DrawRectangle()` with an **existing name** updates the rectangle in place — no duplicate is created. Use this to extend the right edge each bar:

```csharp
// Extend zone right edge on each new bar while active
rect.Time2 = Bars.OpenTimes[index] + TimeFrame.ToTimeSpan();
```

### Color with Opacity

```csharp
private Color GetFillColor(int score)
{
    int alpha = score == 5 ? (int)(OpacityExtreme * 2.55)
              : score == 4 ? (int)(OpacityStrong  * 2.55)
                           : (int)(OpacityWeak    * 2.55);
    return score == 5 ? Color.FromArgb(alpha, 255,  68,  68)   // Red
         : score == 4 ? Color.FromArgb(alpha, 255, 165,   0)   // Orange
                      : Color.FromArgb(alpha, 255, 215,   0);  // Yellow
}
```

`Color.FromArgb(alpha, r, g, b)` where alpha is 0–255.

### Zone ID Strategy

```csharp
// Generate a stable, unique ID when zone first activates
string zoneId = $"CZH_{Bars.OpenTimes[index]:yyyyMMddHHmmss}";
```

Reuse this ID on every subsequent Calculate() call to update the existing rectangle rather than create new objects.

### Labels

```csharp
private void DrawLabel(int barIndex, double upper, double lower,
                        int score, int barCount, string zoneId)
{
    if (!ShowLabels) return;
    if (score < 5 && !ShowStrongLabels) return;

    double zoneWidthPips = (upper - lower) / Symbol.PipSize;
    string text = $"CZH {score}/5  |  {barCount} bars  |  {zoneWidthPips:F1} pip zone";

    Chart.DrawText(
        $"{zoneId}_label",
        text,
        Bars.OpenTimes[barIndex],
        (upper + lower) / 2.0,
        GetBorderColor(score)
    );
}
```

### Historical Zone Management

```csharp
private void ExpireActiveZone()
{
    if (_activeZoneId == null) return;

    // Use _activeRect (direct reference) rather than FindObject — faster and null-safe
    // _activeRect is set null when zone is created and assigned when DrawRectangle succeeds
    if (_activeRect != null)
    {
        int histAlpha      = (int)(HistoricalOpacity * 2.55);
        _activeRect.Color  = Color.FromArgb(histAlpha, 136, 136, 136);
        _activeRect.Thickness = 0;
        // Right edge is already at last active bar — leave it locked
        _activeRect = null;  // release reference; historical rect is no longer tracked
    }

    _historicalZoneIds.Add(_activeZoneId);

    // Enforce max historical zones limit
    // Always null-check before removal — user may have manually deleted the object
    while (_historicalZoneIds.Count > HistoricalZonesMax)
    {
        string idToRemove = _historicalZoneIds[0];
        _historicalZoneIds.RemoveAt(0);  // remove from list first regardless of chart state

        if (Chart.FindObject(idToRemove) != null)
            Chart.RemoveObject(idToRemove);

        // Also clean up associated label if present
        string labelId = idToRemove + "_label";
        if (Chart.FindObject(labelId) != null)
            Chart.RemoveObject(labelId);
    }

    _activeZoneId    = null;
    _activeZoneScore = 0;
}
```

### Median and Percentile Helpers

```csharp
private double Median(double[] values)
{
    var sorted = (double[])values.Clone();
    Array.Sort(sorted);
    int n = sorted.Length;
    return n % 2 == 1 ? sorted[n / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0;
}

private double Percentile(double[] sorted, double pct)
{
    // sorted must already be sorted ascending
    int idx = (int)Math.Floor(sorted.Length * pct);
    idx = Math.Min(idx, sorted.Length - 1);
    return sorted[idx];
}
```

### Performance Notes

- **Overlap calculation:** O(N²) per bar. N = 20 → 400 comparisons; N = 60 → 3,600. Negligible for real-time. On a 50,000-bar history load this is 20M comparisons total — runs in under a second on modern hardware.
- **Zone boundary sweep:** The previous ATR/10-resolution price sweep is replaced with enumeration of actual bar High/Low values (Section 8, Step 1). This is O(N log N) per bar and removes the history-load bottleneck that was the primary performance risk.
- **ATR caching:** Store `_longAtr.Result[index]` and `_shortAtr.Result[index]` in local variables at the top of Calculate() — avoid repeated indexer calls inside the inner loops.
- **Choppiness Index:** Requires `Math.Log10()` — `System.Math` is available by default in cAlgo.
- **Percentile calculation:** `Array.Sort()` on 2N doubles is O(N log N) — acceptable.
- **Chart.DrawXxx():** All draw calls are non-blocking in cTrader and safe to call from Calculate(). Only create new rectangle objects on zone state transitions; update existing objects (`Time2`, `Color`) on every bar while the zone is active — this is significantly cheaper than recreating rectangles.
- **Do not** call `Chart.FindObject()` on every bar — maintain a direct reference to the active `ChartRectangle` object in `_activeRect` to avoid the lookup overhead during history replay.

### Optional: Sub-Chart Output for cBot Use

```csharp
[Output("CZH Score", LineColor = "Gold", PlotType = PlotType.Histogram, Thickness = 2)]
public IndicatorDataSeries CzhScore { get; set; }

// In Calculate():
CzhScore[index] = score;
```

This allows a cBot to read the score programmatically via `indicator.CzhScore[index]`.

---

## 15. Limitations and False Signal Conditions

### What the Indicator Cannot Do

1. **No directional prediction.** CZH provides zero information about which way price will break. Without a separate directional bias, a trader faces 50/50 odds on breakout direction.

2. **No magnitude prediction.** Zone width does not predict subsequent move size. A 10-pip zone can produce a 200-pip move; a 50-pip zone can produce a 30-pip breakout that immediately reverses.

3. **No timing prediction.** A Score = 5 zone can persist for many bars. The indicator confirms conditions are ripe, not when the move will start.

4. **Zone boundary drift.** Because boundaries are computed from the sliding N-bar window, they shift slightly as each new bar enters and the oldest bar exits. This is by design — it is not signal repainting.

### Market Conditions That Produce False Signals

| Condition | Why It Creates False Positives | Mitigation |
|-----------|-------------------------------|------------|
| Pre-holiday / weekend low liquidity | Low ATR and narrow range satisfy C1 and C2 without structural congestion | Apply session filter; avoid zones forming outside main market hours |
| Slow shallow trend | A shallow uptrend on compressed volatility can pass all five criteria | Criterion 4 (choppiness) filters most trending cases, but very slow trends can slip through |
| Data gaps and spread widening | Artificial bar ranges distort ATR and overlap calculations | Use reliable data feeds; filter bars with zero or abnormal volume |
| Very small N (< 10) | With N = 8, almost any 8-bar doji cluster passes all criteria | Never use LookbackBars below 10; prefer 15+ for intraday |
| Ranging market at a major S/R extreme | All five criteria can be met near a level where price is about to resolve sharply | Awareness of the higher timeframe context is required; CZH does not know what lies beyond its window |

### Angular Compression (Wedge / Triangle Blindspot)

The CZH is designed around a **horizontal** bounding box. It requires price to oscillate within a roughly flat range — neither side making directional progress. This is a deliberate design choice that creates a significant blind spot for **angular compression** patterns:

| Pattern | Why CZH Misses It |
|---------|-------------------|
| Ascending Triangle | Lows are rising → WindowRange tightens, but the rising lows constitute a micro-trend that depresses the Choppiness Index (C4-B) below 0.618. Score drops at the apex — exactly when the setup is best. |
| Descending Triangle | Mirror image: falling highs create the same C4-B failure. |
| Symmetrical Triangle / Pennant | Both sides converging → early detection may work, but CI drops as the apex tightens since progressively higher lows / lower highs are directional by definition. |
| Wedge (rising or falling) | Strong directional component in both swing highs and lows — fails C4 entirely. |

**Why this is not patched here:** Fixing it would require replacing the rectangular zone model with an angular one (two regression lines converging). That is a fundamentally different indicator — adding it to CZH would produce a system that tries to do everything and validates nothing clearly.

**Recommended approach:** Treat CZH as a **horizontal coil detector** and build a companion "Angular Compression Indicator" (ACI) for triangle/wedge detection. A checklist-based trading app can query both and surface the appropriate setup type. CZH and ACI are complementary, not competitive.

### Repainting

The indicator is designed to be non-repainting. The `if (index == Bars.Count - 1) return;` guard ensures all calculations run on confirmed closed bars. Zone boundaries drift as the window slides (expected behavior), but a bar's score classification does not change retroactively once that bar is closed.

---

## Appendix A — Default Parameter Summary Table

| Parameter | Default | Notes |
|-----------|---------|-------|
| LookbackBars | 20 | Core window size |
| ShortAtrPeriod | 7 | Criterion 1 short volatility |
| LongAtrPeriod | 50 | Criterion 1 baseline & ATR reference |
| AtrRatioThreshold | 0.65 | Criterion 1 |
| RangeRatioThreshold | 2.0 | Criterion 2 |
| MinPairwiseOverlap | 0.50 | Criterion 3 |
| OverlapZoneThreshold | 0.70 | Criterion 3 |
| ChoppinessThreshold | 0.618 | Criterion 4 |
| BalanceRatioThreshold | 0.60 | Criterion 4 |
| ClusterRatioThreshold | 0.30 | Criterion 5 |
| MinScore | 3 | Minimum score to draw any zone |
| BreakThresholdATR | 0.50 | Zone expiry on close breakout |
| ExpiryBars | 3 | Bars below MinScore before expiry |
| HistoricalZonesMax | 5 | Count of faded historical zones |
| BoundaryUpperPct | 80 | Zone upper boundary percentile |
| BoundaryLowerPct | 20 | Zone lower boundary percentile |
| MinZoneWidthATR | 0.30 | Floor on zone height |
| OverlapCoveragePct | 0.60 | Coverage fraction for overlap core |

---

## Appendix B — Quick Reference: Criterion Pass/Fail Summary

```
Criterion 1 (ATR Ratio):
    ShortATR / LongATR  <=  0.65                         [PASS]

Criterion 2 (Range Tightness):
    (WindowHigh - WindowLow) / LongATR  <=  2.00         [PASS]

Criterion 3 (Bar Overlap):
    Fraction of bars with pairwise overlap >= 50%  >=  0.70  [PASS]

Criterion 4 (Directional Balance):
    WeightedBalanceRatio  >=  0.60                       [PASS]  ← body-weighted, not binary count
    AND ChoppinessIndex   >=  0.618                      [PASS]
    (both required)

Criterion 5 (Close Clustering):
    StdDev(Close) / LongATR  <=  0.30                   [PASS]

Score = number of passed criteria (0–5)
Draw zone if Score >= MinScore (default 3)
```

---

*End of Specification — Consolidation Zone Highlighter (CZH) v1.0*
