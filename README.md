# OHLCV Support & Resistance Detection System

A Python-based system for detecting, backtesting, and visualizing Support and Resistance (S/R) levels using OHLCV data. The algorithm is based on Kevin Yu's theory of "跌不下去 / 上不去" (Can't go down / Can't go up).

## 🚀 Quick Start

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Setup Environment**:
   Create a `.env` file with your Supabase and Telegram credentials:
   ```env
   SUPABASE_URL=your_url
   SUPABASE_KEY=your_key
   TELEGRAM_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

3. **Run Multi-Symbol Dashboard**:
   ```bash
   python dashboard.py
   ```
   Generates a 3x3 grid of the latest S/R levels for 9 symbols and sends a consolidated Telegram alert.

4. **Run Single Backtest**:
   ```bash
   python backtest.py --symbol USOIL --timeframe H1
   ```
   Open `backtest_report.html` to view the interactive TradingView chart.

---

## 🧠 Detection Algorithm Details

The detection logic in `sr_detect.py` uses a multi-step approach to identify high-conviction price consolidation zones.

### 1. Volatility Baseline (ATR)
The system calculates the **Average True Range (ATR)** over a configurable period (default: 200 bars). This ATR serves as the baseline for measuring "closeness" to a price level, ensuring the detection is relative to current market volatility.

### 2. Narrow Price Range (The Zone)
For any potential level (a bar's High or Low), a "Narrow Range" is defined:
- **Range Height** = `ATR * threshold_factor` (e.g., $0.5 \times ATR$).
- The zone is defined as `[Price - RangeHeight, Price + RangeHeight]`.

### 3. Core Constraints (The Rules)

To be confirmed as a valid S/R zone, a window of $N$ bars (e.g., 6 bars) must satisfy **all** of the following:

#### A. Cluster Density (Minimum Bars)
- At least **M** bars (e.g., 4 bars) within the $N$-bar window must have their High (for resistance) or Low (for support) fall within the narrow range.

#### B. Recency Requirement
- The **latest bar** in the window must be one of the bars touching the range. This ensures the level is currently active.

#### C. Validation (Close Price Rule)
- For **Resistance**: No bar in the window is allowed to **close above** the `range_high`.
- For **Support**: No bar in the window is allowed to **close below** the `range_low`.
- *Note: Price is allowed to spike through (wicks), but closing beyond the boundary invalidates the consolidation.*

#### D. Price Rejection (Wick Rule)
To ensure the level represents a rejection area, at least **50%** of the bars touching the range must have a meaningful rejection wick on the correct side.

Wick size is measured as a **ratio of the bar's total range** (`High − Low`):

```
# Resistance — upper wick ratio per bar:
upper_wick  = High − max(Open, Close)
wick_ratio  = upper_wick / (High − Low)

# Support — lower wick ratio per bar:
lower_wick  = min(Open, Close) − Low
wick_ratio  = lower_wick / (High − Low)
```

A bar qualifies if `wick_ratio ≥ --wick` (default `0.1`, i.e. the rejection wick must be at least **10% of the bar's total High-to-Low range**). At least `--min_wick_bars` (default `2`) of the in-range bars must qualify for the zone to pass this rule.

#### E. Drift Constraint (The "Miss" Rule)
- To ensure a tight cluster, no more than **one bar** in the window is allowed to "drift" away from the level:
    - **Resistance**: Max 1 bar can have a High strictly **below** the range.
    - **Support**: Max 1 bar can have a Low strictly **above** the range.

### 4. Zone Boundaries: `z_low` and `z_high`

Every detected zone is exported as a `price_range` object with two boundaries:

```
z_low  = price_range.low  = anchor_price − y_range
z_high = price_range.high = anchor_price + y_range
```

Where:
- **`anchor_price`** — the bar's **High** (resistance) or **Low** (support) that produces the densest cluster. The algorithm tries every bar in the N-bar window as a candidate center: for each candidate, it counts how many other bars have their High (or Low) within `[candidate − y_range, candidate + y_range]`. The candidate with the highest count becomes the anchor, and its High/Low is `anchor_price`. All other constraints (recency, close rule, wick rule, drift rule) must also pass for that candidate to be accepted.
- **`y_range`** — `ATR × threshold_factor` (half-width of the zone, in price units).

So the zone is always **symmetric** around its anchor price, with a total width of `2 × ATR × threshold_factor`.

**Example** — support zone on XAUUSD H1, ATR = 4.0, threshold = 0.5:
```
anchor_price = 2,310.00 (bar low)
y_range      = 4.0 × 0.5 = 2.0

