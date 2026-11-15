from fixgraph.lookup import wilson_lower_bound


def test_wilson_beats_lucky_single_success():
    # 1/1 success must not outrank 10/12 — the whole point of the lower bound
    lucky = wilson_lower_bound(1, 1)
    proven = wilson_lower_bound(10, 12)
    assert proven > lucky


def test_wilson_zero_samples():
    assert wilson_lower_bound(0, 0) == 0.0


def test_wilson_monotonic_in_successes():
    assert wilson_lower_bound(9, 10) > wilson_lower_bound(5, 10)
    assert wilson_lower_bound(5, 10) > wilson_lower_bound(1, 10)


def test_wilson_bounds():
    assert 0.0 <= wilson_lower_bound(10, 10) <= 1.0
    assert wilson_lower_bound(0, 10) == 0.0
