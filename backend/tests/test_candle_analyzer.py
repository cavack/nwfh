import time

from waterfallhunter.core.candle_analyzer import MultiTimeframeAnalyzer


def closed_failed_pullback_candles():
    start = (
        int(time.time() * 1000)
        - 23 * 300_000
    )

    rows = [
        [
            start + index * 300_000,
            10.0,
            10.2,
            9.8,
            10.0,
            10.0,
        ]
        for index in range(20)
    ]

    rows.extend(
        [
            [
                start + 20 * 300_000,
                10.0,
                10.0,
                9.2,
                9.3,
                12.0,
            ],
            [
                start + 21 * 300_000,
                9.9,
                10.0,
                9.5,
                9.6,
                14.0,
            ],
            [
                start + 22 * 300_000,
                9.7,
                9.9,
                9.0,
                9.2,
                30.0,
            ],
        ]
    )

    return rows


def test_failed_pullback_has_regime_setup_and_trigger_evidence():
    result = MultiTimeframeAnalyzer()._evaluate(
        closed_failed_pullback_candles()
    )

    assert result["regime_bearish"] is True
    assert result["setup"] == "FAILED_PULLBACK"
    assert result["trigger_ready"] is True
    assert result["bearish_close"] is True


def test_gapped_candles_are_rejected():
    analyzer = MultiTimeframeAnalyzer()

    now = int(time.time() * 1000)
    start = now - 25 * 300_000

    rows = [
        [
            start + index * 300_000,
            10.0,
            10.2,
            9.8,
            10.0,
            1.0,
        ]
        for index in range(20)
    ]

    rows[10][0] += 300_000

    assert analyzer._closed_candles(
        rows,
        "5m",
    ) is None


def test_stale_or_zero_price_candles_are_rejected():
    analyzer = MultiTimeframeAnalyzer()

    gap = analyzer.timeframe_ms["5m"]

    start = (
        int(time.time() * 1000)
        - 31 * 86_400_000
    )

    stale = [
        [
            start + index * gap,
            10.0,
            10.2,
            9.8,
            10.0,
            1.0,
        ]
        for index in range(20)
    ]

    assert analyzer._closed_candles(
        stale,
        "5m",
    ) is None

    fresh = closed_failed_pullback_candles()
    fresh[0][1] = 0.0

    assert analyzer._closed_candles(
        fresh,
        "5m",
    ) is None


def test_evaluate_exposes_atr_geometry_and_rolling_returns():
    result = MultiTimeframeAnalyzer()._evaluate(
        closed_failed_pullback_candles()
    )

    expected_fields = (
        "atr_14",
        "atr_pct",
        "distance_to_support_pct",
        "distance_to_support_atr",
        "distance_from_recent_high_pct",
        "extension_from_support_atr",
        "return_3bars_pct",
        "return_6bars_pct",
        "return_12bars_pct",
    )

    for key in expected_fields:
        assert key in result

    assert result["atr_14"] is not None
    assert result["atr_14"] > 0

    assert result["atr_pct"] is not None

    assert (
        result["distance_to_support_atr"]
        is not None
    )


def test_atr_uses_true_range_not_only_high_low():
    analyzer = MultiTimeframeAnalyzer()

    rows = []

    for index in range(20):
        opening = (
            10.0
            if index == 0
            else rows[-1][4]
        )

        close = opening

        rows.append(
            [
                index,
                opening,
                opening + 0.1,
                opening - 0.1,
                close,
                1.0,
            ]
        )

    # Large gap from the previous close.
    # True Range must capture the gap,
    # not only current high-low.
    rows[-1] = [
        19,
        12.0,
        12.1,
        11.9,
        12.0,
        1.0,
    ]

    atr = analyzer._atr(
        rows,
        period=14,
    )

    assert atr is not None
    assert atr > 0.2


def test_rolling_return_returns_none_when_history_is_too_short():
    rows = [
        [
            0,
            1.0,
            1.0,
            1.0,
            1.0,
            1.0,
        ]
    ]

    assert (
        MultiTimeframeAnalyzer._return_pct(
            rows,
            3,
        )
        is None
    )


