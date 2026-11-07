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
