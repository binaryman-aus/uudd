# Donchian Squeeze Box: Technical Architecture & Trading Logic

## 1. System Overview
The **Donchian Squeeze Box** is a custom volatility indicator designed for cTrader. It identifies high-probability price action consolidation zones (coils) and visualizes them as dynamic bounding boxes. 

Unlike traditional TTM Squeeze indicators that rely on lagging moving averages (Bollinger Bands vs. Keltner Channels), this system uses absolute price boundaries (Donchian Channels). This architecture provides a "zero-lag" signal, ensuring that breakout triggers are synchronized perfectly with live price action.

---

## 2. Mathematical Core: The Zero-Lag Squeeze

The primary engine measures the absolute compression of the market relative to its baseline volatility.

### Step 2.1: Baseline Volatility (ATR)
The system calculates a Simple Average True Range (ATR) to establish the current "normal" size of a candlestick.
$$ATR_N = \frac{1}{N} \sum_{i=1}^{N} TR_i$$
*(Where $N$ is the Lookback Period, and $TR$ is the True Range).*

### Step 2.2: Donchian Channel Width
Instead of standard deviations, the system queries the absolute highest high and lowest low over the lookback period to determine the exact vertical footprint of the market.
$$W_{Donchian} = \max_{i=0}^{N}(High_i) - \min_{i=0}^{N}(Low_i)$$

### Step 2.3: The Squeeze Trigger
A squeeze is mathematically validated when the entire $N$-period Donchian Width compresses to a user-defined multiple of the baseline ATR.
$$Condition_{Squeeze} = W_{Donchian} \le (ATR \times Multiplier_{Squeeze})$$

---

## 3. Price Action Filtering (The Edge)

To prevent the algorithm from boxing "dead markets" or absorbing breakout candles, two strict price action filters are applied before a box is drawn.

### 3.1: Close Price Clustering
Volatility can mathematically shrink even if price action is choppy and erratic. To ensure the market is genuinely "coiling," the system checks the closing prices of the last $C$ bars (Cluster Lookback).
$$Range_{Close} = \max(Close_{1 \to C}) - \min(Close_{1 \to C})$$
$$Condition_{Cluster} = Range_{Close} \le (ATR \times Tightness_{Cluster})$$
*Note: This loop strictly evaluates historical bars (indexes 1 to C) to prevent live tick flickering from corrupting the cluster.*

### 3.2: The Momentum Circuit Breaker
If an individual bar's total range exceeds the baseline ATR, it is classified as an ignition/momentum bar. 
$$Condition_{ValidBar} = (High_0 - Low_0) \le ATR$$
If $Condition_{ValidBar}$ evaluates to false, the squeeze state is instantly terminated, locking the box to the previous candle and leaving the breakout candle clean and highly visible.

---

## 4. State Management & Playback Stability

A critical engineering hurdle in cTrader is maintaining stable state logic across standard historical data, live Market Playback, and real-time tick data. Global C# variables (e.g., `bool isSqueezing`) suffer from mid-bar state corruption.

To resolve this, the indicator relies entirely on **cTrader IndicatorDataSeries**:
* `_isSqueezingSeries`
* `_startIndexSeries`
* `_boxHighSeries`
* `_boxLowSeries`

### The "Ghost Box" Cleanup Routine
During live Market Playback, tick volatility can briefly trigger the mathematical squeeze conditions mid-bar, prompting the `Chart.DrawRectangle` command. If subsequent ticks invalidate the setup before the bar closes, the standard logic leaves a permanent "ghost box" on the chart. 

The system mitigates this via an active cleanup sequence:
```csharp
if (_isSqueezingSeries[index] == 1)
{
    if (!wasSqueezing) {
        // False positive on a new squeeze: Delete the object entirely.
        Chart.RemoveObject("DonchianBox_" + activeStartIdx);
    } else {
        // False positive on an expanding squeeze: Revert dimensions to index - 1.
        Chart.DrawRectangle(boxName, activeStartIdx, _boxHighSeries[index - 1], index - 1, ...);
    }
}