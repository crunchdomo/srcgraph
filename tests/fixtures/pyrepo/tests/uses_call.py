from src.contracts.c import call

def test_call():
    call(1)

def test_fails():
    assert call(1) == 2
