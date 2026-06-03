"""
mirror_tool.py — 高达版记忆工具
独立化的 mirror 工具集
"""

import logging

logger = logging.getLogger(__name__)

_mirror_instance = None


def set_mirror_instance(mirror):
    """注册 mirror 实例供工具使用"""
    global _mirror_instance
    _mirror_instance = mirror
    logger.info("mirror 实例已注册")


def get_mirror():
    """获取 mirror 实例"""
    return _mirror_instance
