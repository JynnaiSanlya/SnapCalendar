#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单测：EventKit 写日历链路。创建一个测试事件 → 查询核对 → 删除清理。"""
import sys
from datetime import timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import snapcalendar
from snapcalendar import TZ, get_store, request_calendar_access
from datetime import datetime


def main():
    cfg = snapcalendar.load_config()
    store = get_store()
    print("请求日历访问权限（首次会弹系统授权框，请在弹窗中点「允许」）…")
    if not request_calendar_access(store):
        print("未获得日历授权 ❌")
        sys.exit(1)
    print("日历授权 OK ✓")

    now = datetime.now(TZ)
    ev = {
        "title": "SnapCalendar 测试事件（自动清理）",
        "start": now + timedelta(minutes=10),
        "end": now + timedelta(minutes=70),
        "is_all_day": False,
        "location": "测试会议室",
        "notes": None,
        "confidence": 1.0,
        "quote": "单元测试",
    }
    ident = snapcalendar.add_event(ev, cfg)
    print(f"已创建测试事件：{ident} ✓")

    events = snapcalendar.list_events(cfg, days_ahead=7)
    titles = [str(e.title()) for e in events]
    print(f"日历「{cfg['calendar_name']}」未来 7 天共 {len(titles)} 条事件")
    for t in titles:
        print(f"  - {t}")
    if "SnapCalendar 测试事件（自动清理）" not in titles:
        print("核对失败：未找到刚创建的测试事件 ❌")
        sys.exit(1)
    print("写入+查询核对 OK ✓")

    if snapcalendar.delete_event_by_id(ident):
        print("测试事件已删除（清理完成）✓")


if __name__ == "__main__":
    main()
