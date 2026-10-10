# -*- coding: utf-8 -*-
"""骑砍2 皮套 Mod 动画预览器 —— 烘焙器。

把游戏本体资产（网格/骨架/动画/材质/贴图/装备定义）与 mod 的全部 TPAC 包
转成查看器可直接加载的目录。所有路径都从 config.env() 取，源码里不出现字面路径。
"""
from .config import env, ensure_dirs, PROJECT_ROOT, DATA_DIR  # noqa: F401

__all__ = ["env", "ensure_dirs", "PROJECT_ROOT", "DATA_DIR"]
__version__ = "0.1.0"
