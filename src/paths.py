# -*- coding: utf-8 -*-
"""统一路径解析(目录结构化后的单一来源, mysql-console v3.5+)。

布局(开发仓库与发布包一致):
  <APP_ROOT>/                项目/部署根 —— src/ 的父目录
  ├── src/                   全部 Python 源码 + static/
  │   ├── paths.py
  │   ├── server.py ...
  │   └── static/            前端静态资源(index.html / app.js / style.css / login.html / echarts.min.js)
  ├── docs/  scripts/  tests/
  └── data/                  运行时数据(不入库; MC_DATA_DIR 可重定位)

兼容旧版平铺布局: 若本文件所在目录名为 `src`(新布局), APP_ROOT=父目录;
否则(旧版平铺: *.py 与 data/ 同层), APP_ROOT=本文件所在目录。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _resolve_app_root():
    if os.path.basename(_HERE) == "src":
        return os.path.dirname(_HERE)          # 开发仓库 / 发布包:部署根 = src 的父目录
    if os.path.basename(_HERE) in ("site-packages", "dist-packages"):
        # pip 安装:代码进入 site-packages,部署根无意义 → 数据默认放用户目录 ~/.mysql-console
        return os.path.join(os.path.expanduser("~"), ".mysql-console")
    return _HERE                               # 旧平铺布局兼容


APP_ROOT = _resolve_app_root()

# 运行时数据目录: 环境变量 > 部署根 data/(统一存储、密钥、日志、备份全部在此)
DATA_DIR = os.environ.get("MC_DATA_DIR") or os.path.join(APP_ROOT, "data")

# pip 安装形态下的静态资源兜底(数据文件安装到 <sys.prefix>/share/mysql-console/static)
_PIP_STATIC = os.path.join(sys.prefix, "share", "mysql-console", "static")


def static_dir():
    """前端静态资源目录: 优先源码树 src/static, 其次 pip 数据文件目录。"""
    for cand in (os.path.join(_HERE, "static"), _PIP_STATIC):
        if os.path.isdir(cand):
            return cand
    return os.path.join(_HERE, "static")