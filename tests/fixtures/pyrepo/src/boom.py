def boom(x):
    inner(x)

def inner(x):
    raise ValueError(f"bad {x}")

def inner_ok(i):
    return i

def loop(n):
    for i in range(n):
        inner_ok(i)

def echo(x):
    return x

class Grid:
    shape = (3, 4)
