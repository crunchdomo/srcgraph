"""Calls made through an object: an edge is drawn only when the code itself says what the object is."""
import pytest

from srcgraph.scan import scan

MODEL = '''\
class Base:
    def __init__(self):
        pass

    def helper(self):
        pass


class A(Base):
    def run(self):
        self.helper()

    def helper(self):
        super().helper()


class Other:
    def run(self):
        pass


def make() -> A:
    return A()


def make_unannotated():
    a = A()
    return a


def mystery():
    return object()


shared = A()
'''


def calls(tmp_path, monkeypatch, files: dict[str, str]) -> set[tuple[str, str]]:
    for name, text in {"model.py": MODEL, **files}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    monkeypatch.chdir(tmp_path)
    return {(e.src.split("::")[-1], e.dst.split("::")[-1]) for e in scan(["."]).edges if e.label == "CALLS"}


def test_calling_a_class_reaches_its_init_through_its_bases(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {})
    assert ("model.make", "model.A") in e                      # as before
    assert ("model.make", "model.Base.__init__") in e          # A defines none, Base does


def test_self_call_reaches_a_method_inherited_from_another_file(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"sub.py": "from model import Base\n\nclass B(Base):\n    def go(self):\n        self.helper()\n"})
    assert ("sub.B.go", "model.Base.helper") in e


def test_super_call_reaches_the_base_method_not_its_own(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {})
    assert ("model.A.helper", "model.Base.helper") in e
    assert ("model.A.helper", "model.A.helper") not in e


def test_method_on_a_local_built_from_a_class(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"use.py": "from model import A\n\ndef f():\n    x = A()\n    x.run()\n    x.helper()\n"})
    assert ("use.f", "model.A.run") in e and ("use.f", "model.A.helper") in e


def test_a_reassignment_to_something_unknown_forgets_the_type(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"use.py": "from model import A, mystery\n\ndef f():\n    x = A()\n    x = mystery()\n    x.run()\n"})
    assert not any(src == "use.f" and dst.endswith(".run") for src, dst in e)


@pytest.mark.parametrize("annotation", ["A", '"A"', "A | None", "Optional[A]", "model.A"])
def test_method_on_an_annotated_parameter(tmp_path, monkeypatch, annotation):
    e = calls(tmp_path, monkeypatch, {"use.py": f"import model\nfrom typing import Optional\nfrom model import A\n\ndef g(a: {annotation}):\n    a.run()\n"})
    assert ("use.g", "model.A.run") in e


def test_method_on_the_result_of_an_annotated_function(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"use.py": "from model import make\n\ndef h():\n    x = make()\n    x.run()\n"})
    assert ("use.h", "model.A.run") in e


def test_method_on_the_result_of_a_function_that_returns_what_it_built(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"use.py": "from model import make_unannotated\n\ndef h():\n    x = make_unannotated()\n    x.run()\n"})
    assert ("use.h", "model.A.run") in e


def test_method_on_a_module_variable(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"use.py": "from model import shared\n\ndef k():\n    shared.run()\n"})
    assert ("use.k", "model.A.run") in e


def test_an_untyped_parameter_is_never_guessed(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"use.py": "def f(x):\n    x.run()\n"})
    assert not any(src == "use.f" for src, _ in e)             # both A.run and Other.run exist: no edge to either


def test_a_pytest_fixture_is_called_for_the_test_and_types_its_parameter(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {
        "tests/conftest.py": "import pytest\nfrom model import A\n\n@pytest.fixture\ndef thing():\n    return A()\n\n"
                             "@pytest.fixture\ndef wrapped(thing):\n    yield thing\n",
        "tests/test_x.py": "def test_it(thing, wrapped, nothing):\n    thing.run()\n    wrapped.helper()\n    nothing.run()\n",
    })
    assert ("tests.test_x.test_it", "tests.conftest.thing") in e       # pytest calls the fixture on the test's behalf
    assert ("tests.test_x.test_it", "model.A.run") in e
    assert ("tests.test_x.test_it", "model.A.helper") in e             # a fixture that hands on another fixture
    assert ("tests.conftest.wrapped", "tests.conftest.thing") in e
    assert not any(dst.endswith("Other.run") for _, dst in e)          # `nothing` is no fixture: no edge


OO = '''\
class Engine:
    def start(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class TurboEngine(Engine):
    def start(self):
        pass


class Opaque:
    def __enter__(self):
        return something_unknown()

    def start(self):
        pass


class Car:
    engine: Engine

    def __init__(self):
        self.spare = TurboEngine()

    def by_annotation(self):
        self.engine.start()

    def by_assignment(self):
        self.spare.start()

    def build(self) -> Engine:
        return Engine()

    def fluent(self):
        return self


class Van(Car):
    def inherited_attribute(self):
        self.spare.start()
'''


def test_method_on_an_attribute_annotated_in_the_class_body(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"oo.py": OO})
    assert ("oo.Car.by_annotation", "oo.Engine.start") in e


def test_method_on_an_attribute_assigned_in_a_method_even_from_a_subclass(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"oo.py": OO})
    assert ("oo.Car.by_assignment", "oo.TurboEngine.start") in e
    assert ("oo.Van.inherited_attribute", "oo.TurboEngine.start") in e
    assert ("oo.Car.by_assignment", "oo.Engine.start") not in e


def test_with_calls_enter_and_exit_and_types_the_name_it_binds(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"oo.py": OO, "use.py": "from oo import Engine\n\ndef f():\n    with Engine() as e:\n        e.start()\n"})
    assert {("use.f", "oo.Engine.__enter__"), ("use.f", "oo.Engine.__exit__"), ("use.f", "oo.Engine.start")} <= e


def test_enter_that_returns_self_gives_back_the_subclass_it_was_called_on(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"oo.py": OO, "use.py": "from oo import TurboEngine\n\ndef f():\n    with TurboEngine() as e:\n        e.start()\n"})
    assert ("use.f", "oo.TurboEngine.start") in e and ("use.f", "oo.Engine.start") not in e


def test_enter_that_returns_something_unknown_types_nothing(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"oo.py": OO, "use.py": "from oo import Opaque\n\ndef f():\n    with Opaque() as o:\n        o.start()\n"})
    assert ("use.f", "oo.Opaque.__enter__") in e
    assert not any(src == "use.f" and dst.endswith(".start") for src, dst in e)


def test_method_on_what_a_method_gives_back(tmp_path, monkeypatch):
    e = calls(tmp_path, monkeypatch, {"oo.py": OO, "use.py": "from oo import Car\n\ndef g():\n    c = Car()\n    e = c.build()\n    e.start()\n"
                                                             "    same = c.fluent()\n    same.by_annotation()\n"})
    assert ("use.g", "oo.Car.build") in e and ("use.g", "oo.Engine.start") in e
    assert ("use.g", "oo.Car.by_annotation") in e