# ---------------------------------------------------------------------------
# Change D: evaluate_bearish_structure / Change E: evaluate_absorption
# ---------------------------------------------------------------------------

from waterfallhunter.core.candle_analyzer import (
    evaluate_absorption,
    evaluate_bearish_structure,
)

_GAP_15M = 900_000

# Pre-pivot candles (indices 0-15): a pivot low at index 5 (low=95) and a
# higher pivot low at index 15 (low=97), which becomes the active
# support / last_higher_low level once confirmed by the two candles either
# side of it (indices 13-14 and 16-17).
_DEFAULT_PRE_PIVOT_TAIL = [
    (96.0, 97.0, 95.0, 96.0, 1.0),   # index 5: pivot low at 95
    (98.0, 99.0, 97.0, 98.0, 1.0),   # index 15: higher pivot low at 97
]

_ABOVE_SUPPORT_ROW = (100.0, 101.0, 99.0, 100.0, 1.0)


def _structure_candles(second_half_rows, start=None):
    """Builds 16 pre-pivot candles (indices 0-15, pivot low 95 @ idx5,
    higher pivot low 97 @ idx15) followed by the caller-supplied
    `second_half_rows` (indices 16 onward, each an (open, high, low,
    close, volume) tuple), all as CLOSED 15m candles on a strict 900_000ms
    grid.
    """
    if start is None:
        start = int(time.time() * 1000) - (16 + len(second_half_rows)) * _GAP_15M

    rows = []
    for index in range(16):
        if index == 5:
            body = _DEFAULT_PRE_PIVOT_TAIL[0]
        elif index == 15:
            body = _DEFAULT_PRE_PIVOT_TAIL[1]
        else:
            body = _ABOVE_SUPPORT_ROW
        rows.append([start + index * _GAP_15M, *body])

    for offset, body in enumerate(second_half_rows):
        index = 16 + offset
        rows.append([start + index * _GAP_15M, *body])

    return rows


def test_bullish_structure_is_not_flagged_bearish_regardless_of_below_vwap():
    # No candle after the higher-low (idx 15) closes below the 97 support,
    # so this must be bullish/no-break structure. below_vwap is a separate
    # location feature this function does not consume at all -- a caller
    # setting below_vwap=True elsewhere must not be able to flip this.
    candles = _structure_candles([_ABOVE_SUPPORT_ROW] * 14)
    result = evaluate_bearish_structure(candles)

    assert result["available"] is True
    assert result["bearish_15m_structure"] is False
    assert result["support_break_close"] is False


def test_wick_below_support_without_close_does_not_confirm_break():
    second_half = [_ABOVE_SUPPORT_ROW] * 14
    second_half[4] = (98.0, 98.5, 95.0, 98.0, 2.0)  # wicks to 95, closes at 98 (above 97 support)
    candles = _structure_candles(second_half)

    result = evaluate_bearish_structure(candles)

    assert result["available"] is True
    assert result["support_break_close"] is False
    assert result["bearish_15m_structure"] is False
    assert "WICK_ONLY_NO_CLOSE_BELOW_SUPPORT" in result["reason_codes"]


def test_completed_candle_closing_below_support_confirms_break():
    second_half = [_ABOVE_SUPPORT_ROW] * 14
    second_half[4] = (98.0, 98.5, 95.0, 96.0, 2.0)  # closes at 96, below the 97 support
    candles = _structure_candles(second_half)

    result = evaluate_bearish_structure(candles)

    assert result["available"] is True
    assert result["support_break_close"] is True
    assert result["bearish_15m_structure"] is True
    assert result["broken_support"] == 97.0
    assert result["last_higher_low"] == 97.0


def test_unavailable_when_candle_history_is_too_short():
    result = evaluate_bearish_structure(_structure_candles([])[:10])

    assert result["available"] is False
    assert result["bearish_15m_structure"] is False
    assert result["support_break_close"] is False
    assert "INSUFFICIENT_CANDLE_HISTORY" in result["reason_codes"]


