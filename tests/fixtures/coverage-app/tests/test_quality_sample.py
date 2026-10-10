import pytest

from quality_sample import classify, twice


@pytest.mark.fast
def test_twice():
    assert twice(2) == 4


@pytest.mark.slow
def test_classify():
    assert classify(2) == "positive"
