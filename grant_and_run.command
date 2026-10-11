#!/bin/bash
# SnapCalendar 一次性授权 + 自测 + 启动（双击本文件即可）
# 首次运行会弹日历授权框（"终端"想访问/控制你的日历），点「允许」即可，之后不会再问
cd "$(dirname "$0")" || exit 1
mkdir -p state
{
  echo "[$(date '+%H:%M:%S')] == SnapCalendar 启动（首次会弹日历授权，请点「允许」）=="
  ./venv/bin/python -u snapcalendar.py --selftest
  code=$?
  if [ $code -eq 0 ]; then
    echo "[$(date '+%H:%M:%S')] == 监控运行中：保持本窗口开着；按 Ctrl+C 停止 =="
  else
    echo "[!] 启动失败（退出码 $code）。请检查上方日志；稍后重新双击本文件可重试。"
  fi
} 2>&1 | tee -a state/terminal.log
