#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SnapCalendar — 剪贴板截图 → 日程抽取 → 写入 macOS 日历

用法：
    ./run.sh                    # 启动后常驻后台，监控剪贴板
    按 Ctrl+Cmd+Shift+4 框选截图（进剪贴板）→ 自动识别 → 写入日历 → 弹通知

数据流：
    剪贴板出现新图片
      → sha256 去重
      → 调多模态 LLM 抽取日程 JSON（当前时间锚定，Asia/Shanghai）
      → 置信度过滤 + 事件键去重
      → EventKit 写入独立日历（默认"来自聊天"，带提前提醒）
      → macOS 通知播报结果
"""
import base64
import fcntl
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
import objc
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
STATE_DIR = BASE / "state"
TZ = ZoneInfo("Asia/Shanghai")

SYSTEM_PROMPT = """你是日程抽取助手。用户会给你一张屏幕截图（可能来自微信、QQ、邮件、网页等），你的任务是识别图中明确提到的日程安排（会议、约会、活动、截止时间、提醒等）。

规则：
1. 只抽取图中明确出现的时间安排，不要推测或编造图中没有的信息。
2. 模糊表述不算日程，例如"下周找时间聚聚""改天再说""尽快回复"。
3. 相对时间（明天、后天、下周三、今晚8点）以用户消息中提供的"当前时间"为基准换算为绝对时间。
4. 图中没写年份时，默认取距离当前时间最近的未来日期。
5. 未说明时长的活动默认时长 1 小时。
6. 只有日期没有具体时刻的事项（如"10月20日交报告"）设 is_all_day=true。
7. confidence 为你对"这是一条真实日程"的把握（0 到 1 的小数）。
8. quote 字段抄录图中与该日程最相关的原文片段。
9. 产品/项目上线、发布、交付、验收、签约等节点也算日程（通常为全天事件）。

只输出 JSON，不要输出任何其他文字、解释或 markdown 代码块标记，格式：
{"events": [{"title": "事件标题", "start": "YYYY-MM-DDTHH:MM:SS+08:00", "end": "YYYY-MM-DDTHH:MM:SS+08:00 或 null", "is_all_day": false, "location": "地点或 null", "notes": "备注或 null", "confidence": 0.9, "quote": "图中原文片段"}]}

没有日程时输出：{"events": []}"""

SYSTEM_PROMPT_TEXT = """你是日程抽取助手。用户会给你一段从屏幕截图 OCR 得来的消息文本（可能来自微信、QQ、邮件、网页聊天），可能有少量 OCR 错别字，请按语义理解。识别其中明确提到的日程安排（会议、约会、活动、任务截止时间、提醒等）。
规则：
1. 只抽取有明确时间指向的安排，不要编造；模糊表述（"改天""找时间""有空再说"）不算日程。
2. 相对时间（明天、后天、下周三、今晚八点）以用户消息提供的"当前时间"和日期对照表为基准换算为绝对时间，直接查表，不要自己算星期。一周从星期一开始；今天恰逢星期日时，"下周X"指明天开始那一周的星期X；存在歧义时取更近的未来日期。
3. 文本没写年份时，默认取距离当前时间最近的未来日期。
4. 未说明时长的活动默认 1 小时；只有日期没时刻的设 is_all_day=true。
5. "X点前发/交"是截止时间，事件时间就是该截止时刻，标题前加"截止："。
6. 任务和时间不在同一句时，把任务短语作为标题（例如领导先布置任务、后面才说时间），quote 抄录相关原文。
7. confidence 为你对"这是一条真实日程"的把握（0 到 1 的小数）。
8. 产品/项目上线、发布、交付、验收、签约等节点也算日程（通常为全天事件）。
只输出 JSON，不要输出任何其他文字、解释或 markdown 代码块标记，格式：
{"events": [{"title": "事件标题", "start": "YYYY-MM-DDTHH:MM:SS+08:00", "end": "YYYY-MM-DDTHH:MM:SS+08:00 或 null", "is_all_day": false, "location": "地点或 null", "notes": "备注或 null", "confidence": 0.9, "quote": "图中原文片段"}]}

