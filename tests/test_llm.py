#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单测：把测试图直接喂给 LLM，验证识别与 JSON 抽取链路（不写日历）。"""
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
    events = snapcalendar.extract_events_llm(png, cfg)
    if not events:
        print("结果：未识别到日程 ❌")
        sys.exit(1)
    print(f"结果：识别到 {len(events)} 条日程 ✓")
    for e in events:
        print(f"  - {e['title']} | {e['start']:%Y-%m-%d %H:%M} ~ "
              f"{e['end']:%H:%M if e['end'] else '?'} | 置信度 {e['confidence']} | 原文：{e['quote'][:40]}")


if __name__ == "__main__":
    main()
