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

class Shape:
    def area(self):
        return 12

    def never(self):
        pass

def use(s):
    return s.area()

def twice(x):
    return echo(echo(x))
def apply(fn, x):
    return fn(x)

def by_name(x):
    return x

def passer(x):
    return apply(by_name, x)

def catcher():
    try:
        pass
    except ValueError as err:
        return err
