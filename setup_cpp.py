"""
Build script for sketchlog C++ extension.

Usage:
    pip install -e .           # installs with C++ extension if compiler available
    python setup_cpp.py build  # build only
"""

import os
import sys
from setuptools import setup, Extension
from pybind11.setup_helpers import Pybind11Extension, build_ext

ROOT = os.path.dirname(os.path.abspath(__file__))

ext_modules = [
    Pybind11Extension(
        "_sketchlog_cpp",
        sources=[
            os.path.join(ROOT, "cpp", "bindings.cpp"),
            os.path.join(ROOT, "src", "ddsketch.cpp"),
            os.path.join(ROOT, "src", "hyperloglog.cpp"),
            os.path.join(ROOT, "src", "countmin.cpp"),
            os.path.join(ROOT, "src", "sketchlog.cpp"),
        ],
        include_dirs=[
            os.path.join(ROOT, "include"),
        ],
        cxx_std=17,
        define_macros=[("NDEBUG", "1")],
    ),
]

setup(
    name="sketchlog-cpp",
    version="0.1.0",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
