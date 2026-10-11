#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 SnapCalendar 工作流程图（assets/flow.png）。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets" / "flow.png"


def font(size, bold=False):
    paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size, index=1 if bold else 0)
        except Exception:
            continue
    return ImageFont.load_default()


STEPS = [
    ("截图触发", ["Ctrl+Cmd+Shift+4", "框选含日程的消息"]),
    ("剪贴板捕获", ["监听器发现新截图", "sha256 去重"]),
    ("本机识别", ["Vision 中文 OCR", "+ 中文日期解析"]),
    ("过滤校验", ["置信度阈值", "事件键防重复"]),
    ("写入日历", ["「来自聊天」日历", "+ 提前提醒 + 通知"]),
]


def main():
    W, H = 1600, 900
    img = Image.new("RGB", (W, H), "#F8FAFC")
    d = ImageDraw.Draw(img)

    d.text((80, 90), "SnapCalendar 工作流程", font=font(64, True), fill="#111827")
    d.text((80, 180), "剪贴板截图 → 本机识别日程 → 写入 macOS 日历", font=font(34), fill="#6B7280")

    n = len(STEPS)
    margin, gap = 80, 44
    box_w = (W - margin * 2 - gap * (n - 1)) // n
    box_h, box_y = 250, 380

    f_label = font(36, True)
    f_desc = font(24)
    f_num = font(26, True)

    for i, (label, desc_lines) in enumerate(STEPS):
        x = margin + i * (box_w + gap)
        d.rounded_rectangle([x, box_y, x + box_w, box_y + box_h], radius=20,
                            fill="#FFFFFF", outline="#93B4F0", width=3)
        # 序号徽章
        cx, cy = x + 34, box_y + 34
        d.ellipse([cx - 24, cy - 24, cx + 24, cy + 24], fill="#2F6BDF")
        tw = d.textlength(str(i + 1), font=f_num)
        d.text((cx - tw / 2, cy - 17), str(i + 1), font=f_num, fill="#FFFFFF")
        # 标题
        d.text((x + 24, box_y + 76), label, font=f_label, fill="#1F2937")
        # 描述
        yy = box_y + 138
        for line in desc_lines:
            d.text((x + 24, yy), line, font=f_desc, fill="#6B7280")
            yy += 36
        # 箭头
        if i < n - 1:
            ax0, ax1 = x + box_w + 6, x + box_w + gap - 6
            ay = box_y + box_h // 2
            d.line([ax0, ay, ax1 - 14, ay], fill="#94A3B8", width=5)
            d.polygon([(ax1 - 16, ay - 11), (ax1, ay), (ax1 - 16, ay + 11)], fill="#94A3B8")

    d.text((W / 2 - 330, 760), "识别全程在本机完成；仅切换 LLM 模式时截图文本会发往云端模型",
           font=font(26), fill="#9CA3AF")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"已生成：{OUT}")


if __name__ == "__main__":
    main()
