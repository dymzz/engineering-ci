from matrix_sample import classify, twice


def test_twice():
    assert twice(2) == 4


def test_positive():
    assert classify(2) == "positive"


def test_nonpositive():
    assert classify(0) == "nonpositive"
