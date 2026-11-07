def boom(x):
    inner(x)

def inner(x):
    raise ValueError(f"bad {x}")
