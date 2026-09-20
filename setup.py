"""可编辑安装的兼容入口（元数据统一以 pyproject.toml 为准）。

较旧的 pip（< 21.3）不支持 PEP 660 时，可用
``python setup.py develop`` 完成传统可编辑安装。
"""

from setuptools import setup

setup()
