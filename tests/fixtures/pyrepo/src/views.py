import functools
from functools import lru_cache as cache
from src.contracts.c import Contract

def route(path):
    def deco(fn):
        return fn
    return deco

class Base:
    pass

class View(Base, Contract):
    @cache
    def get(self):
        pass

@route("/x")
@functools.wraps(route)
def handler():
    pass

class Plain:
    pass

class Dyn(make_base()):
    pass

class Repr:
    def __repr__(self):
        return "r"

import html.parser

class Parser(html.parser.HTMLParser):
    def handle_data(self, data):
        pass

    def helper(self):
        pass
