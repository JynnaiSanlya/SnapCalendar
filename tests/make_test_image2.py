#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成第二张测试图：领导体（任务在前、时间在后、中文数字）。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "test_leader.png"


def load_font(size):
    for candidate in ("/System/Library/Fonts/PingFang.ttc",
                      "/System/Library/Fonts/Hiragino Sans GB.ttc"):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    W, H = 1100, 420
    img = Image.new("RGB", (W, H), "#F5F6F7")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 64], fill="#EDEEF0")
    d.text((24, 14), "项目组（8）", font=load_font(30), fill="#1F2937")

    d.rounded_rectangle([(60, 100), (1040, 300)], radius=18,
                        fill="#FFFFFF", outline="#E3E5E8", width=2)
    lines = [
        "王总：小刘，明天上午把机房巡检一下，另外把",
        "季度总结的PPT改一版。",
        "周四上午十点前发到我邮箱。",
    ]
    y = 124
    for text in lines:
        d.text((96, y), text, font=load_font(36), fill="#1F2937")
        y += 58
    img.save(OUT)
    print(f"已生成：{OUT}")


if __name__ == "__main__":
    main()
