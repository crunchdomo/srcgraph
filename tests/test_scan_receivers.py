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
