from os import path

from setuptools import setup

this_directory = path.abspath(path.dirname(__file__))
with open(path.join(this_directory, "README.md"), encoding="utf-8") as handle:
    long_description = handle.read()

setup(
    name="cbpi4-WhirlpoolHold",
    version="0.1.0",
    description="CraftBeerPi 4 step that holds whirlpool temperature with the HLT PID",
    author="cbpi4-WhirlpoolHold",
    url="https://github.com/braubunker/cbpi4-WhirlpoolHold",
    license="GPLv3",
    include_package_data=True,
    package_data={"cbpi4_WhirlpoolHold": ["config.yaml"]},
    packages=["cbpi4_WhirlpoolHold"],
    python_requires=">=3.8",
    long_description=long_description,
    long_description_content_type="text/markdown",
)
