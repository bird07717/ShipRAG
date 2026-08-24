from __future__ import annotations

import re

from app.support.models import RiskLevel

HIGH_RISK = re.compile(
    r"固件|刷机|格式化|删除|覆盖|断电|拆机|接线|更换硬件|电压|绕过保护|恢复出厂", re.I
)
MEDIUM_RISK = re.compile(r"修改.*(?:ip|网络|配置)|登录设备|重启|重新插拔", re.I)


def classify_risk(text: str) -> RiskLevel:
    if HIGH_RISK.search(text):
        return RiskLevel.HIGH
    if MEDIUM_RISK.search(text):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


GENERAL_DISCLAIMER = "这是通用操作建议，不是当前文档中的产品专用处理流程。"
