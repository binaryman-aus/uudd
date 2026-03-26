# Telegram Notification Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace price range data in the Telegram S/R alert with a compact 10-bar detection history string and directional arrow emoji per symbol.

**Architecture:** Extract two pure helper functions (`build_history_string`, `format_telegram_message`) into `dashboard.py`, test them in isolation, then wire them into `run_pipeline` and `send_consolidated_telegram`. No other files change.

**Tech Stack:** Python 3.10, pytest, pandas (already in use)

---

## File Map

| File | Change |
|------|--------|
| `dashboard.py` | Add `build_history_string` and `format_telegram_message`; update `run_pipeline` and `send_consolidated_telegram` |
| `tests/test_dashboard.py` | New — unit tests for the two helpers |

---

### Task 1: Test and implement `build_history_string`

**Files:**
- Create: `tests/test_dashboard.py`
- Modify: `dashboard.py` (add function after line 15, before `generate_dashboard`)

- [ ] **Step 1: Create test file with failing tests**

Create `tests/test_dashboard.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dashboard import build_history_string


def test_full_10_bars_mixed():
    """10 bars, mix of S, R, and no detection."""
    bar_timestamps = list(range(100, 110))  # [100..109]
    symbol_results = [
        {'detected_at': 100, 'result': 'support'},
        {'detected_at': 104, 'result': 'resistance'},
        {'detected_at': 109, 'result': 'support'},
    ]
    result = build_history_string(bar_timestamps, symbol_results)
    # 100=S, 101=~, 102=~, 103=~, 104=R, 105=~, 106=~, 107=~, 108=~, 109=S
    assert result == 'S~~~R~~~~S'


def test_fewer_than_10_bars_pads_left():
    """3 bars — left-pad with ~ to reach 10 chars."""
    bar_timestamps = [100, 101, 102]
    symbol_results = [
        {'detected_at': 101, 'result': 'resistance'},
    ]
    result = build_history_string(bar_timestamps, symbol_results)
    # chars for bars 100,101,102: ~, R, ~  -> '~R~' -> rjust(10,'~')
    assert result == '~~~~~~~~R~'
    assert len(result) == 10


def test_all_nil():
    """No detections at all."""
    bar_timestamps = list(range(100, 110))
    result = build_history_string(bar_timestamps, [])
    assert result == '~~~~~~~~~~'


def test_more_than_10_bars_uses_last_10():
    """Only the last 10 bars are shown."""
    bar_timestamps = list(range(100, 115))  # 15 bars
    symbol_results = [
        {'detected_at': 100, 'result': 'support'},   # outside last 10
        {'detected_at': 110, 'result': 'resistance'}, # inside last 10 (bar index 10)
    ]
    result = build_history_string(bar_timestamps, symbol_results)
    # last 10 = [105..114]
    # 105-109=~, 110=R, 111-114=~
    assert result == '~~~~~R~~~~'
    assert len(result) == 10
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd C:\Users\avpei\UUDD
python -m pytest tests/test_dashboard.py -v
```

Expected: `ImportError` or `AttributeError: module 'dashboard' has no attribute 'build_history_string'`

- [ ] **Step 3: Implement `build_history_string` in `dashboard.py`**

Add this function after line 15 (after the `TELEGRAM_CHAT_ID` line), before `generate_dashboard`:

```python
def build_history_string(bar_timestamps, symbol_results, last_n=10):
    """
    Build an N-char S/R history string for the last N bars.

    bar_timestamps: list of unix int timestamps for all bars, sorted ascending
    symbol_results: list of dicts with 'detected_at' (unix int) and 'result' ('support'/'resistance')
    Returns a string of length last_n: 'S', 'R', or '~' per bar, left-padded with '~' if fewer bars.
    """
    detection_map = {r['detected_at']: r['result'] for r in symbol_results}
    recent = bar_timestamps[-last_n:]
    chars = []
    for t in recent:
        if t in detection_map:
            chars.append('S' if detection_map[t] == 'support' else 'R')
        else:
            chars.append('~')
    return ''.join(chars).rjust(last_n, '~')
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_dashboard.py -v
```

Expected: 4 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/test_dashboard.py dashboard.py
git commit -m "feat: add build_history_string for S/R history bar"
```

---

### Task 2: Test and implement `format_telegram_message`

**Files:**
- Modify: `tests/test_dashboard.py` (add tests)
- Modify: `dashboard.py` (add function after `build_history_string`)

- [ ] **Step 1: Add failing tests to `tests/test_dashboard.py`**

Append to `tests/test_dashboard.py`:

```python
from dashboard import format_telegram_message


def test_format_telegram_message_structure():
    """Message contains header, rows, and footer link."""
    detections = [
        {'symbol': 'US500',  'result': 'resistance', 'history': '~~SSS~~RRR'},
        {'symbol': 'XAUUSD', 'result': 'support',    'history': '~SSSSSSS~~'},
    ]
    msg = format_telegram_message(detections)
    assert msg.startswith('🚨 S/R ALERT 🚨')
    assert '`US500  ~~SSS~~RRR` ⬇️' in msg
    assert '`XAUUSD ~SSSSSSS~~` ⬆️' in msg
    assert '[View Dashboard](https://binaryman-aus.github.io/uudd/)' in msg


def test_format_telegram_message_support_arrow():
    """Support → ⬆️."""
    detections = [{'symbol': 'EURUSD', 'result': 'support', 'history': 'SSSSSSSSSS'}]
    msg = format_telegram_message(detections)
    assert '`EURUSD SSSSSSSSSS` ⬆️' in msg


