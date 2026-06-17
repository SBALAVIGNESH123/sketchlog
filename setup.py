"""
Build script for sketchlog C++ extension.
"""

from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

ext_modules = [
    Pybind11Extension(
        "_sketchlog_cpp",
        sources=[
            "cpp/bindings.cpp",
            "src/ddsketch.cpp",
            "src/hyperloglog.cpp",
            "src/countmin.cpp",
            "src/sketchlog.cpp",
        ],
        include_dirs=[
            "include",
        ],
        cxx_std=17,
        define_macros=[("NDEBUG", "1")],
        optional=True,
    ),
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
