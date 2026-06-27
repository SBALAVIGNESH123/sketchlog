"""
Build script for sketchlog C++ extension.
"""

from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext
import os

ext_modules = [
    Pybind11Extension(
        "_sketchlog_cpp",
        sources=[
            "cpp/bindings.cpp",
        ],
        include_dirs=[
            "include",
        ],
        cxx_std=17,
        define_macros=[("NDEBUG", "1")],
        optional=os.environ.get("CIBUILDWHEEL", "0") != "1",
    ),
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