没有日程时输出：{"events": []}"""


# ---------------- 基础工具 ----------------

def log(msg):
    print(f"[{datetime.now(TZ):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def notify(title, msg):
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{esc(msg)}" with title "{esc(title)}" sound name "Glass"'],
            check=False, timeout=15,
        )
    except Exception as e:
        log(f"通知发送失败：{e}")


def load_config():
    """优先 config.local.json（本地私密配置，不入库），其次模板。"""
    for name in ("config.local.json", "config.json", "config.example.json"):
        p = BASE / name
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError("找不到配置文件：请复制 config.example.json 为 config.local.json")


def load_state():
    p = STATE_DIR / "state.json"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_images": [], "created_keys": []}


def save_state(state):
    STATE_DIR.mkdir(exist_ok=True)
    p = STATE_DIR / "state.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def append_jsonl(obj):
    STATE_DIR.mkdir(exist_ok=True)
    with open(STATE_DIR / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


# ---------------- 剪贴板 ----------------

def read_clipboard_png(pb):
    """从剪贴板读图片，统一转成 PNG 字节；没有图片返回 None。"""
    types = [str(t) for t in (pb.types() or [])]
    from AppKit import NSBitmapImageRep, NSBitmapImageFileTypePNG
    if "public.png" in types:
        data = pb.dataForType_("public.png")
        return bytes(data) if data else None
    if "public.tiff" in types:
        data = pb.dataForType_("public.tiff")
        if not data:
            return None
        rep = NSBitmapImageRep.imageRepWithData_(data)
        if rep is None:
            return None
        png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        return bytes(png) if png else None
    return None


# ---------------- LLM 抽取 ----------------

def parse_dt(s):
    text = str(s).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def parse_events(content):
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:]
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1 or j <= i:
        raise ValueError(f"回复中没有 JSON：{content[:200]}")
    obj = json.loads(text[i:j + 1])
    out = []
    for ev in obj.get("events", []):
        try:
            start = parse_dt(ev["start"])
            end = parse_dt(ev["end"]) if ev.get("end") else None
            out.append({
                "title": str(ev.get("title") or "未命名日程").strip(),
                "start": start,
                "end": end,
                "is_all_day": bool(ev.get("is_all_day")),
                "location": ev.get("location") or None,
                "notes": ev.get("notes") or None,
                "confidence": float(ev.get("confidence", 0.5)),
                "quote": ev.get("quote") or "",
            })
        except Exception as e:
            log(f"丢弃无法解析的事件：{ev} ({e})")
    return out


LLM_BASE = ""
LLM_KEY = ""
LLM_MODELS = []
LLM_OK = False


def _walk_llm_candidates(node, path, out):
    """在 JSON 里递归找 provider 形态的 API key（跳过本地代理用的 autoclaw 占位 key）。"""
    if isinstance(node, dict):
        label = " ".join(str(node.get(k)) for k in ("name", "id", "provider", "label", "type")
                         if isinstance(node.get(k), str))
        for k, v in node.items():
            kl = str(k).lower()
            if isinstance(v, str) and kl in ("apikey", "api_key", "key") \
                    and len(v) >= 24 and not v.lower().startswith("autoclaw"):
                out.append({"path": f"{path}/{k}", "label": label, "key": v, "node": node})
            _walk_llm_candidates(v, f"{path}/{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_llm_candidates(v, f"{path}[{i}]", out)


def decide_llm_config(cfg):
    """LLM 凭据解析：优先 config.api_key；否则从 api_key_file（如 OpenClaw 配置）按提示搜索。"""
    global LLM_BASE, LLM_KEY, LLM_MODELS
    LLM_BASE = (cfg.get("api_base") or "").strip()
    LLM_KEY = (cfg.get("api_key") or "").strip()
    LLM_MODELS = [m for m in (cfg.get("models") or []) if isinstance(m, str) and m]
    if LLM_KEY:
        return True
    f = Path(str(cfg.get("api_key_file") or "")).expanduser()
    if not f.exists():
        return False
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"读取 {f.name} 失败：{e}")
        return False
    cands = []
    _walk_llm_candidates(data, "", cands)
    if not cands:
        return False
    hint = (cfg.get("api_key_provider_hint") or "zai").lower()
    picked = next((c for c in cands if hint in (c["label"] + " " + c["path"]).lower()), None) \
        or cands[0]
    LLM_KEY = picked["key"]
    node = picked["node"]
    if not LLM_BASE:
        LLM_BASE = str(node.get("baseUrl") or node.get("base_url") or node.get("apiBase") or "").strip()
    if not LLM_MODELS and isinstance(node.get("models"), list):
        ms = [m for m in node["models"] if isinstance(m, str) and m]
        if ms:
            LLM_MODELS = ms
    if not LLM_BASE:
        LLM_BASE = "https://api.z.ai/api/paas/v4"
    if not LLM_MODELS:
        LLM_MODELS = ["glm-4.5v", "glm-4.6", "glm-4v-plus", "glm-4v"]
    log(f"LLM key 已从 {f.name} 解析（{picked['path']}，key=…{LLM_KEY[-4:]}，base={LLM_BASE}）")
    return True


def ping_llm():
    payload = {"model": LLM_MODELS[0], "messages": [{"role": "user", "content": "回复OK"}],
               "max_tokens": 8}
    req = urllib.request.Request(
        LLM_BASE.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_KEY}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        json.loads(resp.read().decode("utf-8"))


def extract_events_llm(png_bytes, cfg):
    text = vision_ocr(png_bytes)
    log("OCR 文本：" + text.replace("\n", " ⏎ ")[:300])
    now = datetime.now(TZ)
    weekdays = "一二三四五六日"
    now_text = now.strftime(f"%Y-%m-%d %H:%M（星期{weekdays[now.weekday()]}）+08:00")
    def _anchor(i):
        dt = now + timedelta(days=i)
        return f"{dt.month}月{dt.day}日=星期{weekdays[dt.weekday()]}"
    anchors = "；".join(_anchor(i) for i in range(0, 15))
    sunday_hint = ""
    if now.weekday() == 6:  # 周日说"下周X"歧义高发：按中国习惯（周一起始）给出确切答案
        nxt = now.date() + timedelta(days=1)
        sunday_hint = (f"（注意：今天恰逢星期日。按周一为一周起点的中国习惯，"
                       f"下周一下周日的日期是："
                       + "；".join(f"下周{weekdays[i]}={(nxt + timedelta(days=i)).month}月{(nxt + timedelta(days=i)).day}日"
                                   for i in range(7))
                       + "）")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_TEXT},
        {"role": "user", "content":
         f"当前时间：{now_text}。日期对照表（未来 15 天，供你换算相对日期，直接查表）：{anchors}。{sunday_hint}"
         f"以下是通过屏幕截图 OCR 得到的消息文本：\n{text}"},
    ]
    last_err = None
    for model in (LLM_MODELS or ["glm-4.5v"]):
        try:
            payload = {"model": model, "messages": messages, "temperature": 0.1}
            req = urllib.request.Request(
                LLM_BASE.rstrip("/") + "/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {LLM_KEY}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return parse_events(content)
        except Exception as e:
            last_err = e
            log(f"模型 {model} 调用失败：{e}")
    raise RuntimeError(f"所有模型调用失败：{last_err}")


# ---------------- 本机抽取（Vision OCR + 中文日期解析） ----------------

WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

DATE_MD_RE = re.compile(r"(?:(20\d{2})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
DATE_REL_RE = re.compile(r"(今天|今日|今晚|明天|明日|明晚|后天|大后天)")
DATE_WEEK_RE = re.compile(r"(下下周|下周|下星期|下个星期|本周|这周|本星期|这个星期|星期|礼拜|周)([一二三四五六日天])")
DATE_NUM_RE = re.compile(r"(?<!\d)(\d{1,2})[月./\-](\d{1,2})(?!\d)")
TIME_RE = re.compile(
    r"(上午|早上|清晨|中午|下午|午后|傍晚|晚上|夜里|今晚|明晚)?\s*"
    r"(\d{1,2})\s*(?:点|时|[:：])\s*(半|\d{1,2})?\s*分?(?!\d)")
TIME_CN_RE = re.compile(
    r"(上午|早上|清晨|中午|下午|午后|傍晚|晚上|夜里|今晚|明晚)?\s*"
    r"([一二两三四五六七八九十]{1,3})点(半|一刻|三刻|([一二两三四五六七八九十]{1,3})分?)?")
RANGE_GAP_RE = re.compile(r"^\s*[-—–~～至到]{1,2}\s*$")
META_LINE_RE = re.compile(r"^(昨天|前天|上午|下午|晚上|凌晨|中午)?\s*\d{1,2}[:：]\d{2}$")
KEYWORDS = ("会议", "开会", "评审", "复盘", "例会", "约会", "见面", "面试", "上课", "讲座",
            "沙龙", "聚会", "聚餐", "截止", "提交", "交付", "出发", "航班", "飞机", "火车",
            "高铁", "体检", "预约", "提醒", "参加", "准时", "生日", "婚礼", "活动", "直播",
            "考试", "值机", "退房", "入住", "周会", "答辩", "汇报", "巡检", "培训", "值班")
ACTION_KWS = KEYWORDS + ("发", "发送", "发给", "发到", "提交", "交给", "交到", "改", "修改",
                         "准备", "完成", "写", "整理", "审核", "检查", "回复", "反馈", "确认",
                         "跟进", "对接", "验收", "上线", "发布", "汇总", "填写", "上传", "打印",
                         "签字", "审批", "采购", "订", "碰", "聊", "过一下", "看一下", "处理")
TASK_NOUN_RE = re.compile(r"PPT|PPTX|报告|总结|文档|方案|材料|数据|周报|日报|月报|计划|简历"
                          r"|稿件|稿子|版本|合同|发票|审批|邮件|表格|清单|纪要|附件|链接|截图|进度")
DELIVERY_RE = re.compile(r"^(发|发送|发给|发到|发我|交|提交|交给|交到|上传|回报|汇报|给)")
DEADLINE_AFTER_RE = re.compile(r"^\s*(之前|以前|前(?![排面提往再后]))")
ADDRESS_RE = re.compile(r"^(各位|大家|大家好|领导|老板|老师们|同学们|兄弟们|小伙伴们|团队)[，,、]?")
NAME_LEAD_RE = re.compile(r"^[\u4e00-\u9fa5]{1,3}[，,](?=\S)")
FILLER_LEAD_RE = re.compile(r"^(另外|还有|然后|记得|麻烦|辛苦|劳烦|请|顺便)[，,]?")
LOC_RE = re.compile(
    r"地点\s*[:：]\s*(\S+)"
    r"|([A-Z\d#\-\u4e00-\u9fff]{0,10}会议室|[A-Z\d]{1,6}教室"
    r"|腾讯会议\S{0,20}|飞书会议\S{0,20}|钉钉会议\S{0,20}|Zoom\S{0,20})")


def vision_ocr(png_bytes):
    import Vision
    from Foundation import NSData
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR 失败：{err}")
    lines = []
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)
        if cand:
            lines.append(str(cand[0].string()))
    return "\n".join(lines)


def _resolve_date(kind, now):
    """kind = ('md'|'rel'|'week'|'num', re.Match) → date 或 None"""
    typ, m = kind
    if typ in ("md",):
        y = int(m.group(1)) if m.group(1) else None
        mo, d = int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        y = y or now.year
        try:
            dt = datetime(y, mo, d, tzinfo=TZ)
        except ValueError:
            return None
        if m.group(1) is None and dt.date() < now.date():
            try:
                dt = dt.replace(year=now.year + 1)
            except ValueError:
                return None
        return dt.date()
    if typ == "num":
        mo, d = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        try:
            dt = datetime(now.year, mo, d, tzinfo=TZ)
        except ValueError:
            return None
        if dt.date() < now.date():
            try:
                dt = dt.replace(year=now.year + 1)
            except ValueError:
                return None
        return dt.date()
    if typ == "rel":
        days = {"今天": 0, "今日": 0, "今晚": 0, "明天": 1, "明日": 1,
                "明晚": 1, "后天": 2, "大后天": 3}[m.group(1)]
        return (now + timedelta(days=days)).date()
    if typ == "week":
        prefix, wd = m.group(1), m.group(2)
        monday = now.date() - timedelta(days=now.weekday())
        if prefix.startswith("下下"):
            monday += timedelta(days=14)
        elif prefix.startswith("下"):
            monday += timedelta(days=7)
        d = monday + timedelta(days=WEEKDAY[wd])
        if prefix in ("星期", "周", "礼拜") and d < now.date():
            d += timedelta(days=7)
        return d
    return None


CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s):
    if not s:
        return None
    if "十" in s:
        a, _, b = s.partition("十")
        tens = CN_DIGIT.get(a, 1) if a else 1
        ones = CN_DIGIT.get(b, 0) if b else 0
        return tens * 10 + ones
    return CN_DIGIT.get(s)


def _apply_period(h, period):
    if period in ("下午", "午后", "傍晚", "晚上", "夜里", "今晚", "明晚") and h < 12:
        h += 12
    return h


def _find_times(text):
    """统一找时间 → [(start, end, hour, minute, period)]，支持中文数字（十点半/两点半/一刻）。"""
    out = []
    for m in TIME_RE.finditer(text):
        h = int(m.group(2))
        mi = 30 if m.group(3) == "半" else (int(m.group(3)) if m.group(3) else 0)
        if h < 24 and mi < 60:
            out.append((m.start(), m.end(), h, mi, m.group(1)))
    for m in TIME_CN_RE.finditer(text):
        h = _cn_to_int(m.group(2))
        if h is None:
            continue
        tail = m.group(3)
        if tail == "半":
            mi = 30
        elif tail == "一刻":
            mi = 15
        elif tail == "三刻":
            mi = 45
        elif tail:
            mi = _cn_to_int(m.group(4)) or 0
        else:
            mi = 0
        if h < 24:
            out.append((m.start(), m.end(), h, mi, m.group(1)))
    out.sort(key=lambda t: t[0])
    dedup = []
    for t in out:
        if dedup and t[0] < dedup[-1][1]:
            continue
        dedup.append(t)
    return dedup


def _make_dt(base_date, h, mi, period=None):
    h = _apply_period(h, period) if period else h
    if h > 23 or mi > 59:
        return None
    return datetime(base_date.year, base_date.month, base_date.day, h, mi, tzinfo=TZ)


def _find_dates(text):
    """→ [(start, end, kind)]，含同段去重（md/num 重复、星期补充说明）。"""
    date_matches = []
    for m in DATE_MD_RE.finditer(text):
        if 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
            date_matches.append((m.start(), m.end(), ("md", m)))
    for m in DATE_REL_RE.finditer(text):
        date_matches.append((m.start(), m.end(), ("rel", m)))
    for m in DATE_WEEK_RE.finditer(text):
        date_matches.append((m.start(), m.end(), ("week", m)))
    for m in DATE_NUM_RE.finditer(text):
        mo, d = int(m.group(1)), int(m.group(2))
        after = text[m.end():m.end() + 1]
        if 1 <= mo <= 12 and 1 <= d <= 31 and not re.search(r"[日号年月分点个位人%]", after):
            date_matches.append((m.start(), m.end(), ("num", m)))
    date_matches.sort()
    filtered = []
    for dm in date_matches:
        drop = False
        if dm[2][0] == "week":
            for p in date_matches:
                if p is dm:
                    continue
                if p[2][0] in ("md", "num") and p[1] <= dm[0] <= p[1] + 3:
                    drop = True
                    break
        if not drop:
            for p in filtered:
                if dm[0] < p[1] and p[0] < dm[1]:
                    drop = True
                    break
        if not drop:
            filtered.append(dm)
    return filtered


def _split_clauses(text):
    """把 OCR 文本拆成子句（保留行号用于地点/上下文关联）。"""
    clauses = []
    for li, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        for seg in re.split(r"[，,。．；;！!？?\n\r]+", line):
            seg = seg.strip()
            if seg:
                clauses.append({"text": seg, "line": li})
    return clauses


def _events_from_clauses(clauses, now):
    """跨子句语义关联：时间子句在同子句/前后子句找主日期；日期无时间 → 全天。"""
    for c in clauses:
        c["dates"] = _find_dates(c["text"])
        c["times"] = _find_times(c["text"])

    out = []
    used_dates = set()  # (clause_idx, date_idx)

    # —— 定时事件：每个时间匹配 ——
    for ci, c in enumerate(clauses):
        times = c["times"]
        gi = 0
        while gi < len(times):
            t1 = times[gi]
            t2 = None
            if gi + 1 < len(times) and RANGE_GAP_RE.match(c["text"][t1[1]:times[gi + 1][0]]):
                t2 = times[gi + 1]
                gi += 2
            else:
                gi += 1

            # 主日期：同子句（时间前面最近的日期；“今晚十点半”这类重叠也算）
            # → 前面 1-2 子句 → 后面 1-2 子句
            host = None  # (clause_idx, date_idx, dm)
            for dj, dm in enumerate(c["dates"]):
                if (ci, dj) in used_dates:
                    continue
                overlaps = dm[0] < t1[1] and dm[1] > t1[0]
                if (dm[0] < t1[0] or overlaps) and (host is None or dm[0] > c["dates"][host[1]][0]):
                    host = (ci, dj, dm)
            if host is None:
                for step in (1, 2):
                    for cj in ([ci - step] if 0 <= ci - step < len(clauses) else []) + \
                              ([ci + step] if 0 <= ci + step < len(clauses) else []):
                        cc = clauses[cj]
                        idxs = [dj for dj in range(len(cc["dates"])) if (cj, dj) not in used_dates]
                        if not idxs:
                            continue
                        dj = idxs[-1] if cj < ci else idxs[0]
                        host = (cj, dj, cc["dates"][dj])
                        break
                    if host:
                        break
            if host:
                used_dates.add((host[0], host[1]))
                d = _resolve_date(host[2][2], now)
                if d is None:
                    continue
                score = 0.88 if host[0] == ci else 0.78
            else:
                if META_LINE_RE.match(c["text"]):
                    continue
                d = now.date()
                score = 0.55

            start = _make_dt(d, t1[2], t1[3], t1[4])
            if start is None:
                continue
            if host is None and start < now - timedelta(hours=2):
                start = _make_dt(d + timedelta(days=1), t1[2], t1[3], t1[4])
            end = _make_dt(d, t2[2], t2[3], t2[4] or t1[4]) if t2 else None
            if end is not None and end <= start:
                end = start + timedelta(hours=1)
            if end is None:
                end = start + timedelta(hours=1)

            after = c["text"][t2[1] if t2 else t1[1]:]
            deadline = bool(DEADLINE_AFTER_RE.match(after))
            out.append({"start": start, "end": end, "is_all_day": False,
                        "clause_idx": ci, "score": score, "deadline": deadline,
                        "host_clause": host[0] if host else None})

    # —— 全天事件：日期无时间 ——
    for ci, c in enumerate(clauses):
        for dj, dm in enumerate(c["dates"]):
            if (ci, dj) in used_dates:
                continue
            if dm[2][0] in ("rel", "week"):
                ctx = any(k in c["text"] for k in ACTION_KWS) or re.search(
                    r"时间|地点|截止|提醒|请|参加|交|约", c["text"])
                near_ctx = any(
                    any(k in clauses[cj]["text"] for k in ACTION_KWS)
                    for cj in (ci - 1, ci + 1) if 0 <= cj < len(clauses))
                if not (ctx or near_ctx):
                    continue
            d = _resolve_date(dm[2], now)
            if d is None:
                continue
            score = 0.75 if dm[2][0] in ("md", "num") else 0.6
            out.append({"start": datetime(d.year, d.month, d.day, tzinfo=TZ), "end": None,
                        "is_all_day": True, "clause_idx": ci, "score": score,
                        "deadline": False, "host_clause": ci})
    return out


def _clean_clause(s):
    """清理子句：去掉称呼、容套话、尾巴，剩下任务描述。"""
    s = ADDRESS_RE.sub("", s)
    s = NAME_LEAD_RE.sub("", s)
    s = FILLER_LEAD_RE.sub("", s)
    s = re.sub(r"^(时间|日期|安排|活动|约)\s*[:：]\s*", "", s)
    s = re.sub(r"[【】\[\]（）()]", " ", s)
    s = re.sub(r"(请准时参加|准时参加|请准时|请参加|请提前\S{0,8}(到|抵达)|谢谢大家|谢谢配合|谢谢|辛苦了|拜托了)$", "", s)
    s = DEADLINE_AFTER_RE.sub("", s, count=1)
    s = re.sub(r"\s+", " ", s).strip(" ，,。.；;：:、-_—！!?？ ")
    if s and re.fullmatch(r"[\d\s月日号周年早下午晚中凌晨半点时:：\-—~至到（()）]*", s):
        return ""
    return s


def _is_task_clause(s):
    return bool(s) and (any(k in s for k in ACTION_KWS) or TASK_NOUN_RE.search(s))


def _pick_title(clauses, ci, own):
    """为时间子句选标题：优先自己的任务描述；交付/截止型优先回溯前文强任务子句；其次邻句。"""
    own = own or ""
    strong = re.compile(r"(改|修改|写|完成|准备|整理|审核|汇总|做|填写|修订|评审)")
    if DELIVERY_RE.match(own):
        for j in range(ci - 1, max(-1, ci - 4), -1):
            t = _clean_clause(clauses[j]["text"])
            if t and TASK_NOUN_RE.search(t) and strong.search(t):
                return t, True
    if own and _is_task_clause(own):
        return own, False
    if DELIVERY_RE.match(own):
        for j in range(ci - 1, max(-1, ci - 4), -1):
            t = _clean_clause(clauses[j]["text"])
            if _is_task_clause(t):
                return t, True
    for j in range(ci - 1, max(-1, ci - 4), -1):
        t = _clean_clause(clauses[j]["text"])
        if _is_task_clause(t):
            return t, True
    for j in range(ci + 1, min(len(clauses), ci + 3)):
        t = _clean_clause(clauses[j]["text"])
        if _is_task_clause(t):
            return t, True
    return (own or None), False


def parse_schedule_from_text(text, now=None):
    now = now or datetime.now(TZ)
    clauses = _split_clauses(text)
    if not clauses:
        return []
    raw = _events_from_clauses(clauses, now)

    events, seen = [], set()
    for r in raw:
        ci = r["clause_idx"]
        c = clauses[ci]
        own_text = c["text"]
        spans = sorted([d[:2] for d in c["dates"]] + [t[:2] for t in c["times"]])
        for s0, s1 in reversed(spans):
            own_text = own_text[:s0] + " " + own_text[s1:]
        own = _clean_clause(own_text)

        ctx_ok = _is_task_clause(own) or any(
            _is_task_clause(_clean_clause(clauses[cj]["text"]))
            for cj in (ci - 1, ci + 1) if 0 <= cj < len(clauses))
        if r["score"] < 0.8 and not ctx_ok:
            continue  # 弱信号且无任务语境 → 闲聊，跳过

        title, from_back = _pick_title(clauses, ci, own)
        if not title:
            title = "日程"
        if r["deadline"] and not title.startswith("截止"):
            title = f"截止：{title}"
        title = title[:40]
        key = (title, r["start"].isoformat())
        if key in seen:
            continue
        seen.add(key)

        location = None
        for j in range(max(0, ci - 1), min(len(clauses), ci + 3)):
            m = LOC_RE.search(clauses[j]["text"])
            if m:
                loc = (m.group(1) or m.group(2) or "").strip(" ，,。.；;：:在")
                if loc and loc != "地点":
                    location = loc[:40]
                    break

        notes = []
        if from_back:
            notes.append("语义关联：任务取自前文")
        if r["deadline"]:
            notes.append("截止型日程（由“…前”推断）")
        events.append({
            "title": title,
            "start": r["start"],
            "end": r["end"],
            "is_all_day": r["is_all_day"],
            "location": location,
            "notes": "；".join(notes) or None,
            "confidence": r["score"],
            "quote": c["text"][:60],
        })
    return events


FM_BIN = BASE / "fm_extract"


def extract_events_apple_from_text(text, now=None):
    """端侧模型（Apple Foundation Models）语义抽取：输入 OCR 文本，输出事件列表。"""
    now = now or datetime.now(TZ)
    weekdays = "一二三四五六日"
    now_text = now.strftime(f"%Y-%m-%d %H:%M 星期{weekdays[now.weekday()]}")
    payload = json.dumps({"text": text, "now": now_text}, ensure_ascii=False)
    out = subprocess.run([str(FM_BIN)], input=payload.encode("utf-8"),
                         capture_output=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(f"端侧模型退出码 {out.returncode}: {out.stderr.decode('utf-8', 'ignore')[:200]}")
    raw = out.stdout.decode("utf-8", "ignore")
    i, j = raw.find("{"), raw.rfind("}")
    if i == -1 or j <= i:
        raise RuntimeError(f"端侧模型无 JSON 输出：{raw[:120]}")
    data = json.loads(raw[i:j + 1])
    if "error" in data:
        raise RuntimeError(f"端侧模型：{data['error']}")
    events = []
    for e in data.get("events", []):
        try:
            start = datetime(int(e["year"]), int(e["month"]), int(e["day"]),
                             int(e.get("hour", 0)), int(e.get("minute", 0)), tzinfo=TZ)
            events.append({
                "title": str(e.get("title") or "未命名日程").strip()[:40],
                "start": start,
                "end": None,
                "is_all_day": bool(e.get("isAllDay")),
                "location": (e.get("location") or "").strip() or None,
                "notes": "端侧模型语义抽取",
                "confidence": float(e.get("confidence", 0.8)),
                "quote": text[:60],
            })
        except Exception as ex:
            log(f"丢弃端侧模型事件：{ex}")
    return events


def extract_events_local(png_bytes, cfg):
    text = vision_ocr(png_bytes)
    log("OCR 文本：" + text.replace("\n", " ⏎ ")[:300])
    mode = cfg.get("extractor", "auto")
    if mode in ("auto", "apple") and FM_BIN.exists():
        try:
            events = extract_events_apple_from_text(text)
            log(f"端侧模型抽取：{len(events)} 条日程")
            return events
        except Exception as e:
            log(f"端侧模型不可用，回退规则解析：{e}")
    return parse_schedule_from_text(text)


def extract_events(png_bytes, cfg):
    mode = cfg.get("extractor", "auto")
    if mode == "llm":
        if LLM_OK:
            try:
                return extract_events_llm(png_bytes, cfg)
            except Exception as e:
                log(f"LLM 抽取失败，本张回退规则解析：{e}")
        return extract_events_local(png_bytes, cfg)
    return extract_events_local(png_bytes, cfg)


# ---------------- 写入日历（EventKit） ----------------

import EventKit  # noqa: E402
from Foundation import NSDate, NSAutoreleasePool, NSObject  # noqa: E402

_STORE = None


def get_store():
    global _STORE
    if _STORE is None:
        _STORE = EventKit.EKEventStore.alloc().init()
    return _STORE


def request_calendar_access(store, wait_seconds=180):
    st = EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeEvent)
    if st == 2:
        raise PermissionError("日历访问被拒绝。请到 系统设置 → 隐私与安全性 → 日历 中授权")
    if st in (3, 4):  # authorized / fullAccess
        return True
    done = threading.Event()
    result = {"granted": False}

    def cb(granted, error=None):
        result["granted"] = bool(granted)
        done.set()

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(cb)
    else:
        store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, cb)
    if not done.wait(wait_seconds):
        raise TimeoutError("等待日历授权超时（授权弹窗可能没有处理）")
    return result["granted"]


def get_or_create_calendar(store, name):
    for cal in store.calendarsForEntityType_(EventKit.EKEntityTypeEvent):
        if str(cal.title()) == name:
            return cal
    default = store.defaultCalendarForNewEvents()
    src = default.source() if default else None
    if src is None:
        for s in store.sources():
            src = s
            break
    if src is None:
        raise RuntimeError("找不到任何日历账户来源")
    cal = EventKit.EKCalendar.calendarForEntityType_eventStore_(EventKit.EKEntityTypeEvent, store)
    cal.setTitle_(name)
    cal.setSource_(src)
    ok, err = store.saveCalendar_commit_error_(cal, True, None)
    if not ok:
        raise RuntimeError(f"创建日历「{name}」失败：{err}")
    log(f"已创建日历「{name}」")
    return cal


# ---------------- AppleScript 后备通道（Calendar.app） ----------------

def _as_escaped(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _as_date_expr(dt):
    return (f"mkdate({dt.year}, {dt.month}, {dt.day}, {dt.hour}, {dt.minute})")


def add_event_applescript(ev, cfg):
    import subprocess
    cal = _as_escaped(cfg["calendar_name"])
    title = _as_escaped(ev["title"])
    s, e = ev["start"], ev["end"]
    if ev["is_all_day"]:
        s = s.replace(hour=0, minute=0, second=0)
        e = (e or s + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        if e <= s:
            e = s + timedelta(days=1)
    elif e is None:
        e = s + timedelta(hours=1)
    notes = "来自截图识别（SnapCalendar）"
    if ev.get("quote"):
        notes += f"\\n原文：{ev['quote']}"
    if ev.get("notes"):
        notes += f"\\n备注：{ev['notes']}"
    notes = _as_escaped(notes)
    loc = _as_escaped(ev["location"]) if ev.get("location") else ""
    all_day = "true" if ev["is_all_day"] else "false"
    alarm = ""
    if not ev["is_all_day"] and cfg.get("alarm_minutes"):
        alarm = (f"make new sound alarm at end of sound alarms of nev "
                 f"with properties {{trigger interval:{-int(cfg['alarm_minutes'])}}}")
    script = f'''
on mkdate(y, mo, d, h, mi)
    set dt to current date
    set day of dt to 1
    set year of dt to y
    set month of dt to mo
    set day of dt to d
    set hours of dt to h
    set minutes of dt to mi
    set seconds of dt to 0
    return dt
end mkdate

tell application "Calendar"
    if (count of (calendars whose name = "{cal}")) is 0 then
        make new calendar with properties {{name:"{cal}"}}
    end if
    set c to first calendar whose name = "{cal}"
    set sd to {_as_date_expr(s)}
    set ed to {_as_date_expr(e)}
    set nev to make new event at end of events of c with properties {{summary:"{title}", start date:sd, end date:ed, allday event:{all_day}, description:"{notes}"}}
    if "{loc}" is not "" then set location of nev to "{loc}"
    {alarm}
    return uid of nev
end tell
'''
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"AppleScript 写入失败：{out.stderr.strip()[:200]}")
    return out.stdout.strip()


def applescript_probe(timeout_s=180):
    import subprocess
    out = subprocess.run(
        ["osascript", "-e", 'tell application "Calendar" to get name of calendars'],
        capture_output=True, text=True, timeout=timeout_s)
    return out.returncode == 0


CAL_MODE = None  # 'eventkit' | 'applescript'


def add_event(ev, cfg):
    if CAL_MODE == "applescript":
        return add_event_applescript(ev, cfg)
    return add_event_eventkit(ev, cfg)


def add_event_eventkit(ev, cfg):
    store = get_store()
    cal = get_or_create_calendar(store, cfg["calendar_name"])
    ek = EventKit.EKEvent.eventWithEventStore_(store)
    ek.setTitle_(ev["title"])
    ek.setCalendar_(cal)
    s, e = ev["start"], ev["end"]
    if ev["is_all_day"]:
        s = s.replace(hour=0, minute=0, second=0, microsecond=0)
        e = (e or s + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if e <= s:
            e = s + timedelta(days=1)
        ek.setAllDay_(True)
    else:
        if e is None:
            e = s + timedelta(hours=1)
    ek.setStartDate_(NSDate.dateWithTimeIntervalSince1970_(s.timestamp()))
    ek.setEndDate_(NSDate.dateWithTimeIntervalSince1970_(e.timestamp()))
    if ev.get("location"):
        ek.setLocation_(ev["location"])
    notes = "来自截图识别（SnapCalendar）"
    if ev.get("quote"):
        notes += f"\n原文：{ev['quote']}"
    if ev.get("notes"):
        notes += f"\n备注：{ev['notes']}"
    ek.setNotes_(notes)
    if not ev["is_all_day"] and cfg.get("alarm_minutes"):
        ek.addAlarm_(EventKit.EKAlarm.alarmWithRelativeOffset_(-int(cfg["alarm_minutes"]) * 60))
    ok, err = store.saveEvent_span_error_(ek, EventKit.EKSpanThisEvent, None)
    if not ok:
        raise RuntimeError(f"写入日历失败：{err}")
    return str(ek.eventIdentifier())


def list_events(cfg, days_ahead=7):
    """列出独立日历未来 N 天的事件（测试/核对用）。"""
    store = get_store()
    cal = get_or_create_calendar(store, cfg["calendar_name"])
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days_ahead)
    pred = store.predicateForEventsWithStartDate_endDate_calendars_(
        NSDate.dateWithTimeIntervalSince1970_(start.timestamp()),
        NSDate.dateWithTimeIntervalSince1970_(end.timestamp()),
        {cal},
    )
    return list(store.eventsMatchingPredicate_(pred) or [])


def delete_event_by_id(identifier):
    store = get_store()
    ek = store.eventWithIdentifier_(identifier)
    if ek is None:
        return False
    ok, err = store.removeEvent_span_error_(ek, EventKit.EKSpanThisEvent, None)
    if not ok:
        raise RuntimeError(f"删除事件失败：{err}")
    return True


# ---------------- 主流程 ----------------

def handle_image(png, cfg, state):
    h = hashlib.sha256(png).hexdigest()
    if h in state["seen_images"]:
        log("重复截图，跳过")
        return
    log(f"捕获新截图（{len(png) // 1024} KB），正在识别…")
    try:
        events = extract_events(png, cfg)
    except Exception as e:
        append_jsonl({"ts": datetime.now(TZ).isoformat(), "image_hash": h,
                      "status": "error", "error": str(e)})
        raise
    append_jsonl({
        "ts": datetime.now(TZ).isoformat(),
        "image_hash": h,
        "status": "ok",
        "events": [
            {"title": e["title"], "start": e["start"].isoformat(),
             "end": e["end"].isoformat() if e["end"] else None,
             "is_all_day": e["is_all_day"], "confidence": e["confidence"],
             "quote": e["quote"]}
            for e in events
        ],
    })
    state["seen_images"].append(h)
    state["seen_images"] = state["seen_images"][-500:]
    save_state(state)

    if not events:
        log("未识别到日程安排")
        notify("SnapCalendar", "截图里没有识别到日程安排")
        return

    added, skipped = [], []
    for ev in events:
        key = f"{ev['title']}|{ev['start'].isoformat()}"
        if ev["confidence"] < cfg.get("confidence_min", 0.5):
            skipped.append(f"{ev['title']}（置信度低 {ev['confidence']:.1f}）")
            continue
        if key in state["created_keys"]:
            skipped.append(f"{ev['title']}（已添加过）")
            continue
        try:
            add_event(ev, cfg)
            state["created_keys"].append(key)
            state["created_keys"] = state["created_keys"][-500:]
            save_state(state)
            added.append(ev)
            when = "全天 " + f"{ev['start']:%m月%d日}" if ev["is_all_day"] else f"{ev['start']:%m月%d日 %H:%M}"
            log(f"已写入日历：{ev['title']} @ {when}")
        except Exception as e:
            log(f"[错误] 写入失败 {ev['title']}：{e}")
            skipped.append(f"{ev['title']}（写入失败）")

    if added:
        parts = []
        for e in added:
            when = "全天 " + f"{e['start']:%m月%d日}" if e["is_all_day"] else f"{e['start']:%m月%d日 %H:%M}"
            parts.append(f"{e['title']} {when}")
        notify("SnapCalendar 已添加日程", "；".join(parts))
    else:
        notify("SnapCalendar", "识别到日程但未写入：" + "；".join(skipped) if skipped else "没有可添加的日程")
    if skipped:
        log(f"跳过：{'；'.join(skipped)}")


def decide_calendar_mode():
    """先试 EventKit 授权（可能不弹窗），失败则退到 AppleScript（会弹「控制日历」授权）。"""
    global CAL_MODE
    try:
        store = get_store()
        if request_calendar_access(store, wait_seconds=90):
            CAL_MODE = "eventkit"
            log("日历通道：EventKit ✓")
            return CAL_MODE
        log("EventKit 未获授权，改试 AppleScript 通道…")
    except Exception as e:
        log(f"EventKit 模式不可用（{e}），改试 AppleScript 通道…")
    try:
        if applescript_probe(timeout_s=180):
            CAL_MODE = "applescript"
            log("日历通道：AppleScript ✓")
            return CAL_MODE
    except Exception as e:
        log(f"AppleScript 通道也不可用：{e}")
    return None


def run_selftest(cfg):
    now = datetime.now(TZ)
    ev = {
        "title": "SnapCalendar 测试事件（自动清理）",
        "start": now + timedelta(minutes=10),
        "end": now + timedelta(minutes=70),
        "is_all_day": False,
        "location": "测试会议室",
        "notes": None,
        "confidence": 1.0,
        "quote": "单元测试",
    }
    ident = add_event(ev, cfg)
    log(f"自测：已创建测试事件 {ident} ✓")
    if CAL_MODE == "eventkit":
        titles = [str(e.title()) for e in list_events(cfg)]
        if "SnapCalendar 测试事件（自动清理）" not in titles:
            log("自测：查询不到刚建的事件 ✗")
            return False
        delete_event_by_id(ident)
    else:
        import subprocess
        cal_name = _as_escaped(cfg["calendar_name"])
        script = (f'tell application "Calendar" to delete '
                  f'(first event of (first calendar whose name = "{cal_name}") '
                  f'whose uid = "{ident}")')
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            log(f"自测：清理测试事件失败（不影响使用）：{out.stderr.strip()[:120]}")
    log("自测：写入/查询/清理 全部通过 ✓")
    return True


# ---------------- 全局快捷键（Quartz event tap，≤3 键可配） ----------------

MOD_FLAGS = {"option": 0x00080000, "alt": 0x00080000,
             "cmd": 0x00100000, "command": 0x00100000,
             "ctrl": 0x00040000, "control": 0x00040000,
             "shift": 0x00020000}
MOD_GLYPH = {"option": "⌥", "alt": "⌥", "cmd": "⌘", "command": "⌘",
             "ctrl": "⌃", "control": "⌃", "shift": "⇧"}
KEYCODES = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
            "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
            "y": 16, "t": 17, "u": 32, "i": 34, "o": 31, "p": 35, "l": 37,
            "j": 38, "k": 40, "n": 45, "m": 46}

_HOTKEY_TAP = None
_HOTKEY_DESC = ""


def parse_hotkey(spec):
    """'option+s' → (keycode, 修饰键掩码, 显示文案)；非法返回 None。"""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if len(parts) < 2:
        return None
    key, mods = parts[-1], parts[:-1]
    if key not in KEYCODES or any(m not in MOD_FLAGS for m in mods):
        return None
    mask = 0
    for m in mods:
        mask |= MOD_FLAGS[m]
    desc = "".join(MOD_GLYPH[m] for m in mods) + key.upper()
    return KEYCODES[key], mask, desc


def setup_hotkey(cfg, prompt=True):
    """注册全局快捷键：按下 → 调起原生区域截图进剪贴板。需要辅助功能授权。"""
    global _HOTKEY_TAP, _HOTKEY_DESC
    if _HOTKEY_TAP is not None:
        return True
    parsed = parse_hotkey(cfg.get("hotkey", "option+s"))
    if parsed is None:
        log(f"快捷键配置无效：{cfg.get('hotkey')!r}（格式如 option+s / cmd+shift+a）")
        return False
    keycode, mask, desc = parsed

    from Quartz import (CGEventTapCreate, CGEventMaskBit, kCGEventKeyDown,
                        kCGSessionEventTap, kCGHeadInsertEventTap,
                        kCGEventTapOptionDefault, CFMachPortCreateRunLoopSource,
                        CFRunLoopGetCurrent, CFRunLoopAddSource, kCFRunLoopCommonModes,
                        CGEventGetIntegerValueField, kCGKeyboardEventKeycode,
                        CGEventGetFlags, CGEventTapEnable)

    def cb(proxy, etype, event, refcon):
        try:
            if CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode) == keycode \
                    and (CGEventGetFlags(event) & 0xFF0000) == mask:
                subprocess.Popen(["/usr/sbin/screencapture", "-i", "-c"])
                return None  # 吞掉按键
        except Exception as e:
            log(f"快捷键触发异常：{e}")
        return event

    tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                           kCGEventTapOptionDefault,
                           CGEventMaskBit(kCGEventKeyDown), cb, None)
    if tap is None:
        if prompt:
            try:
                from ApplicationServices import (AXIsProcessTrustedWithOptions,
                                                 kAXTrustedCheckOptionPrompt)
                AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
                log("快捷键需要「辅助功能」授权：请在弹出的设置里打开「终端」开关")
            except Exception as e:
                log(f"快捷键注册失败：{e}")
        return False
    src = CFMachPortCreateRunLoopSource(None, tap, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), src, kCFRunLoopCommonModes)
    CGEventTapEnable(tap, True)
    _HOTKEY_TAP = tap
    _HOTKEY_DESC = desc
    log(f"全局快捷键 {desc} 已注册：按下即框选截图进剪贴板")
    notify("SnapCalendar", f"快捷键 {desc} 已启用")
    return True


class Poller(NSObject):
    """定时轮询剪贴板 + 重试快捷键注册（事件循环托管给 NSApplication）。"""

    def initWithState_(self, cfg_state):
        self = objc.super(Poller, self).init()
        if self is None:
            return None
        self.cfg, self.state = cfg_state
        from AppKit import NSPasteboard
        self.pb = NSPasteboard.generalPasteboard()
        self.last_count = self.pb.changeCount()
        self.hotkey_pending = False
        self.hotkey_retry = 0
        return self

    def poll_(self, timer):
        try:
            if self.hotkey_pending:
                self.hotkey_retry += 1
                if setup_hotkey(self.cfg, prompt=False):
                    self.hotkey_pending = False
                elif self.hotkey_retry % 30 == 0:
                    log("快捷键仍在等待「辅助功能」授权：系统设置 → 隐私与安全性 → 辅助功能 → 打开「终端」")
            count = self.pb.changeCount()
            if count != self.last_count:
                self.last_count = count
                png = read_clipboard_png(self.pb)
                if png:
                    handle_image(png, self.cfg, self.state)
        except KeyboardInterrupt:
            log("手动退出")
            NSApplication.sharedApplication().terminate_(None)
        except Exception as e:
            log(f"[错误] {e}")
            notify("SnapCalendar", f"处理失败：{e}")


def main():
    cfg = load_config()

    # 单实例锁：避免终端实例与开机自启实例双开
    STATE_DIR.mkdir(exist_ok=True)
    lock_fp = open(STATE_DIR / "lock", "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("已有 SnapCalendar 实例在运行，本实例退出")
        sys.exit(0)

    state = load_state()

    # 授权探测：EventKit 优先，失败退 AppleScript
    if decide_calendar_mode() is None:
        log("两种日历通道都未获授权，退出。请重新运行本程序并在弹窗中点「允许」")
        notify("SnapCalendar", "未获得日历授权，已退出；重新运行可重试")
        sys.exit(1)

    if "--selftest" in sys.argv:
        if not run_selftest(cfg):
            sys.exit(1)
        notify("SnapCalendar", "日历授权与写入自测通过，监控启动")

    # LLM 模式：启动时解析凭据并探活，失败自动回退规则引擎
    global LLM_OK
    if cfg.get("extractor") == "llm":
        if decide_llm_config(cfg):
            try:
                ping_llm()
                LLM_OK = True
                log(f"LLM 通道就绪：{LLM_BASE} · {LLM_MODELS[0]} · key…{LLM_KEY[-4:]}")
            except Exception as e:
                log(f"LLM 通道探测失败，本次运行使用规则引擎：{e}")
        else:
            log("未找到可用的 LLM key（config.api_key 或 api_key_file），使用规则引擎")

    log(f"SnapCalendar 已启动：监控剪贴板 → 日历「{cfg['calendar_name']}」")
    notify("SnapCalendar", "已启动：⌥S 截图即用（原生 Ctrl+Cmd+Shift+4 也可以）")

    # 事件循环：剪贴板轮询定时器 + 全局快捷键事件源都挂在主 runloop 上
    from AppKit import NSApplication, NSTimer
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory：不占 Dock
    poller = Poller.alloc().initWithState_((cfg, state))
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        float(cfg.get("poll_interval", 2)), poller, "poll:", None, True)

    if not setup_hotkey(cfg):
        poller.hotkey_pending = True
        log("快捷键暂未生效：等待辅助功能授权后自动注册（原生 Ctrl+Cmd+Shift+4 仍可用）")

    app.run()


if __name__ == "__main__":
    main()