def test_unavailable_when_no_candle_data_supplied():
    result = evaluate_bearish_structure([])

    assert result["available"] is False
    assert result["bearish_15m_structure"] is False
    assert result["support_break_close"] is False
    assert "INSUFFICIENT_CANDLE_HISTORY" in result["reason_codes"]


def test_unavailable_when_candles_are_gapped():
    second_half = [_ABOVE_SUPPORT_ROW] * 14
    second_half[4] = (98.0, 98.5, 95.0, 96.0, 2.0)
    candles = _structure_candles(second_half)
    candles[10][0] += _GAP_15M  # introduce a timestamp gap

    result = evaluate_bearish_structure(candles)

    assert result["available"] is False
    assert result["bearish_15m_structure"] is False
    assert result["support_break_close"] is False
    assert "STRUCTURE_DATA_GAPPED" in result["reason_codes"]


def test_retest_rejected_true_when_price_retests_and_closes_back_below():
    # Break at offset 4 (index 20), then every candle afterwards retests
    # (high >= 97 support) but keeps closing back below it -> rejection.
    second_half = [_ABOVE_SUPPORT_ROW] * 14
    second_half[4] = (98.0, 98.5, 95.0, 96.0, 2.0)  # support break close
    for offset in range(5, 14):
        second_half[offset] = (96.5, 97.5, 96.2, 96.8, 1.0)  # retest wick, close stays below 97
    candles = _structure_candles(second_half)

    result = evaluate_bearish_structure(candles)

    assert result["available"] is True
    assert result["support_break_close"] is True
    assert result["retest_rejected"] is True


def test_retest_rejected_none_when_no_retest_data_available_yet():
    # Break at offset 4 (index 20), then price simply never comes back up
    # to test the broken 97 support again -> insufficient data to call it.
    second_half = [_ABOVE_SUPPORT_ROW] * 14
    second_half[4] = (98.0, 98.5, 95.0, 96.0, 2.0)  # support break close
    for offset in range(5, 14):
        second_half[offset] = (95.5, 96.5, 95.0, 96.0, 1.0)  # stays below support, never retests
    candles = _structure_candles(second_half)

    result = evaluate_bearish_structure(candles)

    assert result["available"] is True
    assert result["support_break_close"] is True
    assert result["retest_rejected"] is None


def test_absorption_detected_when_sell_flow_high_without_displacement():
    result = evaluate_absorption(
        sell_flow_usdt=500_000.0,
        buy_flow_usdt=200_000.0,
        taker_buy_sell_ratio=0.7,
        closed_price_return_pct=-0.02,
        support_break_close=False,
    )

    assert result["available"] is True
    assert result["sell_pressure_high"] is True
    assert result["downside_displacement_confirmed"] is False
    assert result["absorption_detected"] is True
    assert "SELL_FLOW_WITHOUT_PRICE_DISPLACEMENT" in result["reason_codes"]


def test_absorption_not_detected_with_confirmed_displacement_and_support_break():
    result = evaluate_absorption(
        sell_flow_usdt=500_000.0,
        buy_flow_usdt=200_000.0,
        taker_buy_sell_ratio=0.7,
        closed_price_return_pct=-1.5,
        support_break_close=True,
    )

    assert result["available"] is True
    assert result["sell_pressure_high"] is True
    assert result["downside_displacement_confirmed"] is True
    assert result["support_broken"] is True
    assert result["absorption_detected"] is False


def test_absorption_unavailable_when_essential_data_missing():
    result = evaluate_absorption(
        sell_flow_usdt=None,
        buy_flow_usdt=None,
        taker_buy_sell_ratio=None,
        closed_price_return_pct=-1.5,
        support_break_close=True,
    )

    assert result["available"] is False
    assert result["absorption_detected"] is False
    assert "ABSORPTION_DATA_UNAVAILABLE" in result["reason_codes"]
