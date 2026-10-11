#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单测：Vision OCR + 中文日程解析（不写日历、不联网）。"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import snapcalendar


def main():
    cfg = snapcalendar.load_config()
    img_path = Path(__file__).resolve().parent / "test_schedule.png"
    png = img_path.read_bytes()
    print(f"测试图：{img_path}（{len(png) // 1024} KB）")
    text = snapcalendar.vision_ocr(png)
    print("--- OCR 文本 ---")
    print(text)
    print("--- 解析结果 ---")
    events = snapcalendar.parse_schedule_from_text(text)
    if not events:
        print("未识别到日程 ❌")
        sys.exit(1)
    print(f"识别到 {len(events)} 条日程 ✓")
    for e in events:
        when = "全天 " + f"{e['start']:%m月%d日}" if e["is_all_day"] else \
            f"{e['start']:%m月%d日 %H:%M} ~ {e['end']:%H:%M}"
        print(f"  - {e['title']} | {when} | 地点:{e['location']} | 置信度 {e['confidence']}")


if __name__ == "__main__":
    main()
