#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成一张模拟聊天截图（含日程信息），用于测试识别链路。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "test_schedule.png"


def load_font(size):
    for candidate, idx in [
        ("/System/Library/Fonts/PingFang.ttc", 0),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Songti.ttc", 0),
    ]:
        try:
            return ImageFont.truetype(candidate, size, index=idx)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    W, H = 1000, 420
    img = Image.new("RGB", (W, H), "#F5F6F7")
    d = ImageDraw.Draw(img)

    # 顶部栏，模拟聊天窗口标题
    d.rectangle([0, 0, W, 64], fill="#EDEEF0")
    d.text((24, 14), "产品项目群（6）", font=load_font(30), fill="#1F2937")

    # 一条浅色气泡
    bubble = [(60, 100), (940, 380)]
    d.rounded_rectangle(bubble, radius=18, fill="#FFFFFF", outline="#E3E5E8", width=2)
    font_body = load_font(38)
    lines = [
        ("各位，产品评审会安排如下：", "#1F2937"),
        ("时间：10月13日（周二）15:00-16:00", "#1F2937"),
        ("地点：3号会议室", "#1F2937"),
        ("请准时参加，会前提交进度报告", "#1F2937"),
    ]
    y = 124
    for text, color in lines:
        d.text((96, y), text, font=font_body, fill=color)
        y += 60

    img.save(OUT)
    print(f"已生成测试图：{OUT}")


if __name__ == "__main__":
    main()
