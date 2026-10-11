#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成第三张测试图：客户消息，多事件（会议 + 上线截止）。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "test_client.png"


def load_font(size):
    for candidate in ("/System/Library/Fonts/PingFang.ttc",
                      "/System/Library/Fonts/Hiragino Sans GB.ttc"):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    W, H = 1100, 400
    img = Image.new("RGB", (W, H), "#F5F6F7")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 64], fill="#EDEEF0")
    d.text((24, 14), "客户对接群（9）", font=load_font(30), fill="#1F2937")

    d.rounded_rectangle([(60, 100), (1040, 290)], radius=18,
                        fill="#FFFFFF", outline="#E3E5E8", width=2)
    lines = [
        "王总：2.3版本先把已知问题都处理掉，",
        "周五下午4点在B会议室和我同步一下进度。",
        "下周三上线，别错过了。",
    ]
    y = 122
    for text in lines:
        d.text((96, y), text, font=load_font(36), fill="#1F2937")
        y += 56
    img.save(OUT)
    print(f"已生成：{OUT}")


if __name__ == "__main__":
    main()
