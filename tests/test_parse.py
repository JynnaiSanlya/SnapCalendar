#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单测：中文日程文本解析边界用例（不 OCR、不联网）。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import snapcalendar
from snapcalendar import TZ

NOW = datetime(2026, 10, 11, 19, 0, tzinfo=TZ)  # 周日


def ev_of(text):
    events = snapcalendar.parse_schedule_from_text(text, now=NOW)
    return events


def main():
    failures = []

    def check(name, cond, detail=""):
        status = "✓" if cond else "❌"
        print(f"{status} {name} {detail}")
        if not cond:
            failures.append(name)

    # 1. 相对日期 + 时段词
    evs = ev_of("明天下午3点开产品会")
    check("明天下午3点 → 10-12 15:00", len(evs) == 1 and
          f"{evs[0]['start']:%m-%d %H:%M}" == "10-12 15:00" and evs[0]["is_all_day"] is False,
          str([(f"{e['start']:%m-%d %H:%M}", e['title']) for e in evs]))

    # 2. 下周 + 晚上
    evs = ev_of("下周三晚上8点聚餐")
    check("下周三20:00 → 10-14", len(evs) == 1 and
          f"{evs[0]['start']:%m-%d %H:%M}" == "10-14 20:00",
          str([(f"{e['start']:%m-%d %H:%M}") for e in evs]))

    # 3. 纯日期 → 全天
    evs = ev_of("10月20日交项目报告")
    check("10月20日 → 全天", len(evs) == 1 and evs[0]["is_all_day"] is True
          and f"{evs[0]['start']:%m-%d}" == "10-20",
          str([(e['is_all_day'], f"{e['start']:%m-%d}") for e in evs]))

    # 4. 本周已过的星期 → 顺延下周
    evs = ev_of("周五15:00-17:00面试")
    check("周五(周日看) → 顺延10-16", len(evs) == 1 and
          f"{evs[0]['start']:%m-%d %H:%M}" == "10-16 15:00",
          str([(f"{e['start']:%m-%d %H:%M}") for e in evs]))

    # 5. 噪音：时间戳行 + 无关文本 → 无日程
    evs = ev_of("昨天 下午2:30\n今天天气不错\n改天再约")
    check("噪音行 → 0 条", len(evs) == 0, str(evs))

    # 6. 时间在日期下一行（跨行）→ 至少不要产生错误日期
    evs = ev_of("评审会\n10月14日\n下午2点开始")
    ok = all(f"{e['start']:%m-%d}" in ("10-11", "10-12", "10-14") for e in evs)
    check("跨行场景不崩、日期合理", ok, str([(f"{e['start']:%m-%d %H:%M}", e['title']) for e in evs]))

    # 7. 带年份 + 分钟
    evs = ev_of("2026年12月1日 09:30-11:30 全员大会")
    check("带年份 12-01 09:30", len(evs) == 1 and
          f"{evs[0]['start']:%Y-%m-%d %H:%M}" == "2026-12-01 09:30",
          str([(f"{e['start']:%Y-%m-%d %H:%M}") for e in evs]))

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{failures}")
        sys.exit(1)
    print("全部通过 ✓")


if __name__ == "__main__":
    main()
