from pathlib import Path
from argparse import Namespace

import pytest

from pydecanter import PyDecanter, DEFAULT_ARGS


@pytest.fixture(scope="session")
def base_root(tmp_path_factory):
    return tmp_path_factory.mktemp("root")


@pytest.fixture(scope="session")
def decanter(base_root):
    args = Namespace(**{**DEFAULT_ARGS, **{"base_root": base_root, "ini_file": None}})
    return PyDecanter(args)


def test_index_get(decanter, base_root):
    index = base_root / "index.html"
    with index.open("w") as _fh:
        _fh.write("<h1>hello world!</h1>")

    assert decanter.get("/") == b"<h1>hello world!</h1>"


def test_subdir_get(decanter, base_root):
    subdir = base_root / "sub"
    subdir.mkdir()
    index = subdir / "hello.html"
    with index.open("w") as _fh:
        _fh.write("<h1>hello world!</h1>")

    assert decanter.get("/sub/hello.html") == b"<h1>hello world!</h1>"