z_low  = 2,310.00 − 2.0 = 2,308.00
z_high = 2,310.00 + 2.0 = 2,312.00
```

These boundaries are used directly in accuracy evaluation:
- **Support** — a long entry limit order sits at `z_high`. A bar that straddles `z_high` (`high ≥ z_high` and `low ≤ z_high`) fills the order. A bar whose `low < z_low` on the same fill bar counts as an immediate break.
- **Resistance** — a short entry limit order sits at `z_low`. A bar that straddles `z_low` (`low ≤ z_low` and `high ≥ z_low`) fills the order. A bar whose `high > z_high` on the same fill bar counts as an immediate break.

### 5. Tiebreaker When Both S and R Are Detected
It is possible for the same bar to simultaneously satisfy both support and resistance conditions (e.g., a doji-like bar with lows and highs both hugging the same tight zone). In this case the winner is decided by a three-tier priority:

1. **Higher touch count wins** — whichever type has more bars touching the zone is reported.
2. **Larger average wick ratio wins** — if counts are equal, the type with the higher average wick-to-bar-range ratio across its in-zone bars wins. A larger average wick means the level is showing stronger price rejection.
3. **Resistance wins** — if both count and average wick ratio are identical, resistance is the final fallback.

Only a single result (`support` or `resistance`) is returned per bar.

---

## 🛠️ Toolset

### `dashboard.py` (The Pipeline)
Automatically processes 9 major symbols (US500, GER40, JP225, USOIL, XAUUSD, BTCUSD, AUDUSD, EURUSD, USDJPY) and builds a **3x3 Dashboard**.
- **Consolidated Alerts**: Sends a single Telegram message for all symbols where the **latest bar has an active S/R detection**. Each symbol line shows a 10-bar history string and a directional arrow (⬆️ support / ⬇️ resistance).
- **Single-Screen View**: Optimized HTML layout ([`dashboard.html`](dashboard.html)) that fits 9 interactive charts without scrolling.

#### Telegram Alert Format

```
🚨 S/R ALERT 🚨

`US500  ~~SSS~~RRR` ⬇️
`GER40  ~SSSSSSS~~` ⬆️
`XAUUSD RRRR~~SSSS` ⬆️

[View Dashboard](https://binaryman-aus.github.io/uudd/)
```

Each row is rendered in monospace. The 10-character history string reads left (oldest) → right (most recent):
- `S` — support detected on that bar
- `R` — resistance detected on that bar
- `~` — no detection on that bar

Symbols with fewer than 10 bars of history are left-padded with `~`. Only symbols with an active detection on the very latest bar are included.

### `sr_detect.py` (The Engine)
Identifies S/R levels. Includes a powerful **Debug Mode**:
```bash
python sr_detect.py --debug_time "2026-03-24T04:00:00Z" [other params]
```
Returns a detailed JSON object showing exactly why a specific timestamp passed or failed each rule.

### `backtest.py` (The Simulator)
Runs the detection engine across historical data and generates a responsive HTML report with TradingView Lightweight Charts.

### `fetch_ohlcv.py` (The Data Link)
Connects to Supabase to download the latest market data.

---

## 🤖 Automation (GitHub Actions)

The system is configured to run automatically using GitHub Actions:
- **Schedule**: Every hour at the 1-minute mark (`1 * * * *`).
- **Deployment**: Automatically pushes the updated dashboard to **GitHub Pages**.
- **Secrets**: Securely uses `SUPABASE_URL`, `SUPABASE_KEY`, `TELEGRAM_TOKEN`, and `TELEGRAM_CHAT_ID` stored in repository settings.

---

## ⚙️ Configurable Parameters

The system uses a `config.json` file for persistent settings. Any parameter can also be overridden via command-line arguments.

| Parameter | CLI Flag | config.json Key | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| **N-Bars** | `--nbars` | `"nbars"` | 6 | The lookback window for detection. |
| **Min Bars** | `--min_bars` | `"min_bars"` | 4 | Min bars required to touch the zone. |
| **ATR Period** | `--atr_period` | `"atr_period"` | 200 | Window for volatility calculation. |
| **Threshold** | `--threshold` | `"threshold"` | 0.5 | Multiplier for ATR to set zone width. |
| **Min Wick** | `--wick` | `"wick"` | 0.1 | Min rejection wick size (as % of bar). |
| **Min Wick Bars** | `--min_wick_bars` | `"min_wick_bars"` | 2 | Min number of in-range bars that must satisfy the wick requirement. |
| **Window Size**| `--window` | `"window"` | 200 | Total bars shown in the sliding window. |

### Current `config.json`:
```json
{
    "nbars": 6,
    "min_bars": 4,
    "atr_period": 200,
    "threshold": 0.5,
    "wick": 0.1,
    "min_wick_bars": 2,
    "window": 200
}
```
