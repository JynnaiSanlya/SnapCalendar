#!/bin/bash
# SnapCalendar 一次性授权 + 自测 + 启动（双击本文件即可）
# 首次运行会弹「终端想访问/控制你的日历」「终端想控制系统事件」，点「允许」即可
cd "$(dirname "$0")" || exit 1
mkdir -p state
{
  echo "[$(date '+%H:%M:%S')] == SnapCalendar 启动（首次会弹日历授权，请点「允许」）=="

  # 开机自启：注册登录项（已存在则跳过）
  li_err=$(osascript -e 'tell application "System Events"
      if (count of (login items whose path is "/Users/jynnai/.openclaw-autoclaw/workspace/clip2cal/grant_and_run.command")) is 0 then
          make login item at end with properties {path:"/Users/jynnai/.openclaw-autoclaw/workspace/clip2cal/grant_and_run.command", hidden:false}
      end if
  end tell' 2>&1 >/dev/null)
  if [ -z "$li_err" ]; then
    echo "[✓] 开机自启已就绪（登录项，开机后自动打开本脚本）"
  else
    echo "[!] 登录项注册失败：$li_err（可手动到 系统设置 → 通用 → 登录项与扩展 添加本文件）"
  fi

  ./venv/bin/python -u snapcalendar.py --selftest
  code=$?
  if [ $code -eq 0 ]; then
    echo "[$(date '+%H:%M:%S')] == 监控运行中：保持本窗口开着；按 Ctrl+C 停止 =="
  else
    echo "[!] 启动失败（退出码 $code）。请检查上方日志；稍后重新双击本文件可重试。"
  fi
} 2>&1 | tee -a state/terminal.log
