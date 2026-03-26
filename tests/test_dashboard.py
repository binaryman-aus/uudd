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
