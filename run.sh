#!/bin/bash
# SnapCalendar 启动脚本：在项目自带的虚拟环境里运行主程序
cd "$(dirname "$0")"
exec ./venv/bin/python snapcalendar.py
