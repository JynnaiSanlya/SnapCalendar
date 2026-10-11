#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单测：领导体/跨句语义关联（任务在前、时间在后、中文数字、截止语义）。"""
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import snapcalendar
from snapcalendar import TZ

NOW = datetime(2026, 10, 11, 20, 0, tzinfo=TZ)  # 周日

CASES = [
    # (名称, 文本, 期望条数, 校验函数列表[(描述, lambda events)])
    ("任务在前+截止在后", "小刘，把季度总结的PPT改一版，重点突出Q3数据。周四上午十点前发到我邮箱。",
     1, [
        ("周四10:00", lambda evs: f"{evs[0]['start']:%m-%d %H:%M}" == "10-15 10:00"),
        ("标题含PPT", lambda evs: "PPT" in evs[0]["title"]),
        ("标记截止", lambda evs: "截止" in evs[0]["title"]),
     ]),
    ("时间在前任务在后", "明天下午三点到五点，产品评审会，3号会议室。",
     1, [
        ("10-12 15:00~17:00", lambda evs: f"{evs[0]['start']:%m-%d %H:%M}~{evs[0]['end']:%H:%M}" == "10-12 15:00~17:00"),
        ("标题含评审会", lambda evs: "评审会" in evs[0]["title"]),
        ("地点3号会议室", lambda evs: evs[0]["location"] and "3号" in evs[0]["location"]),
     ]),
    ("中文数字时间", "今晚十点半开复盘会",
     1, [
        ("22:30", lambda evs: f"{evs[0]['start']:%m-%d %H:%M}" == "10-11 22:30"),
        ("标题含复盘", lambda evs: "复盘" in evs[0]["title"]),
     ]),
    ("日期+任务无时刻 → 全天", "下周一交月报",
     1, [
        ("10-12 全天", lambda evs: evs[0]["is_all_day"] and f"{evs[0]['start']:%m-%d}" == "10-12"),
        ("标题含月报", lambda evs: "月报" in evs[0]["title"]),
     ]),
    ("闲聊不误报", "今天辛苦了，大家早点休息，改天再约", 0, []),
    ("截屏典型：多事件", "明天上午把机房巡检一下，周四下午2点开架构评审会",
     2, [
        ("含巡检事件", lambda evs: any("巡检" in e["title"] for e in evs)),
        ("周四14:00评审会", lambda evs: any(
            f"{e['start']:%m-%d %H:%M}" == "10-15 14:00" and "评审" in e["title"] for e in evs)),
     ]),
]


def main():
    failures = []
    for name, text, expect_n, checks in CASES:
        evs = snapcalendar.parse_schedule_from_text(text, now=NOW)
        ok = len(evs) == expect_n
        detail = f"得到 {len(evs)} 条: " + "; ".join(
            f"{e['title']}@{e['start']:%m-%d %H:%M}{'全天' if e['is_all_day'] else ''}" for e in evs)
        if ok:
            for desc, fn in checks:
                try:
                    if not fn(evs):
                        ok = False
                        detail += f" | 校验失败: {desc}"
                except Exception as ex:
                    ok = False
                    detail += f" | 校验异常 {desc}: {ex}"
        print(("✓ " if ok else "❌ ") + name + " — " + detail)
        if not ok:
            failures.append(name)
    print()
    if failures:
        print(f"失败 {len(failures)} 项：{failures}")
        sys.exit(1)
    print("全部通过 ✓")


if __name__ == "__main__":
    main()