def test_format_telegram_message_resistance_arrow():
    """Resistance → ⬇️."""
    detections = [{'symbol': 'BTCUSD', 'result': 'resistance', 'history': 'RRRRRRRRRR'}]
    msg = format_telegram_message(detections)
    assert '`BTCUSD RRRRRRRRRR` ⬇️' in msg


def test_format_telegram_message_symbol_padding():
    """5-char symbols get padded to 6 with a trailing space."""
    detections = [{'symbol': 'GER40', 'result': 'support', 'history': 'SSSSSSSSSS'}]
    msg = format_telegram_message(detections)
    # 'GER40' padded to 6 = 'GER40 ' (one trailing space)
    assert '`GER40  SSSSSSSSSS` ⬆️' in msg
```

Note on padding: `f"{symbol:<6}"` for a 5-char symbol like `GER40` produces `'GER40 '` (one space). Combined with the separator space in the format string, the row becomes `` `GER40  SSSSSSSSSS` `` (two spaces between symbol and history).

- [ ] **Step 2: Run tests to confirm the new tests fail**

```bash
python -m pytest tests/test_dashboard.py -v
```

Expected: 4 existing tests PASS, 4 new tests FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: Implement `format_telegram_message` in `dashboard.py`**

Add this function immediately after `build_history_string`:

```python
def format_telegram_message(detections):
    """
    Build the consolidated Telegram alert message body.

    detections: list of dicts with keys 'symbol' (str), 'result' ('support'/'resistance'),
                'history' (10-char string from build_history_string)
    Returns a Markdown-formatted string ready to send.
    """
    message = "🚨 S/R ALERT 🚨\n\n"
    for det in detections:
        emoji = "⬆️" if det['result'] == 'support' else "⬇️"
        message += f"`{det['symbol']:<6} {det['history']}` {emoji}\n"
    message += "\n[View Dashboard](https://binaryman-aus.github.io/uudd/)"
    return message
```

- [ ] **Step 4: Run all tests to confirm all 8 pass**

```bash
python -m pytest tests/test_dashboard.py -v
```

Expected: 8 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/test_dashboard.py dashboard.py
git commit -m "feat: add format_telegram_message with history bar and arrow emoji"
```

---

### Task 3: Wire helpers into `run_pipeline` and `send_consolidated_telegram`

**Files:**
- Modify: `dashboard.py` — `run_pipeline` (lines ~418–427) and `send_consolidated_telegram` (lines ~331–362)

- [ ] **Step 1: Update `run_pipeline` to build history and strip price range**

In `run_pipeline`, find this block (~line 418):

```python
            # Check for latest bar detection (Active S/R)
            if symbol_results:
                latest_detection = symbol_results[-1]
                last_bar_time = int(df['time'].iloc[-1].timestamp())
                if latest_detection['detected_at'] == last_bar_time:
                    active_detections.append({
                        "symbol": symbol,
                        "result": latest_detection['result'],
                        "range_low": latest_detection['price_range']['low'],
                        "range_high": latest_detection['price_range']['high']
                    })
```

Replace with:

```python
            # Check for latest bar detection (Active S/R)
            if symbol_results:
                latest_detection = symbol_results[-1]
                last_bar_time = int(df['time'].iloc[-1].timestamp())
                if latest_detection['detected_at'] == last_bar_time:
                    bar_timestamps = [int(t.timestamp()) for t in df['time']]
                    history = build_history_string(bar_timestamps, symbol_results)
                    active_detections.append({
                        "symbol": symbol,
                        "result": latest_detection['result'],
                        "history": history
                    })
```

- [ ] **Step 2: Update `send_consolidated_telegram` to use `format_telegram_message`**

Find the current `send_consolidated_telegram` function (~line 331):

```python
def send_consolidated_telegram(detections):
    """
    Sends a consolidated Telegram notification for all active S/R levels.
    """
    if not detections:
        print("No active S/R detections to notify.")
        return

    message = "🚨 *S/R DASHBOARD ALERT* 🚨\n\n"
    for det in detections:
        message += (
            f"📍 *{det['symbol']}*: {det['result'].upper()}\n"
            f"   Range: `{det['range_low']:.2f} - {det['range_high']:.2f}`\n\n"
        )

    message += "[View Live Dashboard](https://binaryman-aus.github.io/uudd/)"

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Consolidated Telegram notification sent.")
        else:
            print(f"Failed to send consolidated Telegram: {response.text}")
    except Exception as e:
        print(f"Error sending consolidated Telegram: {e}")
```

Replace with:

```python
def send_consolidated_telegram(detections):
    """
    Sends a consolidated Telegram notification for all active S/R detections.
    Each symbol shows a 10-bar history string and a directional arrow emoji.
    """
    if not detections:
        print("No active S/R detections to notify.")
        return

    message = format_telegram_message(detections)

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Consolidated Telegram notification sent.")
        else:
            print(f"Failed to send consolidated Telegram: {response.text}")
    except Exception as e:
        print(f"Error sending consolidated Telegram: {e}")
```

- [ ] **Step 3: Run all tests to confirm nothing broke**

```bash
python -m pytest tests/test_dashboard.py -v
```

Expected: 8 tests PASSED

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: wire history bar and arrow emoji into Telegram notification"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** History string ✅, arrow emoji ✅, price range removed ✅, only active-detection symbols ✅, monospace alignment ✅, edge cases (< 10 bars, 6-char padding, no detections) ✅
- [x] **No placeholders:** All steps have complete code
- [x] **Type consistency:** `bar_timestamps` is `list[int]` throughout; `symbol_results` shape matches what `run_pipeline` already builds; `active_detections` dict keys match between Task 3 Step 1 and `format_telegram_message` parameter
- [x] **`format_telegram_message` tests account for 5-char vs 6-char symbol padding** — `GER40` → `GER40 ` (1 space pad) + separator space = 2 spaces before history
