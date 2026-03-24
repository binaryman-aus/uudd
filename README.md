# OHLCV Support & Resistance Detection System

A Python-based system for detecting, backtesting, and visualizing Support and Resistance (S/R) levels using OHLCV data. The algorithm is based on Kevin Yu's theory of "跌不下去 / 上不去" (Can't go down / Can't go up).

## 🚀 Quick Start

1. **Install Dependencies**:
   ```bash
   pip install supabase python-dotenv pandas jinja2
   ```

2. **Fetch Data**:
   ```bash
   python fetch_ohlcv.py --symbol USOIL --timeframe H1
   ```

3. **Run Backtest**:
   ```bash
   python backtest.py --nbars 7 --confirm 0.7 --atr_period 21 --threshold 0.5 --wick 0.1
   ```
   Open `backtest_report.html` to view the interactive TradingView chart.

---

## 🧠 Detection Algorithm Details

The detection logic in `sr_detect.py` uses a multi-step approach to identify high-conviction price consolidation zones.

### 1. Volatility Baseline (ATR)
The system calculates the **Average True Range (ATR)** over a configurable period (default: 21 bars). This ATR serves as the baseline for measuring "closeness" to a price level, ensuring the detection is relative to current market volatility.

### 2. Narrow Price Range (The Zone)
For any potential level (a bar's High or Low), a "Narrow Range" is defined:
- **Range Height** = `ATR * threshold_factor` (e.g., $0.5 \times ATR$).
- The zone is defined as `[Price - RangeHeight, Price + RangeHeight]`.

### 3. Core Constraints (The Rules)

To be confirmed as a valid S/R zone, a window of $N$ bars (e.g., 7 bars) must satisfy **all** of the following:

#### A. Cluster Density (Confirmation %)
- At least **X%** (e.g., 70%) of the $N$ bars must have their High (for resistance) or Low (for support) fall within the narrow range.

#### B. Recency Requirement
- The **latest bar** in the window must be one of the bars touching the range. This ensures the level is currently active.

#### C. Validation (Close Price Rule)
- For **Resistance**: No bar in the window is allowed to **close above** the `range_high`.
- For **Support**: No bar in the window is allowed to **close below** the `range_low`.
- *Note: Price is allowed to spike through (wicks), but closing beyond the boundary invalidates the consolidation.*

#### D. Price Rejection (Wick Rule)
- To ensure the level represents a "rejection" area, at least **50%** of the bars touching the range must have significant rejection wicks:
    - **Resistance**: Long upper wicks.
    - **Support**: Long lower wicks.
- The "length" is defined by the `--wick` parameter as a percentage of the bar's total range.

#### E. Drift Constraint (The "Miss" Rule)
- To ensure a tight cluster, no more than **one bar** in the window is allowed to "drift" away from the level:
    - **Resistance**: Max 1 bar can have a High strictly **below** the range.
    - **Support**: Max 1 bar can have a Low strictly **above** the range.

---

## 🛠️ Toolset

### `sr_detect.py` (The Engine)
Identifies S/R levels. Includes a powerful **Debug Mode**:
```bash
python sr_detect.py --debug_time "2026-03-24T04:00:00+00:00" [other params]
```
Returns a detailed JSON object showing exactly why a specific timestamp passed or failed each rule (Wick %, Close validity, Miss count, etc.).

### `backtest.py` (The Simulator)
Uses a sliding window to run the detection engine across your entire historical dataset and generates a responsive HTML report.
- **Interactive Visuals**: Uses TradingView Lightweight Charts.
- **Rectangle Mapping**: S/R zones are drawn as solid boxes from the start bar to the end bar.
- **Navigation**: Click any row in the table to zoom the chart to that detection.

### `fetch_ohlcv.py` (The Data Link)
Connects to Supabase using your `.env` configuration to download the latest market data.
- Supports custom symbols (`--symbol`) and limits (`--limit`).

---

## ⚙️ Configurable Parameters

| Parameter | CLI Flag | Default | Description |
| :--- | :--- | :--- | :--- |
| **N-Bars** | `--nbars` | 7 | The lookback window for detection. |
| **Confirm %** | `--confirm` | 0.7 | % of bars required to touch the zone. |
| **ATR Period** | `--atr_period` | 21 | Window for volatility calculation. |
| **Threshold** | `--threshold` | 0.5 | Multiplier for ATR to set zone width. |
| **Min Wick** | `--wick` | 0.1 | Min rejection wick size (as % of bar). |
| **Window Size**| `--window` | 200 | Total bars shown in the sliding window. |
