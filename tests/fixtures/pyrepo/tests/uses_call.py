from src.contracts.c import call
from src.contracts import warm

def test_call():
    call(1)
    warm()

def test_fails():
    assert call(1) == 2
