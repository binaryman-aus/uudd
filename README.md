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

### `dashboard.py` (The Pipeline)
Automatically processes 9 major symbols (US500, GER40, JP225, USOIL, XAUUSD, BTCUSD, AUDUSD, EURUSD, USDJPY) and builds a **3x3 Dashboard**.
- **Consolidated Alerts**: Sends a single Telegram message summarizing all active S/R levels.
- **Single-Screen View**: Optimized HTML layout ([`dashboard.html`](dashboard.html)) that fits 9 interactive charts without scrolling.

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
| **Window Size**| `--window` | `"window"` | 200 | Total bars shown in the sliding window. |

### Current `config.json`:
```json
{
    "nbars": 6,
    "min_bars": 4,
    "atr_period": 200,
    "threshold": 0.5,
    "wick": 0.1,
    "window": 200
}
```
