#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4人團隊區網免安裝專案管理伺服器 v11 (零外部套件、純標準庫、零 exe)
功能：
  - 模式 A：GitHub 私有倉庫自動雙向同步（本地修改自動推送至 GitHub，GitHub Pages 雲端修改自動拉回本地並備份，零外部套件）
  - 甘特圖五段時間尺度縮放：日（小時）／週（每日）／月（每週）／季（3個月一季）／年（每月）
  - 休假日管理與任務截斷功能（支援農曆過年等國定連假標示、甘特圖跨假自動視覺截斷、順延補回、實體拆分）
  - 啟動觀看密碼鎖定保護、任務全面依開始時間排序、依任務分色時主任務與子任務自動同色系
  - 任務時間精準至小時與分鐘、子任務展開收合、內網共用資料夾直開、四級權限、內建聚光燈教學
執行方式:
  python pm_server-v11.py [埠號，預設 8765] [--reset-admin] [--reset-view-pass] [--shared "D:\共用資料夾"]
"""

import base64
import datetime
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import socket
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import urllib.error
import urllib.request
from urllib.parse import parse_qs, quote, urlparse

DEFAULT_PORT = 8765
DEFAULT_VIEW_PASS = "8888"
RESET_ADMIN_FLAG = "--reset-admin" in sys.argv
RESET_VIEW_PASS_FLAG = "--reset-view-pass" in sys.argv
PORT_ARGS = [a for a in sys.argv[1:] if a.isdigit()]
REQUESTED_PORT = int(PORT_ARGS[0]) if PORT_ARGS else DEFAULT_PORT

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "project_data.json")
GITHUB_SYNC_FILE = os.path.join(BASE_DIR, "github_sync.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
DEFAULT_SHARED_DIR = os.path.join(BASE_DIR, "shared_files")
GITHUB_API_BASE = os.environ.get("PM_GITHUB_API_BASE", "https://api.github.com").rstrip("/")
DATA_LOCK = threading.Lock()
SYNC_LOCK = threading.Lock()
SYNC_WAKE_EVENT = threading.Event()
SERVER_BOOT_ID = uuid.uuid4().hex[:8]
CLOUD_SYNC_STATE = {
    "enabled": False,
    "status": "disabled",  # disabled | idle | syncing | synced | error
    "lastSync": "",
    "lastError": "",
    "repo": "",
    "branch": "main",
    "filePath": "project_data.json",
    "intervalSec": 10,
    "remoteSha": "",
    "remoteEtag": "",
    "lastSyncedVersion": 0,
    "tick": 0,
}
SESSIONS = {}  # token -> {"memberId": str}
VIEW_SESSIONS = set()  # unlocked viewing tokens
VALID_ROLES = ("sysadmin", "pm", "member", "viewer")

CLI_SHARED_PATH = ""
for _i, _arg in enumerate(sys.argv[1:], start=1):
    if _arg == "--shared" and _i + 1 < len(sys.argv):
        CLI_SHARED_PATH = sys.argv[_i + 1].strip()
    elif _arg.startswith("--shared="):
        CLI_SHARED_PATH = _arg.split("=", 1)[1].strip()

PREVIEWABLE_EXTS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
    ".txt", ".md", ".csv", ".json", ".log", ".xml", ".yaml", ".yml",
    ".mp4", ".webm", ".mp3", ".wav", ".ogg",
}
MIME_OVERRIDES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".yaml": "text/plain; charset=utf-8",
    ".yml": "text/plain; charset=utf-8",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
}
SKIP_SYSTEM_FILES = {"desktop.ini", "thumbs.db", ".ds_store"}

DEFAULT_HOLIDAYS_PRESET = [
    {"id": "h_2026_1225", "name": "2026 行憲紀念日連假", "start": "2026-12-25", "end": "2026-12-27"},
    {"id": "h_2027_0101", "name": "2027 元旦連假", "start": "2027-01-01", "end": "2027-01-03"},
    {"id": "h_2027_cny",  "name": "2027 農曆春節連假", "start": "2027-02-04", "end": "2027-02-10"},
    {"id": "h_2027_0228", "name": "2027 和平紀念日連假", "start": "2027-02-27", "end": "2027-03-01"},
    {"id": "h_2027_0404", "name": "2027 兒童節與清明節連假", "start": "2027-04-03", "end": "2027-04-06"},
    {"id": "h_2027_0501", "name": "2027 勞動節連假", "start": "2027-04-30", "end": "2027-05-02"},
    {"id": "h_2027_0609", "name": "2027 端午節", "start": "2027-06-09", "end": "2027-06-09"},
    {"id": "h_2027_0915", "name": "2027 中秋節", "start": "2027-09-15", "end": "2027-09-15"},
    {"id": "h_2027_0928", "name": "2027 教師節", "start": "2027-09-28", "end": "2027-09-28"},
    {"id": "h_2027_1010", "name": "2027 國慶日連假", "start": "2027-10-09", "end": "2027-10-11"},
    {"id": "h_2027_1025", "name": "2027 臺灣光復節連假", "start": "2027-10-23", "end": "2027-10-25"},
    {"id": "h_2027_1225", "name": "2027 行憲紀念日連假", "start": "2027-12-24", "end": "2027-12-26"},
    {"id": "h_2028_0101", "name": "2028 元旦連假", "start": "2027-12-31", "end": "2028-01-02"},
]


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def disable_windows_quick_edit():
    """在 Windows 終端機自動停用「快速編輯模式」，避免誤點小黑窗導致伺服器凍結"""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE = 0x0040
            ENABLE_EXTENDED_FLAGS = 0x0080
            new_mode = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
            kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


def is_valid_iso_date(s):
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(s or "").strip()))


def is_valid_hex_color(s):
    return bool(re.match(r"^#[0-9a-fA-F]{6}$", str(s or "").strip()))


def normalize_dt(val, default_time="09:00", allow_empty=False):
    """將日期時間正規化為 YYYY-MM-DDTHH:MM，向下相容舊版 YYYY-MM-DD"""
    s = str(val or "").strip()
    if not s:
        if allow_empty:
            return ""
        return f"{datetime.date.today().isoformat()}T{default_time}"
    s = s.replace(" ", "T")
    m_dt = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?$", s)
    if m_dt:
        try:
            datetime.datetime.strptime(f"{m_dt.group(1)}T{m_dt.group(2)}", "%Y-%m-%dT%H:%M")
            return f"{m_dt.group(1)}T{m_dt.group(2)}"
        except ValueError:
            pass
    if is_valid_iso_date(s):
        try:
            datetime.date.fromisoformat(s)
            return f"{s}T{default_time}"
        except ValueError:
            pass
    if allow_empty:
        return ""
    return f"{datetime.date.today().isoformat()}T{default_time}"


def hash_pin(member_id, pin):
    pin_str = str(pin or "").strip()
    if not pin_str:
        return ""
    raw = f"pm_lan_v3:{member_id}:{pin_str}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_pin(member, pin):
    stored = member.get("pinHash") or ""
    if not stored:
        return True
    candidate = hash_pin(member["id"], pin)
    return hmac.compare_digest(stored, candidate)


def hash_view_pass(pw):
    pw_str = str(pw or "").strip()
    if not pw_str:
        return ""
    raw = f"pm_lan_view_v8:{pw_str}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_view_pass(data, pw):
    stored = str(data.get("viewPassHash") or "")
    if not stored:
        return True
    candidate = hash_view_pass(pw)
    return hmac.compare_digest(stored, candidate)


def default_data():
    today = datetime.date.today()

    def dt(offset, hhmm="09:00"):
        return f"{(today + datetime.timedelta(days=offset)).isoformat()}T{hhmm}"

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "schemaVersion": 9,
        "version": 1,
        "strictMemberEdit": False,
        "viewPassEnabled": True,
        "viewPassHash": hash_view_pass(DEFAULT_VIEW_PASS),
        "holidays": [dict(h) for h in DEFAULT_HOLIDAYS_PRESET],
        "sharedRoots": [
            {
                "id": "r_default",
                "label": "本機共用資料夾",
                "path": CLI_SHARED_PATH or DEFAULT_SHARED_DIR,
            }
        ],
        "members": [
            {
                "id": "u1",
                "name": "成員A",
                "role": "sysadmin",
                "pinHash": hash_pin("u1", "1234"),
                "hasPin": True,
            },
            {"id": "u2", "name": "成員B", "role": "pm", "pinHash": "", "hasPin": False},
            {"id": "u3", "name": "成員C", "role": "member", "pinHash": "", "hasPin": False},
            {"id": "u4", "name": "成員D", "role": "viewer", "pinHash": "", "hasPin": False},
        ],
        "projects": [
            {
                "id": "p_main",
                "name": "專案 A：主開發排程",
                "managerIds": [],
                "tasks": [
                    {
                        "id": "t1",
                        "title": "需求確認與規格定案",
                        "assignee": "成員A",
                        "status": "done",
                        "start": dt(-3, "09:00"),
                        "end": dt(0, "18:00"),
                        "actualEnd": dt(-1, "15:30"),
                        "progress": 100,
                        "priority": "high",
                        "dep": "",
                        "parentId": "",
                        "color": "#2563eb",
                        "isMilestone": False,
                        "fileLink": "share://r_default/",
                        "notes": "比預期提前 1 天 2.5 小時完成！點左側 📂 可直接於瀏覽器開啟共用資料夾。",
                    },
                    {
                        "id": "t2",
                        "title": "版面規劃與初稿製作",
                        "assignee": "成員B",
                        "status": "doing",
                        "start": dt(0, "09:30"),
                        "end": dt(5, "17:30"),
                        "actualEnd": "",
                        "progress": 60,
                        "priority": "high",
                        "dep": "t1",
                        "parentId": "",
                        "color": "#7c3aed",
                        "isMilestone": False,
                        "fileLink": "",
                        "notes": "點左側 ▾ 可展開或收合下方子任務；縮放切到「日」可精細調整小時與分鐘。",
                    },
                    {
                        "id": "t2_1",
                        "title": "收集參考資料與線框規劃",
                        "assignee": "成員B",
                        "status": "done",
                        "start": dt(0, "09:30"),
                        "end": dt(1, "16:00"),
                        "actualEnd": dt(2, "10:30"),
                        "progress": 100,
                        "priority": "medium",
                        "dep": "",
                        "parentId": "t2",
                        "color": "",
                        "isMilestone": False,
                        "fileLink": "",
                        "notes": "實際完成時間比預期晚 18.5 小時，可在甘特圖與「📊 進度分析」查看差異。",
                    },
                    {
                        "id": "t2_2",
                        "title": "視覺初稿與圖面細修",
                        "assignee": "成員C",
                        "status": "doing",
                        "start": dt(2, "09:00"),
                        "end": dt(5, "17:30"),
                        "actualEnd": "",
                        "progress": 40,
                        "priority": "high",
                        "dep": "t2_1",
                        "parentId": "t2",
                        "color": "",
                        "isMilestone": False,
                        "fileLink": "",
                        "notes": "完成後更改狀態為「已完成」會自動帶入目前日期與時間為實際完成時間。",
                    },
                    {
                        "id": "t3",
                        "title": "客戶初稿審核里程碑",
                        "assignee": "成員B",
                        "status": "todo",
                        "start": dt(6, "14:00"),
                        "end": dt(6, "14:00"),
                        "actualEnd": "",
                        "progress": 0,
                        "priority": "high",
                        "dep": "t2",
                        "parentId": "",
                        "color": "#db2777",
                        "isMilestone": True,
                        "fileLink": "",
                        "notes": "下午 14:00 關鍵檢核點（甘特圖顯示為菱形標記）。",
                    },
                    {
                        "id": "t4",
                        "title": "修正與正式交付",
                        "assignee": "成員C",
                        "status": "todo",
                        "start": dt(7, "09:00"),
                        "end": dt(10, "18:00"),
                        "actualEnd": "",
                        "progress": 0,
                        "priority": "medium",
                        "dep": "t3",
                        "parentId": "",
                        "color": "#059669",
                        "isMilestone": False,
                        "fileLink": "",
                        "notes": "",
                    },
                ],
            },
            {
                "id": "p_sub",
                "name": "專案 B：常規維運",
                "managerIds": [],
                "tasks": [
                    {
                        "id": "t_b1",
                        "title": "每週進度盤點與文件歸檔",
                        "assignee": "成員C",
                        "status": "doing",
                        "start": dt(0, "10:00"),
                        "end": dt(3, "17:00"),
                        "actualEnd": "",
                        "progress": 30,
                        "priority": "medium",
                        "dep": "",
                        "parentId": "",
                        "color": "#4f46e5",
                        "isMilestone": False,
                        "fileLink": "",
                        "notes": "左上角下拉選單可隨時新增或切換不同專案。",
                    }
                ],
            },
        ],
        "logs": [
            {
                "time": now_str,
                "user": "系統",
                "projectId": "p_main",
                "projectName": "專案 A：主開發排程",
                "text": "建立多專案排程與四級權限初始資料（系統管理員預設 PIN：1234）",
            }
        ],
    }


def normalize_task(task, default_member=""):
    is_ms = bool(task.get("isMilestone", False))
    start = normalize_dt(task.get("start"), default_time="09:00", allow_empty=False)
    end = start if is_ms else normalize_dt(task.get("end") or start, default_time="18:00", allow_empty=False)
    if end < start:
        end = start
    tid = str(task.get("id") or ("t_" + uuid.uuid4().hex[:8]))
    dep = str(task.get("dep") or "")
    if dep == tid:
        dep = ""
    parent_id = str(task.get("parentId") or "").strip()
    if parent_id == tid:
        parent_id = ""
    actual_end = normalize_dt(task.get("actualEnd"), default_time="18:00", allow_empty=True)
    raw_color = str(task.get("color") or "").strip()
    color = raw_color.lower() if is_valid_hex_color(raw_color) else ""
    try:
        prog = max(0, min(100, int(task.get("progress", 0))))
    except Exception:
        prog = 0
    split_by_holidays = True if "splitByHolidays" not in task else bool(task.get("splitByHolidays"))
    return {
        "id": tid,
        "title": str(task.get("title") or "未命名任務").strip(),
        "assignee": str(task.get("assignee") or default_member),
        "status": str(task.get("status") or "todo"),
        "start": start,
        "end": end,
        "actualEnd": actual_end,
        "progress": prog,
        "priority": str(task.get("priority") or "medium"),
        "dep": dep,
        "parentId": parent_id,
        "color": color,
        "splitByHolidays": split_by_holidays,
        "isMilestone": is_ms,
        "fileLink": str(task.get("fileLink") or "").strip(),
        "notes": str(task.get("notes") or ""),
    }


def normalize_holidays(raw_holidays, use_default_if_missing=False):
    if raw_holidays is None and use_default_if_missing:
        return [dict(h) for h in DEFAULT_HOLIDAYS_PRESET]
    if not isinstance(raw_holidays, list):
        return []
    result = []
    seen_ids = set()
    for item in raw_holidays:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        s = str(item.get("start") or "").strip()[:10]
        e = str(item.get("end") or s).strip()[:10]
        if not (is_valid_iso_date(s) and is_valid_iso_date(e)):
            continue
        try:
            datetime.date.fromisoformat(s)
            datetime.date.fromisoformat(e)
        except ValueError:
            continue
        if e < s:
            e = s
        hid = str(item.get("id") or "").strip()
        if not hid or hid in seen_ids:
            hid = "h_" + uuid.uuid4().hex[:6]
        seen_ids.add(hid)
        result.append({
            "id": hid,
            "name": name or f"休假日 ({s})",
            "start": s,
            "end": e,
        })
    result.sort(key=lambda x: (x["start"], x["end"], x["name"]))
    return result


def compute_merged_holiday_intervals(holidays):
    intervals = []
    for h in (holidays or []):
        s = str(h.get("start") or "")[:10]
        e = str(h.get("end") or s)[:10]
        if not (is_valid_iso_date(s) and is_valid_iso_date(e)):
            continue
        try:
            d0 = datetime.datetime.strptime(s, "%Y-%m-%d")
            d1 = datetime.datetime.strptime(e, "%Y-%m-%d") + datetime.timedelta(days=1)
        except ValueError:
            continue
        if d1 > d0:
            intervals.append([d0, d1, h.get("name") or "休假日"])
    intervals.sort(key=lambda x: x[0])
    merged = []
    for s0, e0, nm in intervals:
        if merged and s0 <= merged[-1][1]:
            if e0 > merged[-1][1]:
                merged[-1][1] = e0
            if nm not in merged[-1][2]:
                merged[-1][2].append(nm)
        else:
            merged.append([s0, e0, [nm]])
    return merged


def split_task_interval_by_holidays(start_str, end_str, holidays):
    s_dt = datetime.datetime.strptime(normalize_dt(start_str, "09:00"), "%Y-%m-%dT%H:%M")
    e_dt = datetime.datetime.strptime(normalize_dt(end_str, "18:00"), "%Y-%m-%dT%H:%M")
    if e_dt <= s_dt:
        return [], []
    merged = compute_merged_holiday_intervals(holidays)
    segs = [[s_dt, e_dt, False, False]]  # [start, end, clipped_left, clipped_right]
    hit_names = []
    for h0, h1, names in merged:
        if h1 <= s_dt or h0 >= e_dt:
            continue
        for nm in names:
            if nm not in hit_names:
                hit_names.append(nm)
        next_segs = []
        for a, b, cl, cr in segs:
            if h1 <= a or h0 >= b:
                next_segs.append([a, b, cl, cr])
            else:
                if a < h0:
                    next_segs.append([a, h0, cl, True])
                if h1 < b:
                    next_segs.append([h1, b, True, cr])
        segs = next_segs
    # 調整被休假日切齊的 00:00 邊界為友善上班時段（休假前一日 18:00／收假隔日 09:00）
    formatted = []
    for a, b, cl, cr in segs:
        adj_a = a
        adj_b = b
        if cl and adj_a.hour == 0 and adj_a.minute == 0:
            cand_a = adj_a.replace(hour=9, minute=0)
            if cand_a < adj_b:
                adj_a = cand_a
        if cr and adj_b.hour == 0 and adj_b.minute == 0:
            prev_day = adj_b - datetime.timedelta(days=1)
            cand_b = prev_day.replace(hour=18, minute=0)
            if cand_b > adj_a:
                adj_b = cand_b
            else:
                adj_b = prev_day.replace(hour=23, minute=59)
        if adj_b > adj_a:
            formatted.append((adj_a.strftime("%Y-%m-%dT%H:%M"), adj_b.strftime("%Y-%m-%dT%H:%M"), (adj_b - adj_a).total_seconds()))
    return formatted, hit_names


def has_parent_cycle(tasks, task_id, new_parent_id):
    if not new_parent_id:
        return False
    if new_parent_id == task_id:
        return True
    by_id = {t["id"]: t for t in tasks}
    cur = new_parent_id
    visited = {task_id}
    while cur:
        if cur in visited:
            return True
        visited.add(cur)
        parent_obj = by_id.get(cur)
        if not parent_obj:
            return False
        cur = parent_obj.get("parentId") or ""
    return False


def sanitize_project_parents(tasks):
    valid_ids = {t["id"] for t in tasks}
    changed = False
    for t in tasks:
        pid = t.get("parentId") or ""
        if pid and (pid not in valid_ids or has_parent_cycle(tasks, t["id"], pid)):
            t["parentId"] = ""
            changed = True
    return changed


def normalize_role(role):
    if role == "admin":
        return "sysadmin"
    if role in VALID_ROLES:
        return role
    return "member"


def normalize_members(raw_members):
    """相容 v1/v2 純字串與 v3 admin 角色，自動升級為 v4 四級權限物件"""
    result = []
    seen_names = set()
    if not isinstance(raw_members, list) or not raw_members:
        raw_members = ["成員A", "成員B", "成員C", "成員D"]

    for idx, item in enumerate(raw_members):
        if isinstance(item, str):
            name = item.strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            uid = f"u{idx + 1}"
            is_first = len(result) == 0
            result.append({
                "id": uid,
                "name": name,
                "role": "sysadmin" if is_first else "member",
                "pinHash": hash_pin(uid, "1234") if is_first else "",
                "hasPin": is_first,
            })
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            uid = str(item.get("id") or ("u_" + uuid.uuid4().hex[:6]))
            role = normalize_role(item.get("role"))
            pin_hash = str(item.get("pinHash") or "")
            result.append({
                "id": uid,
                "name": name,
                "role": role,
                "pinHash": pin_hash,
                "hasPin": bool(pin_hash),
            })

    if not result:
        result = [
            {
                "id": "u1",
                "name": "系統管理員",
                "role": "sysadmin",
                "pinHash": hash_pin("u1", "1234"),
                "hasPin": True,
            }
        ]
    if not any(m["role"] == "sysadmin" for m in result):
        result[0]["role"] = "sysadmin"
        if not result[0]["pinHash"]:
            result[0]["pinHash"] = hash_pin(result[0]["id"], "1234")
            result[0]["hasPin"] = True
    return result


def ensure_default_shared_dir():
    try:
        os.makedirs(DEFAULT_SHARED_DIR, exist_ok=True)
        if not os.listdir(DEFAULT_SHARED_DIR):
            readme = os.path.join(DEFAULT_SHARED_DIR, "使用說明_請將共用檔案放這裡.txt")
            with open(readme, "w", encoding="utf-8") as f:
                f.write(
                    "這是預設的區網共用資料夾 (shared_files)。\n"
                    "1. 您可直接將專案檔案或子資料夾放進這裡，團隊 4 人即可在網頁上直接瀏覽、預覽與下載。\n"
                    "2. 系統管理員也可至網頁右上角「系統設定 → 📂 共用資料夾」新增其他本機磁碟或公司內網共用路徑（例如 D:\\專案資料 或 \\\\192.168.1.10\\Shared）。\n"
                )
    except Exception:
        pass


def normalize_shared_roots(raw_roots):
    result = []
    seen_ids = set()
    if isinstance(raw_roots, list):
        for item in raw_roots:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            rid = str(item.get("id") or "").strip()
            if not rid or rid in seen_ids:
                rid = "r_" + uuid.uuid4().hex[:6]
            seen_ids.add(rid)
            label = str(item.get("label") or "").strip() or os.path.basename(path.rstrip("\\/")) or path
            result.append({"id": rid, "label": label, "path": path})
    if not result:
        ensure_default_shared_dir()
        result = [{
            "id": "r_default",
            "label": "本機共用資料夾",
            "path": CLI_SHARED_PATH or DEFAULT_SHARED_DIR,
        }]
    return result


def inspect_dir_path(path_str):
    p = str(path_str or "").strip()
    if not p:
        return {"exists": False, "isDir": False, "readable": False, "itemCount": 0, "message": "路徑不可為空"}
    try:
        exists = os.path.exists(p)
        is_dir = os.path.isdir(p)
        if not exists:
            return {"exists": False, "isDir": False, "readable": False, "itemCount": 0, "message": "找不到此路徑（請確認磁碟機或內網分享已連線）"}
        if not is_dir:
            return {"exists": True, "isDir": False, "readable": False, "itemCount": 0, "message": "此路徑是檔案，請填入資料夾路徑"}
        entries = os.listdir(p)
        return {"exists": True, "isDir": True, "readable": True, "itemCount": len(entries), "message": f"可正常存取（含 {len(entries)} 個項目）"}
    except PermissionError:
        return {"exists": True, "isDir": True, "readable": False, "itemCount": 0, "message": "權限不足，伺服器電腦無法讀取此資料夾"}
    except Exception as e:
        return {"exists": False, "isDir": False, "readable": False, "itemCount": 0, "message": f"存取失敗：{e}"}


def resolve_shared_target(data, root_id, rel_path=""):
    roots = data.get("sharedRoots") or []
    root = next((r for r in roots if r["id"] == root_id), None)
    if not root:
        return None, None, None, "找不到指定的共用資料夾設定"
    root_path = str(root.get("path") or "").strip()
    if not root_path or not os.path.isdir(root_path):
        return root, None, None, f"共用資料夾「{root.get('label')}」目前無法存取，請確認路徑存在"

    raw_rel = str(rel_path or "").replace("\\", "/").strip("/")
    parts = []
    if raw_rel:
        for seg in raw_rel.split("/"):
            if not seg or seg == ".":
                continue
            if seg == ".." or "\x00" in seg or ":" in seg:
                return root, None, None, "不合法的路徑字元（禁止跨層存取）"
            parts.append(seg)

    try:
        base_real = os.path.realpath(root_path)
        target_real = os.path.realpath(os.path.join(base_real, *parts)) if parts else base_real
        if os.path.commonpath([os.path.normcase(base_real), os.path.normcase(target_real)]) != os.path.normcase(base_real):
            return root, None, None, "安全限制：禁止存取共用資料夾以外的路徑"
    except Exception:
        return root, None, None, "路徑解析失敗（禁止跨磁碟或無效路徑）"

    norm_rel = "/".join(parts)
    return root, norm_rel, target_real, None


def migrate_data_if_needed(data):
    """相容 v1/v2/v3/v4 舊檔，自動升級至 v5（含共用資料夾網頁直開）"""
    changed = False
    raw_members = data.get("members")
    norm_members = normalize_members(raw_members)
    if norm_members != raw_members:
        data["members"] = norm_members
        changed = True

    default_member = data["members"][0]["name"]
    valid_pm_ids = {m["id"] for m in data["members"] if m["role"] == "pm"}

    if "strictMemberEdit" not in data:
        data["strictMemberEdit"] = False
        changed = True
    else:
        data["strictMemberEdit"] = bool(data["strictMemberEdit"])

    if "projects" not in data or not isinstance(data.get("projects"), list):
        old_name = str(data.get("projectName") or "團隊專案排程")
        old_tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        data["projects"] = [
            {
                "id": "p_main",
                "name": old_name,
                "managerIds": [],
                "tasks": [normalize_task(t, default_member) for t in old_tasks],
            }
        ]
        data.pop("tasks", None)
        data.pop("projectName", None)
        changed = True
    else:
        if not data["projects"]:
            data["projects"] = [{"id": "p_main", "name": "預設專案", "managerIds": [], "tasks": []}]
            changed = True
        for p in data["projects"]:
            if not p.get("id"):
                p["id"] = "p_" + uuid.uuid4().hex[:8]
                changed = True
            if not p.get("name"):
                p["name"] = "未命名專案"
                changed = True
            raw_mgrs = p.get("managerIds")
            if not isinstance(raw_mgrs, list):
                p["managerIds"] = []
                changed = True
            else:
                cleaned_mgrs = [str(mid) for mid in raw_mgrs if str(mid) in valid_pm_ids]
                if cleaned_mgrs != raw_mgrs:
                    p["managerIds"] = cleaned_mgrs
                    changed = True
            raw_tasks = p.get("tasks") if isinstance(p.get("tasks"), list) else []
            norm_tasks = [normalize_task(t, default_member) for t in raw_tasks]
            if sanitize_project_parents(norm_tasks):
                changed = True
            if norm_tasks != raw_tasks:
                p["tasks"] = norm_tasks
                changed = True

    raw_roots = data.get("sharedRoots")
    norm_roots = normalize_shared_roots(raw_roots)
    if norm_roots != raw_roots:
        data["sharedRoots"] = norm_roots
        changed = True

    if CLI_SHARED_PATH:
        norm_cli = os.path.normcase(os.path.abspath(CLI_SHARED_PATH))
        if not any(os.path.normcase(os.path.abspath(r["path"])) == norm_cli for r in data["sharedRoots"]):
            lbl = os.path.basename(CLI_SHARED_PATH.rstrip("\\/")) or CLI_SHARED_PATH
            data["sharedRoots"].append({"id": "r_cli", "label": f"指定共用 ({lbl})", "path": CLI_SHARED_PATH})
            changed = True

    if "viewPassEnabled" not in data:
        data["viewPassEnabled"] = True
        changed = True
    else:
        data["viewPassEnabled"] = bool(data["viewPassEnabled"])
    if not str(data.get("viewPassHash") or "").strip():
        data["viewPassHash"] = hash_view_pass(DEFAULT_VIEW_PASS)
        changed = True

    if "holidays" not in data:
        data["holidays"] = normalize_holidays(None, use_default_if_missing=True)
        changed = True
    else:
        norm_h = normalize_holidays(data.get("holidays"), use_default_if_missing=False)
        if norm_h != data.get("holidays"):
            data["holidays"] = norm_h
            changed = True

    if data.get("schemaVersion") != 9:
        data["schemaVersion"] = 9
        changed = True

    data.setdefault("version", 1)
    data.setdefault("logs", [])
    return data, changed


def ensure_data_file():
    if not os.path.exists(DATA_FILE):
        save_data_unlocked(default_data())


def load_data_unlocked():
    ensure_data_file()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    data, changed = migrate_data_if_needed(data)
    if changed:
        save_data_unlocked(data)
    return data


def save_data_unlocked(data, trigger_cloud=True):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    tmp_path = DATA_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, DATA_FILE)

    today_str = datetime.date.today().isoformat()
    backup_path = os.path.join(BACKUP_DIR, f"project_data_{today_str}.json")
    if not os.path.exists(backup_path):
        shutil.copy2(DATA_FILE, backup_path)

    if trigger_cloud:
        SYNC_WAKE_EVENT.set()


# ---------- GitHub 私有倉庫自動雙向同步引擎 (純標準庫，零外部套件) ----------
def default_github_config():
    return {
        "enabled": False,
        "owner": "",
        "repo": "",
        "branch": "main",
        "filePath": "project_data.json",
        "token": "",
        "intervalSec": 10,
    }


_GH_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def sanitize_github_file_path(raw):
    r"""誤填絕對路徑（D:\Software\... 或 /home/user/...）時只保留檔名，避免私有庫長出巢狀目錄。"""
    s = str(raw or "").strip().replace("\\", "/").replace("\x00", "")
    if not s:
        return "project_data.json"
    absolute = False
    if _GH_DRIVE_RE.match(s):
        absolute = True
        s = s[2:].lstrip("/")
    if s.startswith("/") or s.startswith("~/"):
        absolute = True
    if absolute:
        s = s.rsplit("/", 1)[-1]
    parts = [seg for seg in s.split("/") if seg not in ("", ".", "..")]
    return "/".join(parts) or "project_data.json"


def normalize_github_config(raw):
    cfg = default_github_config()
    if not isinstance(raw, dict):
        return cfg
    owner = str(raw.get("owner") or "").strip().strip("/")
    repo = str(raw.get("repo") or "").strip().strip("/")
    # 若使用者把 owner/repo 整串貼進 repo 欄，自動拆分
    if "/" in repo and not owner:
        parts = [p for p in repo.split("/") if p]
        if len(parts) >= 2:
            owner, repo = parts[-2], parts[-1]
    elif "/" in owner and not repo:
        parts = [p for p in owner.split("/") if p]
        if len(parts) >= 2:
            owner, repo = parts[-2], parts[-1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    branch = str(raw.get("branch") or "main").strip() or "main"
    file_path = sanitize_github_file_path(raw.get("filePath"))
    token = str(raw.get("token") or "").strip()
    try:
        interval = max(5, min(300, int(raw.get("intervalSec", 10))))
    except Exception:
        interval = 10
    cfg.update({
        "enabled": bool(raw.get("enabled", False)),
        "owner": owner,
        "repo": repo,
        "branch": branch,
        "filePath": file_path,
        "token": token,
        "intervalSec": interval,
    })
    return cfg


def load_github_config():
    if not os.path.exists(GITHUB_SYNC_FILE):
        return default_github_config()
    try:
        with open(GITHUB_SYNC_FILE, "r", encoding="utf-8") as f:
            return normalize_github_config(json.load(f))
    except Exception:
        return default_github_config()


def save_github_config(cfg):
    clean = normalize_github_config(cfg)
    tmp = GITHUB_SYNC_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    os.replace(tmp, GITHUB_SYNC_FILE)
    return clean


def mask_token(tok):
    t = str(tok or "").strip()
    if not t:
        return ""
    if len(t) <= 8:
        return "*" * len(t)
    return f"{t[:4]}...{t[-4:]}"


def public_cloud_sync_status():
    return {
        "enabled": bool(CLOUD_SYNC_STATE.get("enabled")),
        "status": CLOUD_SYNC_STATE.get("status", "disabled"),
        "lastSync": CLOUD_SYNC_STATE.get("lastSync", ""),
        "lastError": CLOUD_SYNC_STATE.get("lastError", ""),
        "repo": CLOUD_SYNC_STATE.get("repo", ""),
        "branch": CLOUD_SYNC_STATE.get("branch", "main"),
        "filePath": CLOUD_SYNC_STATE.get("filePath", "project_data.json"),
        "intervalSec": CLOUD_SYNC_STATE.get("intervalSec", 10),
        "tick": CLOUD_SYNC_STATE.get("tick", 0),
    }


def _bump_cloud_state(**kwargs):
    CLOUD_SYNC_STATE.update(kwargs)
    CLOUD_SYNC_STATE["tick"] = int(CLOUD_SYNC_STATE.get("tick", 0)) + 1


def gh_api_url(cfg):
    fp = quote(cfg["filePath"].lstrip("/"), safe="/")
    return f"{GITHUB_API_BASE}/repos/{quote(cfg['owner'])}/{quote(cfg['repo'])}/contents/{fp}"


def gh_get_file(cfg, etag=""):
    url = gh_api_url(cfg) + f"?ref={quote(cfg['branch'], safe='')}"
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "TeamPM-LAN-v11",
    }
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            new_etag = resp.headers.get("ETag") or ""
            body = json.loads(resp.read().decode("utf-8"))
            raw_b64 = str(body.get("content") or "").replace("\n", "").replace("\r", "")
            sha = str(body.get("sha") or "")
            decoded = base64.b64decode(raw_b64).decode("utf-8")
            return "ok", json.loads(decoded), new_etag, sha, None
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return "not_modified", None, etag, None, None
        if e.code == 404:
            return "not_found", None, "", "", None
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            detail = err_body.get("message") or str(e)
        except Exception:
            detail = f"HTTP {e.code}"
        if e.code == 401:
            return "error", None, "", "", f"GitHub Token 無效或已過期（{detail}）"
        if e.code == 403:
            return "error", None, "", "", f"GitHub 權限不足或超過速率限制（{detail}）"
        return "error", None, "", "", f"GitHub 讀取失敗 HTTP {e.code}：{detail}"
    except Exception as e:
        return "error", None, "", "", f"無法連線 GitHub：{e}"


def gh_put_file(cfg, data_dict, sha="", message="Update project_data.json"):
    url = gh_api_url(cfg)
    raw_json = json.dumps(data_dict, ensure_ascii=False, indent=2).encode("utf-8")
    payload = {
        "message": message,
        "content": base64.b64encode(raw_json).decode("ascii"),
        "branch": cfg["branch"],
    }
    if sha:
        payload["sha"] = sha
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "TeamPM-LAN-v11",
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            new_sha = str((body.get("content") or {}).get("sha") or "")
            return "ok", new_sha, None
    except urllib.error.HTTPError as e:
        if e.code in (409, 422):
            return "conflict", "", "雲端檔案同時被修改，正在自動重新合併..."
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            detail = err_body.get("message") or str(e)
        except Exception:
            detail = f"HTTP {e.code}"
        return "error", "", f"GitHub 寫入失敗 HTTP {e.code}：{detail}"
    except Exception as e:
        return "error", "", f"無法推送至 GitHub：{e}"


def sync_with_github_once(force_check=False, _retry=0):
    need_retry = False
    with SYNC_LOCK:
        cfg = load_github_config()
        repo_full = f"{cfg['owner']}/{cfg['repo']}" if (cfg["owner"] and cfg["repo"]) else ""
        if not cfg["enabled"] or not cfg["owner"] or not cfg["repo"] or not cfg["token"]:
            _bump_cloud_state(
                enabled=bool(cfg["enabled"]),
                status="disabled" if not cfg["enabled"] else "error",
                lastError="" if not cfg["enabled"] else "GitHub 倉庫或 Token 設定不完整",
                repo=repo_full,
                branch=cfg["branch"],
                filePath=cfg["filePath"],
                intervalSec=cfg["intervalSec"],
            )
            return False, CLOUD_SYNC_STATE["lastError"] or "未啟用雲端同步"

        _bump_cloud_state(
            enabled=True,
            status="syncing",
            repo=repo_full,
            branch=cfg["branch"],
            filePath=cfg["filePath"],
            intervalSec=cfg["intervalSec"],
        )

        with DATA_LOCK:
            local_data = json.loads(json.dumps(load_data_unlocked()))
        local_ver = int(local_data.get("version", 1))
        need_push_check = force_check or (local_ver != CLOUD_SYNC_STATE.get("lastSyncedVersion", 0))
        use_etag = "" if need_push_check else CLOUD_SYNC_STATE.get("remoteEtag", "")

        status, remote_data, new_etag, remote_sha, err = gh_get_file(cfg, etag=use_etag)
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if status == "error":
            _bump_cloud_state(status="error", lastError=err or "讀取失敗")
            return False, err or "讀取失敗"

        if status == "not_modified":
            _bump_cloud_state(status="synced", lastSync=now_ts, lastError="")
            return True, "已是最新（無變更）"

        if status == "not_found":
            p_st, new_sha, p_err = gh_put_file(
                cfg, local_data, sha="",
                message=f"Initialize project_data.json (v{local_ver})"
            )
            if p_st == "ok":
                _bump_cloud_state(
                    status="synced", lastSync=now_ts, lastError="",
                    remoteSha=new_sha, remoteEtag="", lastSyncedVersion=local_ver
                )
                return True, f"已初始化並上傳至 GitHub（v{local_ver}）"
            _bump_cloud_state(status="error", lastError=p_err or "上傳失敗")
            return False, p_err or "上傳失敗"

        remote_ver = int(remote_data.get("version", 0)) if isinstance(remote_data, dict) else 0

        if remote_ver > local_ver:
            with DATA_LOCK:
                cur_local = load_data_unlocked()
                cur_local_ver = int(cur_local.get("version", 0))
                if cur_local_ver < remote_ver:
                    save_snapshot_unlocked("before_cloud_pull")
                    local_roots = cur_local.get("sharedRoots")
                    migrated, _ = migrate_data_if_needed(dict(remote_data))
                    if local_roots:
                        migrated["sharedRoots"] = local_roots
                    migrated["version"] = remote_ver
                    save_data_unlocked(migrated, trigger_cloud=False)
            _bump_cloud_state(
                status="synced", lastSync=now_ts, lastError="",
                remoteSha=remote_sha, remoteEtag=new_etag, lastSyncedVersion=remote_ver
            )
            return True, f"已從 GitHub 雲端拉取最新進度（v{remote_ver}）"

        if local_ver > remote_ver:
            p_st, new_sha, p_err = gh_put_file(
                cfg, local_data, sha=remote_sha,
                message=f"Sync from LAN v{local_ver}"
            )
            if p_st == "ok":
                _bump_cloud_state(
                    status="synced", lastSync=now_ts, lastError="",
                    remoteSha=new_sha, remoteEtag="", lastSyncedVersion=local_ver
                )
                return True, f"已推送至 GitHub 雲端（v{local_ver}）"
            if p_st == "conflict" and _retry < 2:
                need_retry = True
            else:
                _bump_cloud_state(status="error", lastError=p_err or "推送失敗")
                return False, p_err or "推送失敗"
        else:
            _bump_cloud_state(
                status="synced", lastSync=now_ts, lastError="",
                remoteSha=remote_sha, remoteEtag=new_etag, lastSyncedVersion=local_ver
            )
            return True, f"本地與雲端已同步（v{local_ver}）"

    if need_retry:
        return sync_with_github_once(force_check=True, _retry=_retry + 1)
    return False, "同步中止"


def cloud_sync_worker():
    while True:
        try:
            cfg = load_github_config()
            if cfg["enabled"] and cfg["owner"] and cfg["repo"] and cfg["token"]:
                sync_with_github_once(force_check=False)
                wait_sec = max(5, int(cfg.get("intervalSec", 10)))
            else:
                if CLOUD_SYNC_STATE.get("enabled"):
                    _bump_cloud_state(enabled=False, status="disabled", lastError="")
                wait_sec = 10
        except Exception as e:
            _bump_cloud_state(status="error", lastError=str(e))
            wait_sec = 15
        SYNC_WAKE_EVENT.wait(timeout=wait_sec)
        SYNC_WAKE_EVENT.clear()


def save_snapshot_unlocked(prefix):
    if not os.path.exists(DATA_FILE):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    snap = os.path.join(BACKUP_DIR, f"{prefix}_{ts}.json")
    shutil.copy2(DATA_FILE, snap)


def reset_admin_pins():
    with DATA_LOCK:
        data = load_data_unlocked()
        admins = [m for m in data["members"] if m["role"] == "sysadmin"]
        if not admins:
            data["members"][0]["role"] = "sysadmin"
            admins = [data["members"][0]]
        for adm in admins:
            adm["pinHash"] = hash_pin(adm["id"], "1234")
            adm["hasPin"] = True
        data["version"] = int(data.get("version", 1)) + 1
        add_log(data, "系統", "已透過命令列重設系統管理員 PIN 碼為 1234")
        save_data_unlocked(data)
        return [a["name"] for a in admins]


def reset_view_password():
    with DATA_LOCK:
        data = load_data_unlocked()
        data["viewPassEnabled"] = True
        data["viewPassHash"] = hash_view_pass(DEFAULT_VIEW_PASS)
        VIEW_SESSIONS.clear()
        data["version"] = int(data.get("version", 1)) + 1
        add_log(data, "系統", f"已透過命令列重設觀看密碼為 {DEFAULT_VIEW_PASS}")
        save_data_unlocked(data)


def public_member(m):
    return {
        "id": m["id"],
        "name": m["name"],
        "role": m["role"],
        "hasPin": bool(m.get("pinHash")),
    }


def public_shared_roots(data):
    out = []
    for r in data.get("sharedRoots", []):
        p = str(r.get("path") or "").strip()
        avail = bool(p and os.path.isdir(p))
        out.append({
            "id": r["id"],
            "label": r["label"],
            "path": p,
            "available": avail,
        })
    return out


def public_state(data, me=None):
    return {
        "schemaVersion": data.get("schemaVersion", 9),
        "version": data.get("version", 1),
        "bootId": SERVER_BOOT_ID,
        "strictMemberEdit": bool(data.get("strictMemberEdit", False)),
        "viewPassEnabled": bool(data.get("viewPassEnabled", True)),
        "cloudSync": public_cloud_sync_status(),
        "holidays": data.get("holidays", []),
        "sharedRoots": public_shared_roots(data),
        "members": [public_member(m) for m in data.get("members", [])],
        "projects": data.get("projects", []),
        "logs": data.get("logs", []),
        "me": public_member(me) if me else None,
    }


def find_project(data, project_id):
    for p in data.get("projects", []):
        if p["id"] == project_id:
            return p
    return data["projects"][0] if data.get("projects") else None


def find_member_by_id(data, member_id):
    for m in data.get("members", []):
        if m["id"] == member_id:
            return m
    return None


def add_log(data, user, text, project=None):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data.setdefault("logs", []).insert(0, {
        "time": now_str,
        "user": user or "成員",
        "projectId": project["id"] if project else "",
        "projectName": project["name"] if project else "系統設定",
        "text": text,
    })
    data["logs"] = data["logs"][:150]


# ---------- 四級權限判定函式 ----------
def is_sysadmin(me):
    return bool(me and me.get("role") == "sysadmin")


def can_create_project(me):
    return bool(me and me.get("role") in ("sysadmin", "pm"))


def can_manage_project(proj, me):
    """系統管理員可管理所有專案；專案管理員可管理未指定限制或自己負責的專案"""
    if not me or not proj:
        return False
    role = me.get("role")
    if role == "sysadmin":
        return True
    if role == "pm":
        mgrs = proj.get("managerIds") or []
        return (len(mgrs) == 0) or (me["id"] in mgrs)
    return False


def can_edit_task(data, proj, me, old_task):
    if not me:
        return False
    if can_manage_project(proj, me):
        return True
    if me.get("role") not in ("pm", "member"):
        return False
    if old_task is None:
        return True
    if data.get("strictMemberEdit") and old_task.get("assignee") != me["name"]:
        return False
    return True


def can_delete_task(proj, me, old_task):
    if not me or not old_task:
        return False
    if can_manage_project(proj, me):
        return True
    if me.get("role") in ("pm", "member") and old_task.get("assignee") == me["name"]:
        return True
    return False


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


class PMHandler(BaseHTTPRequestHandler):
    timeout = 30

    def setup(self):
        super().setup()
        try:
            self.connection.settimeout(30)
        except Exception:
            pass

    def log_message(self, format, *args):
        if args and "/api/state" in str(args[0]):
            return
        super().log_message(format, *args)

    def send_json(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def get_view_token(self, qs=None):
        tok = (self.headers.get("X-View-Token") or "").strip()
        if not tok and qs:
            tok = (qs.get("vtoken", [""])[0] or "").strip()
        return tok

    def is_view_unlocked(self, data, qs=None):
        if not data.get("viewPassEnabled", True):
            return True
        if not str(data.get("viewPassHash") or "").strip():
            return True
        tok = self.get_view_token(qs)
        return bool(tok and tok in VIEW_SESSIONS)

    def send_view_locked(self):
        self.send_json(401, {
            "ok": False,
            "locked": True,
            "requireViewPass": True,
            "error": "請先輸入觀看密碼以檢視專案內容",
        })

    def resolve_me_unlocked(self, data):
        token = (self.headers.get("X-Auth-Token") or "").strip()
        if not token:
            return None
        sess = SESSIONS.get(token)
        if not sess:
            return None
        member = find_member_by_id(data, sess.get("memberId"))
        if not member:
            SESSIONS.pop(token, None)
            return None
        return member

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            raw = HTML_CONTENT.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        qs = parse_qs(parsed.query)
        with DATA_LOCK:
            _gate_data = load_data_unlocked()
            _unlocked = self.is_view_unlocked(_gate_data, qs)
        if not _unlocked:
            self.send_view_locked()
            return

        if parsed.path == "/api/state":
            since = qs.get("since", [None])[0]
            boot = qs.get("boot", [None])[0]
            ctick = qs.get("ctick", [None])[0]
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                cur_ctick = str(CLOUD_SYNC_STATE.get("tick", 0))
                if (
                    since is not None
                    and boot == SERVER_BOOT_ID
                    and str(data.get("version")) == str(since)
                    and (ctick is None or ctick == cur_ctick)
                ):
                    self.send_response(304)
                    self.end_headers()
                    return
                payload = public_state(data, me)
            self.send_json(200, payload)
            return

        if parsed.path == "/api/settings/github":
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not is_sysadmin(me):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」可檢視 GitHub 雲端同步設定"})
                    return
            cfg = load_github_config()
            self.send_json(200, {
                "ok": True,
                "config": {
                    "enabled": cfg["enabled"],
                    "owner": cfg["owner"],
                    "repo": cfg["repo"],
                    "branch": cfg["branch"],
                    "filePath": cfg["filePath"],
                    "intervalSec": cfg["intervalSec"],
                    "hasToken": bool(cfg["token"]),
                    "tokenMask": mask_token(cfg["token"]),
                },
                "status": public_cloud_sync_status(),
            })
            return

        if parsed.path == "/api/fs/list":
            qs = parse_qs(parsed.query)
            root_id = (qs.get("root", [""])[0] or "").strip()
            rel_path = qs.get("path", [""])[0] or ""
            with DATA_LOCK:
                data = load_data_unlocked()
                roots = data.get("sharedRoots") or []
                if not root_id and roots:
                    root_id = roots[0]["id"]
                root, norm_rel, target_real, err = resolve_shared_target(data, root_id, rel_path)
            if err:
                self.send_json(400, {"ok": False, "error": err})
                return
            if not os.path.exists(target_real):
                self.send_json(404, {"ok": False, "error": "找不到該資料夾或檔案（可能已被移動或刪除）"})
                return
            if not os.path.isdir(target_real):
                # 若點到的其實是檔案，回傳上層資料夾與目標提示
                parent_rel = "/".join(norm_rel.split("/")[:-1]) if norm_rel else ""
                self.send_json(200, {
                    "ok": True,
                    "isFileTarget": True,
                    "root": {"id": root["id"], "label": root["label"], "path": root["path"]},
                    "path": norm_rel,
                    "parent": parent_rel,
                    "realPath": target_real,
                    "items": [],
                })
                return

            items = []
            try:
                with os.scandir(target_real) as it:
                    for entry in it:
                        if entry.name.lower() in SKIP_SYSTEM_FILES:
                            continue
                        try:
                            is_dir = entry.is_dir(follow_symlinks=True)
                            st = entry.stat(follow_symlinks=True)
                            size = 0 if is_dir else st.st_size
                            mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                        except OSError:
                            continue
                        ext = "" if is_dir else os.path.splitext(entry.name)[1].lower()
                        child_rel = f"{norm_rel}/{entry.name}" if norm_rel else entry.name
                        items.append({
                            "name": entry.name,
                            "rel": child_rel,
                            "isDir": is_dir,
                            "size": size,
                            "mtime": mtime,
                            "ext": ext,
                            "previewable": (not is_dir) and (ext in PREVIEWABLE_EXTS),
                        })
            except PermissionError:
                self.send_json(403, {"ok": False, "error": "伺服器電腦沒有讀取此資料夾的權限"})
                return
            except Exception as e:
                self.send_json(500, {"ok": False, "error": f"讀取資料夾失敗：{e}"})
                return

            items.sort(key=lambda x: (0 if x["isDir"] else 1, x["name"].lower()))
            parent_rel = "/".join(norm_rel.split("/")[:-1]) if norm_rel else None
            self.send_json(200, {
                "ok": True,
                "root": {"id": root["id"], "label": root["label"], "path": root["path"]},
                "path": norm_rel,
                "parent": parent_rel,
                "realPath": target_real,
                "items": items,
            })
            return

        if parsed.path == "/api/fs/file":
            qs = parse_qs(parsed.query)
            root_id = (qs.get("root", [""])[0] or "").strip()
            rel_path = qs.get("path", [""])[0] or ""
            force_dl = qs.get("download", ["0"])[0] == "1"
            with DATA_LOCK:
                data = load_data_unlocked()
                root, norm_rel, target_real, err = resolve_shared_target(data, root_id, rel_path)
            if err:
                self.send_json(403, {"ok": False, "error": err})
                return
            if not os.path.isfile(target_real):
                self.send_json(404, {"ok": False, "error": "找不到指定檔案"})
                return

            fname = os.path.basename(target_real)
            ext = os.path.splitext(fname)[1].lower()
            ctype = MIME_OVERRIDES.get(ext) or mimetypes.guess_type(fname)[0] or "application/octet-stream"
            disp = "attachment" if (force_dl or ext not in PREVIEWABLE_EXTS) else "inline"
            ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in fname) or "file"
            utf8_name = quote(fname, safe="")

            try:
                fsize = os.path.getsize(target_real)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(fsize))
                self.send_header("Content-Disposition", f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with open(target_real, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                try:
                    self.send_json(500, {"ok": False, "error": f"讀取檔案失敗：{e}"})
                except Exception:
                    pass
            return

        if parsed.path == "/api/backup/export":
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not is_sysadmin(me):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」可匯出完整系統備份"})
                    return
                export_copy = json.loads(json.dumps(data))
            self.send_json(200, {"ok": True, "backup": export_copy})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            body = self.read_json_body()
        except Exception as e:
            self.send_json(400, {"ok": False, "error": f"JSON 格式錯誤: {e}"})
            return

        if parsed.path == "/api/unlock":
            pw = str(body.get("password") or "").strip()
            with DATA_LOCK:
                data = load_data_unlocked()
                if not data.get("viewPassEnabled", True) or verify_view_pass(data, pw):
                    vtoken = uuid.uuid4().hex
                    VIEW_SESSIONS.add(vtoken)
                    self.send_json(200, {"ok": True, "viewToken": vtoken})
                    return
            self.send_json(401, {"ok": False, "locked": True, "error": "觀看密碼錯誤，請重新輸入"})
            return

        if parsed.path == "/api/lock-view":
            vtok = self.get_view_token()
            atok = (self.headers.get("X-Auth-Token") or "").strip()
            with DATA_LOCK:
                if vtok:
                    VIEW_SESSIONS.discard(vtok)
                if atok:
                    SESSIONS.pop(atok, None)
            self.send_json(200, {"ok": True})
            return

        with DATA_LOCK:
            _gate_data = load_data_unlocked()
            _unlocked = self.is_view_unlocked(_gate_data)
        if not _unlocked:
            self.send_view_locked()
            return

        if parsed.path == "/api/login":
            member_id = str(body.get("memberId") or "").strip()
            pin = str(body.get("pin") or "").strip()
            with DATA_LOCK:
                data = load_data_unlocked()
                member = find_member_by_id(data, member_id)
                if not member:
                    self.send_json(404, {"ok": False, "error": "找不到該成員"})
                    return
                if not verify_pin(member, pin):
                    self.send_json(401, {"ok": False, "error": "PIN 碼錯誤，請重新輸入"})
                    return
                token = uuid.uuid4().hex
                SESSIONS[token] = {"memberId": member["id"]}
                payload = public_state(data, member)
            self.send_json(200, {"ok": True, "token": token, "data": payload})
            return

        if parsed.path == "/api/logout":
            token = (self.headers.get("X-Auth-Token") or "").strip()
            with DATA_LOCK:
                SESSIONS.pop(token, None)
                data = load_data_unlocked()
                payload = public_state(data, None)
            self.send_json(200, {"ok": True, "data": payload})
            return

        project_id = str(body.get("projectId") or "")

        if parsed.path == "/api/my-pin":
            old_pin = str(body.get("oldPin") or "").strip()
            new_pin = str(body.get("newPin") or "").strip()
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not me:
                    self.send_json(401, {"ok": False, "error": "請先選擇並登入您的身分"})
                    return
                if me.get("pinHash") and not verify_pin(me, old_pin):
                    self.send_json(401, {"ok": False, "error": "原本的 PIN 碼不正確"})
                    return
                if me["role"] == "sysadmin" and not new_pin:
                    self.send_json(400, {"ok": False, "error": "為了保護系統，系統管理員必須保留 PIN 碼（不可設為空白）"})
                    return
                me["pinHash"] = hash_pin(me["id"], new_pin)
                me["hasPin"] = bool(me["pinHash"])
                add_log(data, me["name"], "更新個人登入 PIN 碼")
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "data": payload})
            return

        if parsed.path == "/api/task":
            task_in = body.get("task") or {}
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not me:
                    self.send_json(401, {"ok": False, "error": "請先點選右上角登入您的身分再進行編輯"})
                    return
                proj = find_project(data, project_id)
                if not proj:
                    self.send_json(400, {"ok": False, "error": "找不到指定專案"})
                    return
                tasks = proj.setdefault("tasks", [])
                tid = task_in.get("id")
                idx = next((i for i, t in enumerate(tasks) if t["id"] == tid), None) if tid else None
                old_task = tasks[idx] if idx is not None else None

                if not can_edit_task(data, proj, me, old_task):
                    msg = (
                        "系統已開啟「嚴格模式」，您於此專案僅能編輯自己負責的任務。"
                        if me["role"] in ("member", "pm")
                        else "您目前為「唯讀檢視」身分，無法新增或修改排程。"
                    )
                    self.send_json(403, {"ok": False, "error": msg})
                    return

                clean = normalize_task(task_in, me["name"])
                if not can_manage_project(proj, me) and data.get("strictMemberEdit"):
                    clean["assignee"] = me["name"]

                if clean["parentId"]:
                    valid_ids = {t["id"] for t in tasks}
                    if clean["parentId"] not in valid_ids:
                        clean["parentId"] = ""
                    elif has_parent_cycle(tasks, clean["id"], clean["parentId"]):
                        self.send_json(400, {"ok": False, "error": "無法將自己或自己的子任務設為上層任務（會形成循環）"})
                        return

                kind = "里程碑 ◆" if clean["isMilestone"] else "任務"
                s_disp = clean["start"].replace("T", " ")
                e_disp = clean["end"].replace("T", " ")
                date_desc = s_disp if clean["isMilestone"] else f"{s_disp} ~ {e_disp}"
                if idx is None:
                    tasks.append(clean)
                    add_log(data, me["name"], f"新增{kind}「{clean['title']}」（{date_desc}）", proj)
                else:
                    tasks[idx] = clean
                    add_log(data, me["name"], f"更新{kind}「{clean['title']}」（{date_desc}，{clean['progress']}%）", proj)
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "data": payload})
            return

        if parsed.path == "/api/task/split-holidays":
            tid = body.get("id")
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not me:
                    self.send_json(401, {"ok": False, "error": "請先登入身分"})
                    return
                proj = find_project(data, project_id)
                if not proj:
                    self.send_json(400, {"ok": False, "error": "找不到指定專案"})
                    return
                tasks = proj.setdefault("tasks", [])
                idx = next((i for i, t in enumerate(tasks) if t["id"] == tid), None)
                if idx is None:
                    self.send_json(404, {"ok": False, "error": "找不到該任務"})
                    return
                old = tasks[idx]
                if not can_edit_task(data, proj, me, old):
                    self.send_json(403, {"ok": False, "error": "權限不足：無法拆分此任務"})
                    return
                if old.get("isMilestone"):
                    self.send_json(400, {"ok": False, "error": "里程碑為單一時點，無法依休假日拆分"})
                    return

                segs, hit_names = split_task_interval_by_holidays(old["start"], old["end"], data.get("holidays", []))
                if len(segs) < 2:
                    self.send_json(400, {"ok": False, "error": "此任務並未跨越多段非休假工作區間（或休假日完全覆蓋），無需實體拆分。"})
                    return

                total_sec = sum(s[2] for s in segs) or 1.0
                done_sec = total_sec * (int(old.get("progress", 0)) / 100.0)
                base_title = re.sub(r"\s*[(（](?:休假前|休假後|第\d+段)[)）]$", "", old["title"]).strip() or old["title"]

                created = []
                acc_sec = 0.0
                prev_id = old.get("dep") or ""
                for si, (seg_s, seg_e, seg_sec) in enumerate(segs):
                    fill_sec = max(0.0, min(seg_sec, done_sec - acc_sec))
                    acc_sec += seg_sec
                    seg_prog = int(round((fill_sec / seg_sec) * 100)) if seg_sec > 0 else 0
                    seg_prog = max(0, min(100, (seg_prog // 5) * 5))
                    if old.get("status") == "done":
                        seg_status = "done"
                        seg_prog = 100
                    elif seg_prog >= 100:
                        seg_status = "done"
                    elif seg_prog > 0:
                        seg_status = "doing"
                    else:
                        seg_status = "todo" if si > 0 else old.get("status", "todo")

                    if len(segs) == 2:
                        suffix = " (休假前)" if si == 0 else " (休假後)"
                    else:
                        suffix = f" (第{si + 1}段)"

                    seg_id = old["id"] if si == 0 else ("t_" + uuid.uuid4().hex[:8])
                    new_t = {
                        **old,
                        "id": seg_id,
                        "title": base_title + suffix,
                        "start": seg_s,
                        "end": seg_e,
                        "actualEnd": old.get("actualEnd", "") if si == len(segs) - 1 else "",
                        "progress": seg_prog,
                        "status": seg_status,
                        "dep": prev_id,
                        "splitByHolidays": True,
                    }
                    created.append(new_t)
                    prev_id = seg_id

                last_id = created[-1]["id"]
                # 原先依賴此任務的後續任務改為依賴最後一段
                for t in tasks:
                    if t["id"] != old["id"] and t.get("dep") == old["id"]:
                        t["dep"] = last_id

                tasks[idx:idx + 1] = created
                h_desc = "、".join(hit_names)
                add_log(data, me["name"], f"依休假日（{h_desc}）將「{old['title']}」實體拆分為 {len(created)} 段任務", proj)
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "segments": len(created), "data": payload})
            return

        if parsed.path == "/api/settings/holidays":
            incoming = body.get("holidays")
            if not isinstance(incoming, list):
                self.send_json(400, {"ok": False, "error": "休假日清單格式錯誤"})
                return
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not me or me.get("role") not in ("sysadmin", "pm"):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」或「專案管理員」可修改休假日行事曆"})
                    return
                cleaned = normalize_holidays(incoming, use_default_if_missing=False)
                data["holidays"] = cleaned
                add_log(data, me["name"], f"更新休假日行事曆（共 {len(cleaned)} 筆休假區間）")
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "data": payload})
            return

        if parsed.path == "/api/task/delete":
            tid = body.get("id")
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not me:
                    self.send_json(401, {"ok": False, "error": "請先登入身分"})
                    return
                proj = find_project(data, project_id)
                if not proj:
                    self.send_json(400, {"ok": False, "error": "找不到指定專案"})
                    return
                old = next((t for t in proj.get("tasks", []) if t["id"] == tid), None)
                if not old:
                    self.send_json(404, {"ok": False, "error": "找不到該任務"})
                    return
                if not can_delete_task(proj, me, old):
                    self.send_json(403, {"ok": False, "error": "權限不足：您僅能刪除自己負責的任務，或請專案管理員／系統管理員協助刪除。"})
                    return
                old_parent = old.get("parentId") or ""
                promoted = 0
                proj["tasks"] = [t for t in proj.get("tasks", []) if t["id"] != tid]
                for t in proj["tasks"]:
                    if t.get("dep") == tid:
                        t["dep"] = ""
                    if t.get("parentId") == tid:
                        t["parentId"] = old_parent
                        promoted += 1
                suffix = f"（其下 {promoted} 筆子任務已自動上移一層）" if promoted else ""
                add_log(data, me["name"], f"刪除「{old['title']}」{suffix}", proj)
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "data": payload})
            return

        # ---------- 專案管理員 / 系統管理員 操作 ----------
        if parsed.path == "/api/project/create":
            name = str(body.get("name") or "").strip()
            if not name:
                self.send_json(400, {"ok": False, "error": "請輸入專案名稱"})
                return
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not can_create_project(me):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」或「專案管理員」可以建立新專案"})
                    return
                # 若由專案管理員建立，預設將本人設為該專案負責人（後續可在專案設定中調整或開放給所有 PM）
                init_mgrs = [me["id"]] if me["role"] == "pm" else []
                new_proj = {
                    "id": "p_" + uuid.uuid4().hex[:8],
                    "name": name,
                    "managerIds": init_mgrs,
                    "tasks": [],
                }
                data["projects"].append(new_proj)
                add_log(data, me["name"], f"建立新專案「{name}」", new_proj)
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "projectId": new_proj["id"], "data": payload})
            return

        if parsed.path == "/api/project/delete":
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if len(data.get("projects", [])) <= 1:
                    self.send_json(400, {"ok": False, "error": "至少需要保留一個專案，無法刪除。"})
                    return
                target = next((p for p in data["projects"] if p["id"] == project_id), None)
                if not target:
                    self.send_json(404, {"ok": False, "error": "找不到該專案"})
                    return
                if not can_manage_project(target, me):
                    self.send_json(403, {"ok": False, "error": "權限不足：僅「系統管理員」或本專案指定的「專案管理員」可刪除此專案"})
                    return
                save_snapshot_unlocked("before_delete_project")
                data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
                add_log(data, me["name"], f"刪除專案「{target['name']}」（刪除前已自動留存快照備份）")
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "data": payload})
            return

        if parsed.path == "/api/settings/project":
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                proj = find_project(data, project_id)
                if not proj:
                    self.send_json(404, {"ok": False, "error": "找不到該專案"})
                    return
                if not can_manage_project(proj, me):
                    self.send_json(403, {"ok": False, "error": "權限不足：僅「系統管理員」或本專案指定的「專案管理員」可修改此專案設定"})
                    return
                changes = []
                new_name = str(body.get("projectName") or "").strip()
                if new_name and new_name != proj["name"]:
                    proj["name"] = new_name
                    changes.append(f"更名為「{new_name}」")
                if "managerIds" in body and isinstance(body["managerIds"], list):
                    valid_pm_ids = {m["id"] for m in data["members"] if m["role"] == "pm"}
                    new_mgrs = [str(mid) for mid in body["managerIds"] if str(mid) in valid_pm_ids]
                    if new_mgrs != (proj.get("managerIds") or []):
                        proj["managerIds"] = new_mgrs
                        if new_mgrs:
                            names = [m["name"] for m in data["members"] if m["id"] in new_mgrs]
                            changes.append(f"指定專案管理員：{', '.join(names)}")
                        else:
                            changes.append("開放所有專案管理員共同管理")
                if changes:
                    add_log(data, me["name"], "更新專案設定（" + "；".join(changes) + "）", proj)
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "data": payload})
            return

        # ---------- 以下皆為「系統管理員專屬」操作 ----------
        if parsed.path == "/api/settings/members":
            incoming = body.get("members")
            strict_mode = bool(body.get("strictMemberEdit", False))
            if not isinstance(incoming, list) or not incoming:
                self.send_json(400, {"ok": False, "error": "成員清單不可為空"})
                return
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not is_sysadmin(me):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」可以管理人員帳號與系統權限"})
                    return

                old_by_id = {m["id"]: m for m in data["members"]}
                new_list = []
                seen_names = set()
                rename_map = {}

                for item in incoming:
                    name = str(item.get("name") or "").strip()
                    if not name:
                        continue
                    if name in seen_names:
                        self.send_json(400, {"ok": False, "error": f"成員名稱「{name}」重複，請修改後再儲存。"})
                        return
                    seen_names.add(name)

                    uid = str(item.get("id") or "").strip()
                    existing = old_by_id.get(uid) if uid else None
                    if not existing:
                        uid = "u_" + uuid.uuid4().hex[:6]

                    role = normalize_role(item.get("role"))
                    clear_pin = bool(item.get("clearPin", False))
                    new_pin = str(item.get("newPin") or "").strip()

                    if clear_pin:
                        pin_hash = ""
                    elif new_pin:
                        pin_hash = hash_pin(uid, new_pin)
                    elif existing:
                        pin_hash = existing.get("pinHash") or ""
                    else:
                        pin_hash = ""

                    if existing and existing["name"] != name:
                        rename_map[existing["name"]] = name

                    new_list.append({
                        "id": uid,
                        "name": name,
                        "role": role,
                        "pinHash": pin_hash,
                        "hasPin": bool(pin_hash),
                    })

                if not any(m["role"] == "sysadmin" for m in new_list):
                    self.send_json(400, {"ok": False, "error": "至少必須保留一位「系統管理員」，無法儲存。"})
                    return

                # 同步更名所有專案任務中的負責人，並清理已非 PM 的專案指定名單
                valid_pm_ids = {m["id"] for m in new_list if m["role"] == "pm"}
                for p in data.get("projects", []):
                    p["managerIds"] = [mid for mid in (p.get("managerIds") or []) if mid in valid_pm_ids]
                    if rename_map:
                        for t in p.get("tasks", []):
                            if t.get("assignee") in rename_map:
                                t["assignee"] = rename_map[t["assignee"]]

                valid_ids = {m["id"] for m in new_list}
                for tok in list(SESSIONS.keys()):
                    if SESSIONS[tok].get("memberId") not in valid_ids:
                        SESSIONS.pop(tok, None)

                data["members"] = new_list
                data["strictMemberEdit"] = strict_mode
                updated_me = self.resolve_me_unlocked(data)
                add_log(
                    data,
                    me["name"],
                    f"更新人員權限設定（共 {len(new_list)} 人，嚴格模式：{'開啟' if strict_mode else '關閉'}）",
                )
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, updated_me)
            self.send_json(200, {"ok": True, "data": payload})
            return

        if parsed.path == "/api/fs/check-path":
            path_in = str(body.get("path") or "").strip()
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not is_sysadmin(me):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」可測試共用路徑"})
                    return
            info = inspect_dir_path(path_in)
            self.send_json(200, {"ok": True, **info})
            return

        if parsed.path == "/api/settings/shared-roots":
            incoming = body.get("sharedRoots")
            if not isinstance(incoming, list):
                self.send_json(400, {"ok": False, "error": "共用資料夾清單格式錯誤"})
                return
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not is_sysadmin(me):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」可修改共用資料夾設定"})
                    return
                cleaned = []
                seen_ids = set()
                for item in incoming:
                    if not isinstance(item, dict):
                        continue
                    p = str(item.get("path") or "").strip()
                    if not p:
                        continue
                    rid = str(item.get("id") or "").strip()
                    if not rid or rid in seen_ids:
                        rid = "r_" + uuid.uuid4().hex[:6]
                    seen_ids.add(rid)
                    lbl = str(item.get("label") or "").strip() or os.path.basename(p.rstrip("\\/")) or p
                    cleaned.append({"id": rid, "label": lbl, "path": p})
                if not cleaned:
                    self.send_json(400, {"ok": False, "error": "請至少保留一個共用資料夾"})
                    return
                data["sharedRoots"] = cleaned
                add_log(data, me["name"], f"更新共用資料夾設定（共 {len(cleaned)} 個根目錄）")
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "data": payload})
            return

        if parsed.path == "/api/settings/github":
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not is_sysadmin(me):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」可修改 GitHub 雲端同步設定"})
                    return
            cur_cfg = load_github_config()
            new_token = str(body.get("token") or "").strip()
            clear_token = bool(body.get("clearToken", False))
            merged_cfg = {
                "enabled": bool(body.get("enabled", False)),
                "owner": body.get("owner", cur_cfg["owner"]),
                "repo": body.get("repo", cur_cfg["repo"]),
                "branch": body.get("branch", cur_cfg["branch"]),
                "filePath": body.get("filePath", cur_cfg["filePath"]),
                "intervalSec": body.get("intervalSec", cur_cfg["intervalSec"]),
                "token": "" if clear_token else (new_token or cur_cfg["token"]),
            }
            saved_cfg = save_github_config(merged_cfg)
            if saved_cfg["enabled"]:
                ok, msg = sync_with_github_once(force_check=True)
            else:
                _bump_cloud_state(
                    enabled=False, status="disabled", lastError="",
                    repo=f"{saved_cfg['owner']}/{saved_cfg['repo']}" if (saved_cfg["owner"] and saved_cfg["repo"]) else "",
                    branch=saved_cfg["branch"], filePath=saved_cfg["filePath"], intervalSec=saved_cfg["intervalSec"]
                )
                ok, msg = True, "已停用 GitHub 雲端同步"
            with DATA_LOCK:
                data = load_data_unlocked()
                add_log(data, me["name"], f"更新 GitHub 雲端同步設定（{'啟用' if saved_cfg['enabled'] else '停用'}：{saved_cfg['owner']}/{saved_cfg['repo']}）")
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data, trigger_cloud=saved_cfg["enabled"])
                payload = public_state(data, me)
            self.send_json(200, {
                "ok": True,
                "syncOk": ok,
                "syncMessage": msg,
                "data": payload,
            })
            return

        if parsed.path == "/api/settings/github/sync-now":
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not me or me.get("role") not in ("sysadmin", "pm", "member"):
                    self.send_json(403, {"ok": False, "error": "請先登入成員或管理員身分再手動觸發同步"})
                    return
            ok, msg = sync_with_github_once(force_check=True)
            with DATA_LOCK:
                data = load_data_unlocked()
                payload = public_state(data, self.resolve_me_unlocked(data))
            self.send_json(200, {"ok": True, "syncOk": ok, "syncMessage": msg, "data": payload})
            return

        if parsed.path == "/api/settings/view-pass":
            enabled = bool(body.get("viewPassEnabled", True))
            new_pw = str(body.get("newPassword") or "").strip()
            kick_others = bool(body.get("kickOthers", False))
            cur_vtok = self.get_view_token()
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not is_sysadmin(me):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」可修改觀看密碼設定"})
                    return
                if new_pw:
                    data["viewPassHash"] = hash_view_pass(new_pw)
                elif enabled and not str(data.get("viewPassHash") or "").strip():
                    self.send_json(400, {"ok": False, "error": "請輸入新的觀看密碼"})
                    return
                data["viewPassEnabled"] = enabled
                if enabled and (kick_others or new_pw):
                    VIEW_SESSIONS.clear()
                    if cur_vtok:
                        VIEW_SESSIONS.add(cur_vtok)
                status_txt = "啟用" if enabled else "停用"
                pw_txt = "並已變更觀看密碼" if new_pw else ""
                add_log(data, me["name"], f"更新觀看密碼保護（{status_txt}{pw_txt}）")
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, me)
            self.send_json(200, {"ok": True, "data": payload})
            return

        if parsed.path == "/api/import":
            imported = body.get("data")
            if not isinstance(imported, dict) or (
                not isinstance(imported.get("projects"), list)
                and not isinstance(imported.get("tasks"), list)
            ):
                self.send_json(400, {"ok": False, "error": "匯入檔案格式不符（需包含 projects 或 tasks）"})
                return
            with DATA_LOCK:
                data = load_data_unlocked()
                me = self.resolve_me_unlocked(data)
                if not is_sysadmin(me):
                    self.send_json(403, {"ok": False, "error": "僅「系統管理員」可以匯入還原系統備份"})
                    return
                save_snapshot_unlocked("before_import")
                migrated, _ = migrate_data_if_needed(dict(imported))
                data["projects"] = migrated["projects"]
                if body.get("includeMembers") and isinstance(migrated.get("members"), list):
                    data["members"] = migrated["members"]
                add_log(data, me["name"], f"匯入 JSON 備份（共 {len(data['projects'])} 個專案）")
                data["version"] = int(data.get("version", 1)) + 1
                save_data_unlocked(data)
                payload = public_state(data, self.resolve_me_unlocked(data))
            self.send_json(200, {"ok": True, "data": payload})
            return

        self.send_json(404, {"ok": False, "error": "未知路徑"})


HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script>
/* 主題初始套用：先於畫面渲染，避免淺色閃爍（網址加 ?theme=dark 可強制指定一次） */
(function(){try{var s=localStorage.getItem("pm_theme");var q=(location.search||"").match(/[?&]theme=(dark|light)/);if(q){s=q[1];}if(s!=="dark"&&s!=="light"){s=(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches)?"dark":"light";}if(s==="dark"){document.documentElement.setAttribute("data-theme","dark");}}catch(e){}})();
</script>

<title>團隊專案排程 (LAN)</title>
<style>
:root {
  --bg: #f4f6f9;
  --card: #ffffff;
  --border: #dfe4ec;
  --text: #1f2937;
  --muted: #6b7280;
  --primary: #2563eb;
  --primary-hover: #1d4ed8;
  --danger: #dc2626;
  --today: #ef4444;
  --milestone: #f59e0b;
  --milestone-dark: #b45309;
  --row-sub-bg: #fafcff;
  --soft-bg: #f8fafc;
  --row-h: 44px;
  --left-w: 390px;
}
* { box-sizing: border-box; }
body {
  margin: 0; font-family: "Microsoft JhengHei", "PingFang TC", "Segoe UI", system-ui, sans-serif;
  background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden;
}
header {
  background: var(--card); border-bottom: 1px solid var(--border);
  padding: 9px 14px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; z-index: 20;
}
.proj-bar { display: flex; align-items: center; gap: 6px; }
#projectSelect {
  font-weight: 700; font-size: 14px; padding: 6px 10px; max-width: 220px;
  border: 1px solid #cbd5e1; background: #f8fafc;
}
.sync-badge {
  font-size: 12px; padding: 3px 8px; border-radius: 99px; background: #dcfce7; color: #166534; font-weight: 600; white-space: nowrap;
}
.sync-badge.err { background: #fee2e2; color: #991b1b; }
.tabs { display: flex; background: #eef2f7; padding: 3px; border-radius: 8px; gap: 2px; }
.tab {
  border: none; background: transparent; padding: 6px 11px; border-radius: 6px;
  cursor: pointer; font-size: 13px; font-weight: 600; color: var(--muted);
}
.tab.active { background: var(--card); color: var(--primary); box-shadow: 0 1px 2px rgba(0,0,0,.08); }
.spacer { flex: 1; }
.controls { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
select, input[type="text"], input[type="password"], input[type="date"], input[type="datetime-local"], textarea {
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px;
  font-size: 13px; font-family: inherit; background: #fff; color: var(--text);
}
button {
  border: 1px solid var(--border); background: #fff; color: var(--text);
  border-radius: 6px; padding: 6px 10px; font-size: 13px; font-weight: 600; cursor: pointer;
}
button:hover:not(:disabled) { background: #f8fafc; }
button:disabled { opacity: .45; cursor: not-allowed; }
button.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
button.primary:hover:not(:disabled) { background: var(--primary-hover); }
button.danger { background: #fff; color: var(--danger); border-color: #fecaca; }
button.danger:hover:not(:disabled) { background: #fef2f2; }
button.tour-btn {
  background: #fffbeb; color: #b45309; border-color: #fcd34d;
}
button.tour-btn:hover { background: #fef3c7; }
button.tour-btn.pulse {
  animation: softPulse 2s infinite;
}
@keyframes softPulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.45); }
  50% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); }
}

/* Identity button & 4-tier role pills */
.identity-btn {
  display: inline-flex; align-items: center; gap: 6px;
  background: #f8fafc; border: 1px solid #cbd5e1; padding: 5px 10px;
}
.role-tag {
  font-size: 11px; padding: 1px 7px; border-radius: 99px; font-weight: 700; white-space: nowrap;
}
.role-sysadmin { background: #ede9fe; color: #6d28d9; }
.role-pm { background: #ccfbf1; color: #0f766e; }
.role-member { background: #dbeafe; color: #1d4ed8; }
.role-viewer { background: #f3f4f6; color: #4b5563; }

.readonly-banner {
  background: #fef3c7; color: #92400e; border-bottom: 1px solid #fde68a;
  padding: 6px 16px; font-size: 12px; display: none; align-items: center; gap: 10px;
}
.readonly-banner.show { display: flex; }

main { flex: 1; overflow: hidden; display: flex; flex-direction: column; }

/* Link chips & badges */
.ms-badge {
  display: inline-flex; align-items: center; gap: 2px;
  font-size: 11px; padding: 1px 6px; border-radius: 4px;
  background: #fef3c7; color: var(--milestone-dark); border: 1px solid #fcd34d; font-weight: 700; white-space: nowrap;
}
.link-chip {
  display: inline-flex; align-items: center; gap: 4px; max-width: 240px;
  font-size: 11px; padding: 2px 7px; border-radius: 5px;
  background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe;
  cursor: pointer; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.link-chip:hover { background: #dbeafe; }
.link-chip.path { background: #f3f4f6; color: #374151; border-color: #d1d5db; }
.link-chip.path:hover { background: #e5e7eb; }
.link-chip.share-dir { background: #f0fdfa; color: #0f766e; border-color: #99f6e4; }
.link-chip.share-dir:hover { background: #ccfbf1; }
.link-chip.share-file { background: #eef2ff; color: #3730a3; border-color: #c7d2fe; }
.link-chip.share-file:hover { background: #e0e7ff; }
.link-icon-btn {
  border: 1px solid #cbd5e1; background: #f8fafc; color: #334155;
  padding: 2px 6px; border-radius: 5px; font-size: 11px; cursor: pointer; white-space: nowrap;
}
.link-icon-btn:hover { background: #e2e8f0; }

/* Toast */
.toast {
  position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
  background: #111827; color: #fff; padding: 9px 18px; border-radius: 8px;
  font-size: 13px; z-index: 300; opacity: 0; pointer-events: none; transition: opacity .2s;
  box-shadow: 0 10px 25px rgba(0,0,0,.25);
}
.toast.show { opacity: 1; }

/* Gantt */
.gantt-toolbar {
  padding: 8px 16px; background: #f9fafb; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--muted); flex-wrap: wrap;
}
.pm-pill-info {
  font-size: 11px; padding: 2px 8px; border-radius: 99px;
  background: #f0fdfa; color: #0f766e; border: 1px solid #99f6e4; font-weight: 600;
}
.gantt-wrap { flex: 1; display: flex; overflow: hidden; background: var(--card); }
.gantt-left {
  width: var(--left-w); min-width: var(--left-w); border-right: 2px solid var(--border);
  display: flex; flex-direction: column; overflow: hidden;
}
.gantt-left-head {
  height: 50px; border-bottom: 1px solid var(--border); background: #f8fafc;
  display: flex; align-items: center; padding: 0 12px; font-weight: 700; font-size: 13px;
}
.gantt-left-body { flex: 1; overflow-y: hidden; }
.gantt-row-label {
  height: var(--row-h); border-bottom: 1px solid #eef2f6; padding: 0 10px;
  display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 13px;
}
.gantt-row-label:hover { background: #f8fafc; }
.gantt-row-title { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 500; }
.member-pill {
  font-size: 11px; padding: 2px 7px; border-radius: 99px; color: #fff; font-weight: 600; white-space: nowrap;
}
.gantt-right { flex: 1; overflow: auto; position: relative; }
.gantt-canvas { position: relative; min-height: 100%; }
.gantt-header {
  position: sticky; top: 0; z-index: 10; height: 50px; background: #f8fafc;
  border-bottom: 1px solid var(--border); display: flex; flex-direction: column;
}
.gantt-months { height: 22px; display: flex; border-bottom: 1px solid #e5e7eb; font-size: 11px; font-weight: 700; color: var(--muted); }
.gantt-month-cell { border-right: 1px solid var(--border); padding: 3px 6px; overflow: hidden; white-space: nowrap; }
.gantt-days { height: 28px; display: flex; }
.gantt-day-cell {
  border-right: 1px solid #eef2f6; font-size: 11px; display: flex; flex-direction: column;
  align-items: center; justify-content: center; color: var(--muted); user-select: none;
}
.gantt-day-cell.wknd { background: #f3f4f6; }
.gantt-day-cell.today { background: #fee2e2; color: #b91c1c; font-weight: 700; }
.gantt-grid { position: relative; min-height: 200px; }
.gantt-bg-layer { position: absolute; inset: 0; display: flex; pointer-events: none; z-index: 1; }
.gantt-grid-row { height: var(--row-h); border-bottom: 1px solid #eef2f6; position: relative; z-index: 2; }
.gantt-bg-col { border-right: 1px solid #f1f5f9; height: 100%; flex-shrink: 0; }
.gantt-bg-col.wknd { background: #f8fafc; }
.gantt-bg-col.offhour { background: #f8fafc; }
.gantt-bg-col.day-sep { border-right: 1px solid #cbd5e1; }
.zoom-group { display: inline-flex; background: #e2e8f0; padding: 2px; border-radius: 6px; gap: 2px; }
.zoom-btn { border: none; background: transparent; padding: 3px 10px; border-radius: 5px; font-size: 12px; font-weight: 700; color: #475569; cursor: pointer; }
.zoom-btn:hover { background: rgba(255,255,255,.65); }
.zoom-btn.active { background: #fff; color: var(--primary); box-shadow: 0 1px 2px rgba(0,0,0,.1); }
.today-line {
  position: absolute; top: 0; bottom: 0; width: 2px; background: var(--today); z-index: 4; pointer-events: none;
}
.gantt-svg { position: absolute; top: 0; left: 0; pointer-events: none; z-index: 5; }
.gantt-bar {
  position: absolute; top: 8px; height: 28px; border-radius: 6px; color: #fff;
  font-size: 12px; font-weight: 600; display: flex; align-items: center;
  padding: 0 10px; cursor: grab; user-select: none; z-index: 6;
  box-shadow: 0 1px 3px rgba(0,0,0,.18); overflow: hidden;
}
.gantt-bar.locked { cursor: pointer; opacity: .88; }
.gantt-bar:active:not(.locked) { cursor: grabbing; }
.gantt-bar-prog {
  position: absolute; left: 0; top: 0; bottom: 0; background: rgba(0,0,0,.22); pointer-events: none;
}
.gantt-bar-text { position: relative; z-index: 2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.resize-handle { position: absolute; top: 0; bottom: 0; width: 8px; cursor: ew-resize; z-index: 7; }
.gantt-bar.locked .resize-handle { display: none; }
.resize-l { left: 0; }
.resize-r { right: 0; }

/* Milestone Diamond */
.gantt-ms-wrap {
  position: absolute; top: 0; height: var(--row-h); display: flex; align-items: center;
  cursor: grab; user-select: none; z-index: 7;
}
.gantt-ms-wrap.locked { cursor: pointer; }
.gantt-ms-wrap:active:not(.locked) { cursor: grabbing; }
.gantt-ms-diamond {
  width: 18px; height: 18px; background: var(--milestone); border: 2px solid var(--milestone-dark);
  transform: rotate(45deg); border-radius: 2px; flex-shrink: 0;
  box-shadow: 0 1px 4px rgba(180,83,9,.35);
}
.gantt-ms-diamond.done { background: #10b981; border-color: #047857; }
.gantt-ms-label {
  margin-left: 10px; font-size: 12px; font-weight: 700; color: var(--milestone-dark);
  background: rgba(255,251,235,.92); padding: 2px 7px; border-radius: 4px;
  border: 1px solid #fde68a; white-space: nowrap;
}
.drag-tip {
  position: fixed; background: #111827; color: #fff; padding: 4px 8px; border-radius: 4px;
  font-size: 12px; pointer-events: none; z-index: 100; display: none;
}

/* Kanban */
.kanban-wrap { flex: 1; overflow-x: auto; padding: 16px; display: flex; gap: 14px; align-items: flex-start; }
.kanban-col {
  width: 300px; min-width: 300px; background: #ebeff5; border-radius: 10px;
  padding: 10px; max-height: 100%; display: flex; flex-direction: column;
}
.kanban-col.drag-over { background: #dbeafe; outline: 2px dashed var(--primary); }
.kanban-head { font-weight: 700; font-size: 14px; margin-bottom: 10px; display: flex; justify-content: space-between; }
.kanban-cards { overflow-y: auto; display: flex; flex-direction: column; gap: 8px; min-height: 60px; }
.k-card {
  background: #fff; border-radius: 8px; padding: 10px; box-shadow: 0 1px 2px rgba(0,0,0,.08);
  cursor: pointer; border-left: 4px solid var(--primary);
}
.k-card.milestone { border-left-color: var(--milestone); background: #fffbeb; }
.k-card-title { font-weight: 600; font-size: 14px; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.k-card-meta { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--muted); }
.k-card-links { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 7px; }
.prog-mini { height: 5px; background: #e5e7eb; border-radius: 99px; margin-top: 8px; overflow: hidden; }
.prog-mini div { height: 100%; background: #10b981; }

/* List & Logs */
.panel-wrap { flex: 1; overflow: auto; padding: 16px; }
table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; font-size: 13px; vertical-align: middle; }
th { background: #f8fafc; font-weight: 700; color: var(--muted); }
.log-item { background: #fff; padding: 10px 14px; border-radius: 8px; margin-bottom: 8px; border: 1px solid var(--border); font-size: 13px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.log-time { color: var(--muted); font-family: monospace; white-space: nowrap; }
.log-proj { font-size: 11px; padding: 2px 7px; border-radius: 4px; background: #f1f5f9; color: #475569; font-weight: 600; }

/* Modals */
.modal-bg {
  position: fixed; inset: 0; background: rgba(15,23,42,.5); display: none;
  align-items: center; justify-content: center; z-index: 60; padding: 16px;
}
.modal-bg.open { display: flex; }
.modal {
  background: #fff; border-radius: 12px; width: 100%; max-width: 580px; padding: 20px;
  box-shadow: 0 20px 40px rgba(0,0,0,.25); max-height: 92vh; overflow-y: auto;
}
.modal.wide { max-width: 760px; }
.modal h3 { margin: 0 0 12px; font-size: 17px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-row { display: flex; flex-direction: column; gap: 4px; }
.form-row.full { grid-column: 1 / -1; }
.form-row label { font-size: 12px; font-weight: 700; color: var(--muted); }
.check-box-row {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; font-size: 13px; font-weight: 600; color: var(--milestone-dark);
}
.info-note {
  padding: 9px 12px; border-radius: 8px; background: #f1f5f9; color: #334155; font-size: 12px; line-height: 1.55;
}
.link-preview-box { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.modal-actions { display: flex; justify-content: space-between; margin-top: 16px; gap: 8px; align-items: center; }

/* Login member cards */
.member-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin: 12px 0; }
.member-card {
  border: 2px solid var(--border); border-radius: 10px; padding: 12px; cursor: pointer;
  display: flex; flex-direction: column; gap: 6px; transition: .15s; background: #fff;
}
.member-card:hover { border-color: #93c5fd; background: #f8fafc; }
.member-card.selected { border-color: var(--primary); background: #eff6ff; }
.member-card-name { font-weight: 700; font-size: 15px; display: flex; align-items: center; justify-content: space-between; }
.pin-box {
  background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 12px;
  margin-top: 10px; display: none;
}
.pin-box.show { display: block; }

/* Settings tabs & Member permission table */
.sub-tabs { display: flex; gap: 6px; border-bottom: 1px solid var(--border); margin-bottom: 14px; padding-bottom: 8px; flex-wrap: wrap; }
.sub-tab { border: none; background: transparent; padding: 6px 12px; border-radius: 6px; font-weight: 700; color: var(--muted); cursor: pointer; }
.sub-tab.active { background: #eff6ff; color: var(--primary); }
.perm-row {
  display: grid; grid-template-columns: 1.15fr 1.15fr 1.2fr auto; gap: 8px; align-items: center;
  padding: 8px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; background: #fafafa;
}
.perm-pin-cell { display: flex; gap: 4px; align-items: center; }
.perm-pin-cell input { width: 95px; padding: 5px 7px; font-size: 12px; }
.pm-check-list {
  display: flex; flex-wrap: wrap; gap: 8px; padding: 10px;
  border: 1px solid var(--border); border-radius: 8px; background: #f8fafc;
}
.pm-check-item {
  display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px;
  background: #fff; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 13px; cursor: pointer;
}

/* Shared Folder Browser Modal */
.fs-toolbar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 10px; padding: 10px; background: #f8fafc;
  border: 1px solid var(--border); border-radius: 8px;
}
.fs-crumb-bar {
  display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
  font-size: 12.5px; margin-bottom: 8px; padding: 6px 10px;
  background: #f1f5f9; border-radius: 6px;
}
.fs-crumb {
  border: none; background: transparent; color: var(--primary);
  padding: 2px 6px; font-weight: 700; cursor: pointer; font-size: 12.5px;
}
.fs-crumb:hover { background: #dbeafe; border-radius: 4px; }
.fs-realpath-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  font-size: 11.5px; color: var(--muted); margin-bottom: 8px;
  padding: 4px 8px; background: #fafafa; border: 1px dashed var(--border); border-radius: 6px;
}
.fs-realpath-text {
  font-family: monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1;
}
.fs-list-wrap {
  border: 1px solid var(--border); border-radius: 8px; max-height: 360px; overflow-y: auto; background: #fff;
}
.fs-item {
  display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  border-bottom: 1px solid #f1f5f9; font-size: 13px;
}
.fs-item:last-child { border-bottom: none; }
.fs-item:hover { background: #f8fafc; }
.fs-item-main {
  flex: 1; display: flex; align-items: center; gap: 8px; min-width: 0; cursor: pointer;
}
.fs-item-name {
  font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.fs-item-meta {
  font-size: 11.5px; color: var(--muted); white-space: nowrap;
}
.fs-item-acts { display: flex; gap: 6px; flex-shrink: 0; }
.root-row {
  display: grid; grid-template-columns: 1fr 1.8fr auto auto; gap: 8px; align-items: center;
  padding: 8px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; background: #fafafa;
}
.root-status { font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 99px; white-space: nowrap; }
.root-status.ok { background: #dcfce7; color: #166534; }
.root-status.bad { background: #fee2e2; color: #991b1b; }

/* Color Legend Bar & Semantic Badges */
.legend-bar {
  background: #f8fafc; border-bottom: 1px solid var(--border);
  padding: 5px 14px; display: flex; align-items: center; gap: 8px;
  flex-wrap: wrap; font-size: 12px; z-index: 15;
}
.legend-title { font-weight: 700; color: var(--muted); white-space: nowrap; }
.legend-chip {
  display: inline-flex; align-items: center; gap: 5px; padding: 2px 9px;
  border-radius: 99px; font-weight: 700; font-size: 11.5px; cursor: pointer;
  border: 1px solid rgba(0,0,0,.08); transition: transform .12s;
}
.legend-chip:hover { transform: translateY(-1px); }
.color-pill {
  font-size: 11px; padding: 2px 7px; border-radius: 99px; font-weight: 700;
  white-space: nowrap; display: inline-flex; align-items: center; gap: 3px;
}

/* Subtask Tree & Expand/Collapse */
.tree-toggle {
  width: 20px; height: 20px; padding: 0; border-radius: 4px; flex-shrink: 0;
  border: 1px solid #cbd5e1; background: #f8fafc; color: #334155;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; cursor: pointer; line-height: 1;
}
.tree-toggle:hover { background: #e2e8f0; }
.tree-spacer { width: 20px; flex-shrink: 0; display: inline-block; text-align: center; color: #94a3b8; font-size: 11px; }
.sub-count-badge {
  font-size: 10.5px; padding: 1px 6px; border-radius: 99px;
  background: #e2e8f0; color: #334155; font-weight: 700; white-space: nowrap;
}
.sub-add-btn {
  border: 1px dashed #cbd5e1; background: transparent; color: #64748b;
  padding: 1px 5px; border-radius: 4px; font-size: 11px; cursor: pointer;
  opacity: 0; transition: opacity .12s;
}
.gantt-row-label:hover .sub-add-btn, tr:hover .sub-add-btn { opacity: 1; }
.sub-add-btn:hover { background: #eff6ff; color: var(--primary); border-color: #93c5fd; }
.gantt-row-label.is-parent .gantt-row-title { font-weight: 700; color: #0f172a; }
.gantt-row-label.is-subtask { background: #fcfdff; }
.gantt-bar.is-parent-bar { box-shadow: 0 2px 5px rgba(0,0,0,.22), inset 0 2px 0 rgba(255,255,255,.35); }
.gantt-bar.is-sub-bar { top: 10px; height: 24px; font-size: 11.5px; }

/* Actual Completion Date & Variance Markers on Gantt */
.gantt-actual-late {
  position: absolute; top: 11px; height: 22px; z-index: 5; pointer-events: none;
  background: repeating-linear-gradient(-45deg, rgba(239,68,68,.28), rgba(239,68,68,.28) 5px, rgba(239,68,68,.12) 5px, rgba(239,68,68,.12) 10px);
  border: 1.5px dashed #dc2626; border-left: none; border-radius: 0 6px 6px 0;
  display: flex; align-items: center; justify-content: flex-end; padding-right: 5px;
  font-size: 10.5px; font-weight: 800; color: #991b1b; white-space: nowrap;
}
.gantt-actual-overdue {
  position: absolute; top: 12px; height: 20px; z-index: 4; pointer-events: none;
  background: repeating-linear-gradient(-45deg, rgba(220,38,38,.18), rgba(220,38,38,.18) 4px, transparent 4px, transparent 8px);
  border: 1px dashed #ef4444; border-left: none; border-radius: 0 5px 5px 0;
  display: flex; align-items: center; justify-content: flex-end; padding-right: 4px;
  font-size: 10px; font-weight: 700; color: #b91c1c; white-space: nowrap;
}
.gantt-actual-flag {
  position: absolute; top: 4px; height: 36px; width: 2px; z-index: 8; pointer-events: none;
}
.gantt-actual-flag-tag {
  position: absolute; top: -2px; left: 4px; font-size: 10px; font-weight: 800;
  padding: 1px 5px; border-radius: 4px; white-space: nowrap; box-shadow: 0 1px 3px rgba(0,0,0,.18);
}

/* Schedule Analysis Dashboard */
.kpi-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
  gap: 12px; margin-bottom: 16px;
}
.kpi-card {
  background: #fff; border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 16px; box-shadow: 0 1px 2px rgba(0,0,0,.04);
}
.kpi-label { font-size: 12px; color: var(--muted); font-weight: 700; margin-bottom: 6px; }
.kpi-val { font-size: 24px; font-weight: 800; color: #0f172a; line-height: 1.2; }
.kpi-sub { font-size: 11.5px; color: var(--muted); margin-top: 4px; }
.analysis-section { margin-bottom: 18px; }
.analysis-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  margin-bottom: 8px; flex-wrap: wrap;
}
.analysis-head h4 { margin: 0; font-size: 15px; color: #0f172a; }
.filter-pills { display: flex; gap: 6px; flex-wrap: wrap; }
.filter-pill {
  padding: 4px 10px; border-radius: 99px; font-size: 12px; font-weight: 700;
  border: 1px solid var(--border); background: #fff; color: var(--muted); cursor: pointer;
}
.filter-pill.active { background: var(--primary); color: #fff; border-color: var(--primary); }
.var-bar-wrap {
  display: inline-flex; align-items: center; gap: 6px; min-width: 115px;
}
.var-bar-track {
  width: 64px; height: 7px; background: #e2e8f0; border-radius: 99px; overflow: hidden;
}
.var-bar-fill { height: 100%; border-radius: 99px; }

/* Color Customizer Modal */
.color-cfg-group {
  border: 1px solid var(--border); border-radius: 10px; padding: 12px;
  margin-bottom: 12px; background: #fafafa;
}
.color-cfg-title {
  font-size: 13px; font-weight: 800; margin: 0 0 10px; color: #0f172a;
  display: flex; align-items: center; justify-content: space-between;
}
.color-cfg-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 8px;
}
.color-cfg-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px;
}
.color-cfg-item input[type="color"] {
  width: 32px; height: 26px; border: none; padding: 0; background: transparent; cursor: pointer;
}

/* Holiday Bands & Split Task Bridges on Gantt */
.gantt-holiday-layer {
  position: absolute; inset: 0; pointer-events: none; z-index: 2;
}
.gantt-holiday-band {
  position: absolute; top: 0; bottom: 0;
  background: repeating-linear-gradient(
    -45deg,
    rgba(244, 63, 94, 0.11),
    rgba(244, 63, 94, 0.11) 6px,
    rgba(254, 226, 226, 0.22) 6px,
    rgba(254, 226, 226, 0.22) 12px
  );
  border-left: 1px dashed rgba(225, 29, 72, 0.45);
  border-right: 1px dashed rgba(225, 29, 72, 0.45);
}
.gantt-holiday-tag {
  position: sticky; top: 52px; display: inline-block; margin: 2px 3px;
  padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 700;
  background: rgba(255, 241, 242, 0.94); color: #be123c;
  border: 1px solid #fecdd3; white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis;
}
.gantt-split-bridge {
  position: absolute; top: 20px; height: 4px; z-index: 5; pointer-events: none;
  border-top: 2px dashed rgba(100, 116, 139, 0.85);
}
.gantt-split-bridge-tag {
  position: absolute; top: -15px; left: 50%; transform: translateX(-50%);
  font-size: 9.5px; font-weight: 700; color: #be123c;
  background: rgba(255, 241, 242, 0.92); padding: 0 5px; border-radius: 99px;
  border: 1px solid #fecdd3; white-space: nowrap;
}
.holiday-badge {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 10.5px; padding: 1px 6px; border-radius: 99px;
  background: #fff1f2; color: #be123c; border: 1px solid #fecdd3; font-weight: 700; white-space: nowrap;
}
.holiday-alert-box {
  padding: 10px 12px; border-radius: 8px; background: #fff1f2;
  border: 1px solid #fecdd3; color: #9f1239; font-size: 12.5px;
  display: none; flex-direction: column; gap: 8px;
}
.holiday-alert-box.show { display: flex; }
.holiday-row {
  display: grid; grid-template-columns: 1.5fr 1fr 1fr auto auto; gap: 8px; align-items: center;
  padding: 8px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; background: #fafafa;
}
.holiday-impact-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap;
  padding: 8px 10px; border: 1px solid #fecdd3; border-radius: 8px; background: #fff; margin-bottom: 6px; font-size: 12.5px;
}

/* Full-Screen Viewing Password Lock Gate */
.view-lock-overlay {
  position: fixed; inset: 0; z-index: 500;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 55%, #0f172a 100%);
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.view-lock-overlay.hidden { display: none; }
.view-lock-card {
  width: 100%; max-width: 400px; background: #ffffff; border-radius: 16px;
  padding: 28px 26px; box-shadow: 0 25px 60px rgba(0,0,0,.45); text-align: center;
}
.view-lock-icon {
  width: 56px; height: 56px; margin: 0 auto 14px; border-radius: 14px;
  background: #eff6ff; color: var(--primary); font-size: 28px;
  display: flex; align-items: center; justify-content: center;
}
.view-lock-card h2 { margin: 0 0 8px; font-size: 20px; color: #0f172a; }
.view-lock-desc { margin: 0 0 18px; font-size: 13px; color: var(--muted); line-height: 1.6; }
.view-lock-form { display: flex; flex-direction: column; gap: 10px; }
.view-lock-form input {
  width: 100%; padding: 11px 12px; font-size: 15px; text-align: center; letter-spacing: 2px;
  border: 1.5px solid #cbd5e1; border-radius: 8px;
}
.view-lock-form input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37,99,235,.15); }
.view-lock-err {
  min-height: 18px; font-size: 12.5px; color: var(--danger); font-weight: 700;
}
.view-lock-foot {
  margin-top: 14px; font-size: 11.5px; color: #94a3b8; line-height: 1.5;
}

/* Built-in Interactive Spotlight Tour */
.tour-mask {
  position: fixed; background: rgba(15, 23, 42, 0.58); z-index: 200; display: none;
  transition: all .2s ease;
}
.tour-spot {
  position: fixed; z-index: 205; display: none; pointer-events: none;
  border: 3px solid #f59e0b; border-radius: 10px;
  box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.28), 0 0 24px rgba(245, 158, 11, 0.45);
  transition: all .22s ease;
}
.tour-spot-num {
  position: absolute; top: -14px; left: -14px; width: 28px; height: 28px;
  border-radius: 50%; background: #f59e0b; color: #fff; font-weight: 800; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 3px 10px rgba(0,0,0,.3);
}
.tour-card {
  position: fixed; z-index: 210; display: none; width: 380px; max-width: calc(100vw - 24px);
  background: #fff; border-radius: 14px; padding: 16px 18px;
  box-shadow: 0 22px 50px rgba(15, 23, 42, .35); border: 1px solid #e2e8f0;
}
.tour-dots { display: flex; gap: 5px; margin-bottom: 10px; flex-wrap: wrap; }
.tour-dot {
  width: 26px; height: 26px; border-radius: 50%; border: 1px solid #cbd5e1;
  background: #f8fafc; color: #475569; font-size: 12px; font-weight: 700;
  cursor: pointer; padding: 0; display: flex; align-items: center; justify-content: center;
}
.tour-dot.active { background: var(--primary); color: #fff; border-color: var(--primary); }
.tour-title { font-size: 16px; font-weight: 800; margin: 0 0 8px; color: #0f172a; }
.tour-body { font-size: 13.5px; line-height: 1.65; color: #334155; margin: 0 0 10px; }
.tour-tip {
  background: #fffbeb; border-left: 3px solid #f59e0b; padding: 8px 10px; border-radius: 6px;
  font-size: 12.5px; color: #92400e; line-height: 1.55; margin-bottom: 14px;
}
.tour-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
/* ==================== 主題切換：淺色基準元件 ==================== */
.quick-code-box { background: #f0fdfa; border: 1px solid #99f6e4; border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; }
.theme-toggle-btn { white-space: nowrap; }

/* ==================== 深色主題（html[data-theme="dark"]） ==================== */
html[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0d1320;
  --card: #151d2b;
  --border: #2b3749;
  --text: #e6ecf6;
  --muted: #93a2b8;
  --primary: #3b82f6;
  --primary-hover: #60a5fa;
  --danger: #f87171;
  --today: #fb7185;
  --milestone: #fbbf24;
  --milestone-dark: #fcd34d;
  --row-sub-bg: #121a27;
  --soft-bg: #131b28;
}
html[data-theme="dark"] body { background: var(--bg); color: var(--text); }
html[data-theme="dark"] header { background: #111826; border-bottom-color: var(--border); }

/* --- 表單元件 --- */
html[data-theme="dark"] select,
html[data-theme="dark"] input[type="text"],
html[data-theme="dark"] input[type="password"],
html[data-theme="dark"] input[type="date"],
html[data-theme="dark"] input[type="datetime-local"],
html[data-theme="dark"] input[type="number"],
html[data-theme="dark"] input[type="search"],
html[data-theme="dark"] textarea {
  background: #0e1522; color: var(--text); border-color: var(--border);
}
html[data-theme="dark"] select option { background: #0e1522; color: var(--text); }
html[data-theme="dark"] #projectSelect { background: #1a2434; border-color: var(--border); }

/* --- 按鈕（先一般、後特定，避免蓋掉透明樣式） --- */
html[data-theme="dark"] button { background: #1c2637; color: var(--text); border-color: var(--border); }
html[data-theme="dark"] button:hover:not(:disabled) { background: #263244; }
html[data-theme="dark"] button.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
html[data-theme="dark"] button.primary:hover:not(:disabled) { background: var(--primary-hover); }
html[data-theme="dark"] button.danger { background: #2a1418; color: #fca5a5; border-color: #7f1d1d; }
html[data-theme="dark"] button.danger:hover:not(:disabled) { background: #3a1a20; }
html[data-theme="dark"] button.tour-btn { background: #3a2a0a; color: #fcd34d; border-color: #78500f; }
html[data-theme="dark"] button.tour-btn:hover:not(:disabled) { background: #4a360c; }
html[data-theme="dark"] .tabs { background: #18212f; }
html[data-theme="dark"] .tab { background: transparent; color: var(--muted); }
html[data-theme="dark"] .tab.active { background: #26344a; color: #93c5fd; box-shadow: none; }
html[data-theme="dark"] .identity-btn { background: #1a2434; border-color: var(--border); }
html[data-theme="dark"] .zoom-group { background: #18212f; }
html[data-theme="dark"] .zoom-btn { background: transparent; color: var(--muted); }
html[data-theme="dark"] .zoom-btn:hover { background: #26344a; }
html[data-theme="dark"] .zoom-btn.active { background: #2f4059; color: #93c5fd; box-shadow: none; }
html[data-theme="dark"] .sub-tab { background: transparent; color: var(--muted); }
html[data-theme="dark"] .sub-tab.active { background: #1e2f4d; color: #93c5fd; }
html[data-theme="dark"] .fs-crumb { background: transparent; color: #93c5fd; }
html[data-theme="dark"] .fs-crumb:hover { background: #1e3358; }
html[data-theme="dark"] .tour-dot { background: #1c2637; border-color: var(--border); color: #cbd5e1; }
html[data-theme="dark"] .tour-dot.active { background: var(--primary); color: #fff; border-color: var(--primary); }
html[data-theme="dark"] .tree-toggle { background: #1c2637; border-color: var(--border); color: #cbd5e1; }
html[data-theme="dark"] .tree-toggle:hover { background: #263244; }
html[data-theme="dark"] .sub-add-btn { background: transparent; border-color: #38455c; color: var(--muted); }
html[data-theme="dark"] .sub-add-btn:hover { background: #1e3358; color: #93c5fd; border-color: #2b4c7a; }
html[data-theme="dark"] .link-icon-btn { background: #1c2637; border-color: var(--border); color: #cbd5e1; }
html[data-theme="dark"] .link-icon-btn:hover { background: #263244; }
html[data-theme="dark"] .filter-pill { background: #1c2637; border-color: var(--border); color: var(--muted); }
html[data-theme="dark"] .filter-pill.active { background: var(--primary); color: #fff; border-color: var(--primary); }

/* --- 角色標籤與狀態徽章 --- */
html[data-theme="dark"] .role-sysadmin { background: #2e1a5e; color: #c4b5fd; }
html[data-theme="dark"] .role-pm { background: #0b3f37; color: #5eead4; }
html[data-theme="dark"] .role-member { background: #16305c; color: #93c5fd; }
html[data-theme="dark"] .role-viewer { background: #232d3d; color: #cbd5e1; }
html[data-theme="dark"] .sync-badge { background: #0b3f2a; color: #6ee7b7; }
html[data-theme="dark"] .sync-badge.err { background: #431a1a; color: #fca5a5; }
html[data-theme="dark"] .readonly-banner { background: #3f2c08; color: #fcd34d; border-bottom-color: #78500f; }

/* --- 連結／里程碑／共用檔案標籤 --- */
html[data-theme="dark"] .ms-badge { background: #3f2c08; color: #fcd34d; border-color: #78500f; }
html[data-theme="dark"] .link-chip { background: #15294a; color: #93c5fd; border-color: #1e3f6b; }
html[data-theme="dark"] .link-chip:hover { background: #1b3559; }
html[data-theme="dark"] .link-chip.path { background: #232d3d; color: #cbd5e1; border-color: #38455c; }
html[data-theme="dark"] .link-chip.path:hover { background: #2b3749; }
html[data-theme="dark"] .link-chip.share-dir { background: #0b3b36; color: #5eead4; border-color: #134e4a; }
html[data-theme="dark"] .link-chip.share-dir:hover { background: #0f4a44; }
html[data-theme="dark"] .link-chip.share-file { background: #1d2447; color: #a5b4fc; border-color: #3730a3; }
html[data-theme="dark"] .link-chip.share-file:hover { background: #262f5c; }
html[data-theme="dark"] .pm-pill-info { background: #0b3b36; color: #5eead4; border-color: #134e4a; }
html[data-theme="dark"] .sub-count-badge { background: #2b3749; color: #cbd5e1; }

/* --- 甘特圖 --- */
html[data-theme="dark"] .gantt-toolbar { background: #121a27; border-bottom-color: var(--border); }
html[data-theme="dark"] .gantt-left-head { background: #131c2a; border-bottom-color: var(--border); }
html[data-theme="dark"] .gantt-row-label { border-bottom-color: #202a3a; }
html[data-theme="dark"] .gantt-row-label:hover { background: #1a2434; }
html[data-theme="dark"] .gantt-row-label.is-parent .gantt-row-title { color: var(--text); }
html[data-theme="dark"] .gantt-row-label.is-subtask { background: #121a27; }
html[data-theme="dark"] .gantt-header { background: #131c2a; border-bottom-color: var(--border); }
html[data-theme="dark"] .gantt-months { border-bottom-color: var(--border); }
html[data-theme="dark"] .gantt-day-cell { border-right-color: #202a3a; }
html[data-theme="dark"] .gantt-day-cell.wknd { background: #18212f; }
html[data-theme="dark"] .gantt-day-cell.today { background: #4a1d24; color: #fda4af; }
html[data-theme="dark"] .gantt-grid-row { border-bottom-color: #202a3a; }
html[data-theme="dark"] .gantt-bg-col { border-right-color: #1b2433; }
html[data-theme="dark"] .gantt-bg-col.wknd,
html[data-theme="dark"] .gantt-bg-col.offhour { background: #161f2d; }
html[data-theme="dark"] .gantt-bg-col.day-sep { border-right-color: #38455c; }
html[data-theme="dark"] .gantt-ms-label { background: rgba(58,44,12,.92); color: #fcd34d; border-color: #78500f; }
html[data-theme="dark"] .gantt-actual-late { color: #fda4af; border-color: #f87171; }
html[data-theme="dark"] .gantt-actual-overdue { color: #fda4af; border-color: #ef4444; }

/* --- 看板 --- */
html[data-theme="dark"] .kanban-col { background: #121a27; }
html[data-theme="dark"] .kanban-col.drag-over { background: #16305c; }
html[data-theme="dark"] .k-card { background: #1a2434; box-shadow: 0 1px 3px rgba(0,0,0,.5); }
html[data-theme="dark"] .k-card.milestone { background: #3a2d0c; }
html[data-theme="dark"] .prog-mini { background: #2b3749; }

/* --- 清單／表格／紀錄 --- */
html[data-theme="dark"] table { background: #151d2b; box-shadow: none; }
html[data-theme="dark"] th { background: #1a2434; }
html[data-theme="dark"] th, html[data-theme="dark"] td { border-bottom-color: var(--border); }
html[data-theme="dark"] .log-item { background: #151d2b; border-color: var(--border); }
html[data-theme="dark"] .log-proj { background: #232d3d; color: #cbd5e1; }

/* --- 彈出視窗／表單 --- */
html[data-theme="dark"] .modal { background: #151d2b; box-shadow: 0 20px 40px rgba(0,0,0,.55); }
html[data-theme="dark"] .info-note { background: #1b2536; color: #b6c2d4; }
html[data-theme="dark"] .check-box-row { background: #3f2c08; border-color: #78500f; color: #fcd34d; }
html[data-theme="dark"] .member-card { background: #1a2434; border-color: var(--border); }
html[data-theme="dark"] .member-card:hover { border-color: #3b82f6; background: #1e2a3d; }
html[data-theme="dark"] .member-card.selected { border-color: var(--primary); background: #16305c; }
html[data-theme="dark"] .pin-box { background: #131b28; border-color: var(--border); }
html[data-theme="dark"] .perm-row { background: #131b28; border-color: var(--border); }
html[data-theme="dark"] .pm-check-list { background: #131b28; border-color: var(--border); }
html[data-theme="dark"] .pm-check-item { background: #1a2434; border-color: var(--border); }

/* --- 共用資料夾瀏覽器 --- */
html[data-theme="dark"] .fs-toolbar { background: #131b28; border-color: var(--border); }
html[data-theme="dark"] .fs-crumb-bar { background: #1b2536; }
html[data-theme="dark"] .fs-realpath-bar { background: #131b28; border-color: var(--border); }
html[data-theme="dark"] .fs-list-wrap { background: #151d2b; border-color: var(--border); }
html[data-theme="dark"] .fs-item { border-bottom-color: #202a3a; }
html[data-theme="dark"] .fs-item:hover { background: #1a2434; }
html[data-theme="dark"] .root-row { background: #131b28; border-color: var(--border); }
html[data-theme="dark"] .root-status.ok { background: #0b3f2a; color: #6ee7b7; }
html[data-theme="dark"] .root-status.bad { background: #431a1a; color: #fca5a5; }
html[data-theme="dark"] .root-status[style] { background: #232d3d !important; color: #cbd5e1 !important; }

/* --- 圖例條 --- */
html[data-theme="dark"] .legend-bar { background: #121a27; border-bottom-color: var(--border); }

/* --- 進度分析 --- */
html[data-theme="dark"] .kpi-card { background: #151d2b; border-color: var(--border); box-shadow: none; }
html[data-theme="dark"] .kpi-val { color: var(--text); }
html[data-theme="dark"] .analysis-head h4 { color: var(--text); }
html[data-theme="dark"] .var-bar-track { background: #2b3749; }

/* --- 調色盤 --- */
html[data-theme="dark"] .color-cfg-group { background: #131b28; border-color: var(--border); }
html[data-theme="dark"] .color-cfg-title { color: var(--text); }
html[data-theme="dark"] .color-cfg-item { background: #1a2434; border-color: var(--border); }

/* --- 休假日 --- */
html[data-theme="dark"] .gantt-holiday-tag { background: rgba(74,29,36,.94); color: #fda4af; border-color: #7f1d3a; }
html[data-theme="dark"] .gantt-split-bridge-tag { background: rgba(74,29,36,.92); color: #fda4af; border-color: #7f1d3a; }
html[data-theme="dark"] .holiday-badge { background: #3a1520; color: #fda4af; border-color: #7f1d3a; }
html[data-theme="dark"] .holiday-alert-box { background: #3a1520; border-color: #7f1d3a; color: #fda4af; }
html[data-theme="dark"] .holiday-row { background: #131b28; border-color: var(--border); }
html[data-theme="dark"] .holiday-impact-row { background: #161f2e; border-color: #7f1d3a; }

/* --- 觀看密碼鎖定畫面 --- */
html[data-theme="dark"] .view-lock-card { background: #151d2b; }
html[data-theme="dark"] .view-lock-card h2 { color: var(--text); }
html[data-theme="dark"] .view-lock-icon { background: #17284a; color: #93c5fd; }
html[data-theme="dark"] .view-lock-form input { background: #0e1522; color: var(--text); border-color: var(--border); }
html[data-theme="dark"] .view-lock-foot { color: #8b9bb1; }

/* --- 操作教學 --- */
html[data-theme="dark"] .tour-card { background: #151d2b; border-color: var(--border); box-shadow: 0 22px 50px rgba(0,0,0,.6); }
html[data-theme="dark"] .tour-title { color: var(--text); }
html[data-theme="dark"] .tour-body { color: #c3cede; }
html[data-theme="dark"] .tour-tip { background: #3f2c08; color: #fcd34d; }

/* --- 內嵌淺色區塊（inline style 需 !important） --- */
html[data-theme="dark"] #taskReadonlyHint { background: #3f2c08 !important; color: #fcd34d !important; }
html[data-theme="dark"] #splitHolidayWrap { background: #3a1520 !important; border-color: #7f1d3a !important; color: #fda4af !important; }
html[data-theme="dark"] #fVariancePreview { background: #131b28 !important; }
html[data-theme="dark"] #ghStatusBox { background: #131b28 !important; border-color: var(--border) !important; }
html[data-theme="dark"] #btnExtendForHolidays { background: #0b3b36 !important; color: #5eead4 !important; border-color: #134e4a !important; }
html[data-theme="dark"] #btnPresetTW2027 { background: #3a1520 !important; color: #fda4af !important; border-color: #7f1d3a !important; }
html[data-theme="dark"] #btnCopyCloudInvite { background: #0b3b36 !important; color: #5eead4 !important; border-color: #134e4a !important; }

/* --- GitHub 連線畫面的快速連線碼區塊（雲端版） --- */
html[data-theme="dark"] .quick-code-box { background: #0b3b36; border-color: #134e4a; }
html[data-theme="dark"] .quick-code-box label { color: #5eead4 !important; }
html[data-theme="dark"] #btnApplyQuickCode { background: #0f766e; color: #fff; border-color: #0f766e; }
html[data-theme="dark"] #themeToggleBtn { background: #1c2637; color: #fcd34d; border-color: var(--border); }
/* --- 捲軸與其他小晶片 --- */
html[data-theme="dark"] { scrollbar-color: #3a4a63 #0f1623; }
html[data-theme="dark"] ::-webkit-scrollbar { width: 11px; height: 11px; }
html[data-theme="dark"] ::-webkit-scrollbar-track { background: #0f1623; }
html[data-theme="dark"] ::-webkit-scrollbar-thumb { background: #34425a; border-radius: 6px; border: 2px solid #0f1623; }
html[data-theme="dark"] ::-webkit-scrollbar-thumb:hover { background: #44577a; }
html[data-theme="dark"] ::-webkit-scrollbar-corner { background: #0f1623; }
html[data-theme="dark"] #btnPickSharedFile { background: #0b3b36 !important; color: #5eead4 !important; border-color: #134e4a !important; }
html[data-theme="dark"] input[type="range"] { accent-color: var(--primary); }
html[data-theme="dark"] input[type="checkbox"], html[data-theme="dark"] input[type="radio"] { accent-color: var(--primary); }
#themeFab {
  position: fixed; right: 16px; bottom: 16px; z-index: 600;
  width: 42px; height: 42px; border-radius: 50%; padding: 0; font-size: 18px; line-height: 1;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 14px rgba(0,0,0,.28);
}
html[data-theme="dark"] #themeFab { background: #1c2637; color: #fcd34d; border-color: var(--border); box-shadow: 0 4px 14px rgba(0,0,0,.55); }

/* ==================== 手機 / 平板版面 ==================== */
@media (max-width: 820px) {
  :root { --left-w: 46vw; --row-h: 40px; }
  body { font-size: 13px; }
  header { flex-wrap: nowrap; overflow-x: auto; overflow-y: hidden; gap: 6px; padding: 6px 8px;
           -webkit-overflow-scrolling: touch; scrollbar-width: thin; }
  header > * { flex-shrink: 0; }
  header::-webkit-scrollbar { height: 4px; }
  header::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 2px; }
  #projectSelect { max-width: 140px; font-size: 15px; padding: 5px 8px; }
  .tab { padding: 6px 9px; font-size: 12.5px; }
  .controls { gap: 5px; }
  .controls button, .controls select { font-size: 12px; padding: 5px 8px; }
  #searchBox { width: 104px !important; }
  .spacer { flex: 0 0 4px; }
  .legend-bar { flex-wrap: nowrap; overflow-x: auto; padding: 4px 10px; -webkit-overflow-scrolling: touch; }
  .legend-bar > * { flex-shrink: 0; }
  .gantt-toolbar { padding: 6px 10px; gap: 6px; font-size: 11.5px; }
  .gantt-left-head { height: 44px; padding: 0 8px; font-size: 12px; }
  .gantt-header { height: 44px; }
  .gantt-months { height: 20px; font-size: 10px; }
  .gantt-days { height: 24px; }
  .gantt-day-cell { font-size: 9.5px; }
  .gantt-row-label { font-size: 12px; padding: 0 6px; gap: 4px; }
  .gantt-bar { font-size: 11px; padding: 0 6px; }
  .gantt-bar.is-sub-bar { font-size: 10.5px; top: 9px; height: 22px; }
  .member-pill, .sub-count-badge { font-size: 10px; padding: 1px 5px; }
  .gantt-ms-label { font-size: 11px; margin-left: 6px; }
  .panel-wrap { padding: 8px; }
  .panel-wrap table { min-width: 760px; }
  .kanban-wrap { padding: 10px; gap: 10px; scroll-snap-type: x mandatory; }
  .kanban-col { width: 84vw; min-width: 84vw; scroll-snap-align: start; }
  .modal { max-width: 100%; width: 100%; padding: 14px; border-radius: 14px; }
  .form-grid { grid-template-columns: 1fr; }
  .member-grid { grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); }
  .kpi-grid { grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; }
  .kpi-val { font-size: 20px; }
  .view-lock-card { padding: 22px 18px; }
  .toast { bottom: 64px; max-width: calc(100vw - 24px); }
  #themeFab { width: 38px; height: 38px; font-size: 16px; right: 12px; bottom: 12px; }
  /* iOS 聚焦時避免自動放大 */
  input[type="text"], input[type="password"], input[type="date"], input[type="datetime-local"],
  input[type="number"], input[type="search"], select, textarea { font-size: 16px; }
  /* 觸控拖曳任務條時，不要讓整頁跟著捲動 */
  .gantt-bar, .gantt-ms-wrap, .resize-handle { touch-action: none; }
}
@media (max-width: 480px) {
  :root { --left-w: 52vw; }
  header { padding: 5px 6px; }
  .tab { padding: 5px 7px; font-size: 11.5px; }
  .controls button, .controls select { font-size: 11.5px; padding: 4px 6px; }
  .gantt-toolbar { font-size: 11px; }
  .panel-wrap table { min-width: 720px; }
  .k-card-title { font-size: 13px; }
}

/* ==================== 手機版面：清單卡片化 / 甘特左欄精簡 ==================== */
@media (max-width: 820px) {
  .panel-wrap table.list-table { min-width: 0; width: 100%; background: transparent; box-shadow: none; border-radius: 0; }
  .panel-wrap table.list-table thead { display: none; }
  .panel-wrap table.list-table tbody,
  .panel-wrap table.list-table tr,
  .panel-wrap table.list-table td { display: block; width: auto; }
  .panel-wrap table.list-table tr {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; margin-bottom: 10px; padding: 10px 12px;
  }
  .panel-wrap table.list-table td {
    border: none; padding: 3px 0; font-size: 12.5px;
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .panel-wrap table.list-table td::before {
    content: attr(data-label); flex: 0 0 54px; color: var(--muted);
    font-size: 11px; font-weight: 700;
  }
  .panel-wrap table.list-table td:first-child { padding-bottom: 6px; font-size: 14px; display: block; }
  .panel-wrap table.list-table td:first-child::before { display: none; }
  .gantt-row-label .color-pill, .gantt-row-label .gantt-prog-text { display: none; }
  .readonly-banner { font-size: 11.5px; padding: 5px 12px; gap: 8px; }
  .readonly-banner button { font-size: 11.5px; padding: 3px 8px; }
  .gantt-toolbar .zoom-group { margin-left: auto; }
}

/* ==================== 手機版面：微調 ==================== */
@media (max-width: 820px) {
  .panel-wrap table.list-table td { padding: 2px 0; font-size: 12px; }
  .panel-wrap table.list-table td::before { flex: 0 0 46px; font-size: 10.5px; }
  .gantt-row-label .link-icon-btn { display: none; }
}
html[data-theme="dark"] .link-icon-btn[style] {
  background: #1c2637 !important; color: #93c5fd !important; border-color: #2b3749 !important;
}

/* ==================== 甘特左欄寬度拖曳 ==================== */
.gantt-splitter {
  flex: 0 0 6px; width: 6px; cursor: col-resize; position: relative;
  z-index: 12; touch-action: none; background: transparent;
}
.gantt-splitter::after {
  content: ""; position: absolute; top: 0; bottom: 0; left: 2px; width: 2px;
  background: var(--border); transition: background .15s;
}
.gantt-splitter:hover::after, .gantt-splitter.dragging::after { background: var(--primary); }
@media (max-width: 820px) { .gantt-splitter { flex: 0 0 10px; width: 10px; } }
</style>
</head>
<body>
<button type="button" id="themeFab" title="切換深色／淺色主題">🌙</button>
<!-- Full-Screen Viewing Password Lock Screen -->
<div id="viewLockOverlay" class="view-lock-overlay">
  <div class="view-lock-card">
    <div class="view-lock-icon">🔒</div>
    <h2>專案排程受密碼保護</h2>
    <p class="view-lock-desc">為避免不相干人員窺探專案內容，請先輸入團隊<strong>觀看密碼</strong>以解鎖檢視。</p>
    <div class="view-lock-form">
      <input type="password" id="viewPassInput" placeholder="請輸入觀看密碼..." autocomplete="off">
      <div id="viewLockErr" class="view-lock-err"></div>
      <button type="button" id="btnUnlockView" class="primary" style="padding:10px 14px;font-size:14px;">解鎖進入系統</button>
    </div>
    <div class="view-lock-foot">
      首次啟用預設觀看密碼為 <strong>8888</strong>，系統管理員可於「系統設定」中修改。
    </div>
  </div>
</div>

<header>
  <div class="proj-bar">
    <select id="projectSelect" title="切換專案"></select>
    <button id="btnNewProject" title="建立新專案（系統管理員／專案管理員）">+ 專案</button>
    <span id="syncBadge" class="sync-badge">連線中...</span>
    <span id="cloudBadge" class="sync-badge" style="display:none;cursor:pointer;" title="點擊立即與 GitHub 私有倉庫雙向同步">☁️ 雲端</span>
  </div>
  <div class="tabs">
    <button class="tab active" data-view="gantt">甘特圖</button>
    <button class="tab" data-view="kanban">看板</button>
    <button class="tab" data-view="list">清單</button>
    <button class="tab" data-view="analysis">📊 進度分析</button>
    <button class="tab" data-view="logs">動態紀錄</button>
  </div>
  <div class="spacer"></div>
  <div class="controls">
    <select id="colorModeSelect" title="切換文字底色與甘特條分色模式">
      <option value="status">🎨 分色：依任務狀態</option>
      <option value="task">🎨 分色：依任務</option>
      <option value="assignee">🎨 分色：依負責人</option>
      <option value="priority">🎨 分色：依優先級</option>
      <option value="schedule">🎨 分色：依進度偏差</option>
    </select>
    <button id="btnColorConfig" title="自訂各種底色配色方案">調色盤</button>
    <button class="identity-btn" id="btnIdentity" title="點擊切換身分或登入">
      <span id="identityName">尚未選擇身分</span>
      <span id="identityRole" class="role-tag role-viewer">訪客</span>
    </button>
    <select id="filterAssignee"><option value="">全部成員</option></select>
    <input type="text" id="searchBox" placeholder="搜尋任務/路徑..." style="width:120px;">
    <button class="primary" id="btnNewTask">+ 新增任務</button>
    <button id="btnHolidays" title="管理休假日（如農曆過年）與截斷任務設定">📅 休假日</button>
    <button id="btnOpenSharedFiles" title="直接在網頁瀏覽內網共用資料夾">📂 共用檔案</button>
    <button id="btnExportCSV">匯出 CSV</button>
    <button id="themeToggleBtn" class="theme-toggle-btn" title="目前為淺色主題，點擊切換深色">🌙 主題</button>
    <button id="btnSettings">設定</button>
    <button id="btnLockScreen" title="立即鎖定畫面（離開座位防窺探）" style="display:none;">🔒 鎖定</button>
    <button class="tour-btn" id="btnTour">❓ 操作教學</button>
  </div>
</header>

<div id="colorLegendBar" class="legend-bar"></div>

<div id="readonlyBanner" class="readonly-banner">
  <span id="readonlyMsg"></span>
  <button id="btnBannerLogin" style="padding:3px 10px;font-size:12px;">選擇／切換我的身分</button>
</div>

<main id="mainArea"></main>
<div id="dragTip" class="drag-tip"></div>
<div id="toast" class="toast"></div>

<!-- Built-in Spotlight Tour Elements -->
<div id="tourMaskTop" class="tour-mask"></div>
<div id="tourMaskBottom" class="tour-mask"></div>
<div id="tourMaskLeft" class="tour-mask"></div>
<div id="tourMaskRight" class="tour-mask"></div>
<div id="tourSpot" class="tour-spot"><div id="tourSpotNum" class="tour-spot-num">1</div></div>
<div id="tourCard" class="tour-card">
  <div id="tourDots" class="tour-dots"></div>
  <h4 id="tourTitle" class="tour-title"></h4>
  <p id="tourBody" class="tour-body"></p>
  <div id="tourTip" class="tour-tip"></div>
  <div class="tour-footer">
    <button id="tourClose" style="color:var(--muted);">關閉</button>
    <div style="display:flex;gap:6px;">
      <button id="tourPrev">上一步</button>
      <button id="tourNext" class="primary">下一步</button>
    </div>
  </div>
</div>

<!-- Identity / Login Modal -->
<div class="modal-bg" id="loginModal">
  <div class="modal">
    <h3>選擇您的身分</h3>
    <div class="info-note">
      點選您的名字即可登入。有鎖頭 🔒 的帳號需輸入 PIN 碼（首次啟用<strong>系統管理員</strong>預設 PIN 為 <strong>1234</strong>）。
    </div>
    <div id="loginMemberGrid" class="member-grid"></div>
    <div id="loginPinBox" class="pin-box">
      <label style="font-size:12px;font-weight:700;color:var(--muted);display:block;margin-bottom:6px;">
        請輸入 <strong id="loginPinTarget"></strong> 的 PIN 碼：
      </label>
      <div style="display:flex;gap:8px;">
        <input type="password" id="loginPinInput" placeholder="輸入 PIN 碼..." style="flex:1;" autocomplete="off">
        <button class="primary" id="btnConfirmPin">確認登入</button>
      </div>
    </div>

    <div id="myPinSection" style="display:none;border-top:1px solid var(--border);margin-top:16px;padding-top:14px;">
      <div style="font-size:13px;font-weight:700;margin-bottom:8px;">修改我的登入 PIN 碼（目前：<span id="myPinUser"></span>）</div>
      <div class="form-grid">
        <div class="form-row" id="oldPinWrap">
          <label>原本的 PIN 碼</label>
          <input type="password" id="oldPinInput" placeholder="輸入舊 PIN">
        </div>
        <div class="form-row">
          <label>新 PIN 碼（非系統管理員留空可取消密碼）</label>
          <input type="password" id="newPinInput" placeholder="輸入新 PIN 碼">
        </div>
      </div>
      <div style="margin-top:8px;">
        <button id="btnSaveMyPin">更新我的 PIN 碼</button>
      </div>
    </div>

    <div class="modal-actions">
      <button id="btnGuestBrowse">改以唯讀訪客瀏覽</button>
      <button id="btnCloseLogin">關閉</button>
    </div>
  </div>
</div>

<!-- Task Modal -->
<div class="modal-bg" id="taskModal">
  <div class="modal">
    <h3 id="modalTitle">新增任務</h3>
    <div id="taskReadonlyHint" class="info-note" style="display:none;margin-bottom:12px;background:#fef3c7;color:#92400e;">
      唯讀檢視模式：您目前僅能查看內容、點擊網址或複製資料夾路徑。
    </div>
    <div class="form-grid">
      <div class="form-row full">
        <label>任務 / 里程碑名稱</label>
        <input type="text" id="fTitle" placeholder="例如：客戶提案簡報定案">
      </div>
      <div class="form-row full" style="display:flex;flex-direction:row;gap:8px;flex-wrap:wrap;">
        <label class="check-box-row" style="flex:1;min-width:220px;">
          <input type="checkbox" id="fMilestone">
          <span>◆ 設為里程碑（單日關鍵節點）</span>
        </label>
        <label class="check-box-row" id="splitHolidayWrap" style="flex:1;min-width:220px;background:#fff1f2;border-color:#fecdd3;color:#9f1239;">
          <input type="checkbox" id="fSplitHolidays" checked>
          <span>✂️ 遇休假日自動截斷顯示（避開過年／連假）</span>
        </label>
      </div>
      <div class="form-row">
        <label>負責人</label>
        <select id="fAssignee"></select>
      </div>
      <div class="form-row">
        <label>狀態</label>
        <select id="fStatus">
          <option value="todo">待辦</option>
          <option value="doing">進行中</option>
          <option value="review">審核中</option>
          <option value="done">已完成</option>
        </select>
      </div>
      <div class="form-row">
        <label id="lblStart">預期開始時間</label>
        <input type="datetime-local" id="fStart">
      </div>
      <div class="form-row" id="endDateRow">
        <label>預期結束時間</label>
        <input type="datetime-local" id="fEnd">
      </div>
      <div class="form-row">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <label>實際完成時間（選填）</label>
          <div style="display:flex;gap:4px;">
            <button type="button" id="btnActualToday" style="padding:1px 6px;font-size:11px;">現在</button>
            <button type="button" id="btnActualClear" style="padding:1px 6px;font-size:11px;">清除</button>
          </div>
        </div>
        <input type="datetime-local" id="fActualEnd">
      </div>
      <div class="form-row">
        <label>預期 vs. 實際差異預覽</label>
        <div id="fVariancePreview" style="padding:6px 8px;border:1px solid var(--border);border-radius:6px;background:#f8fafc;font-size:12.5px;min-height:33px;display:flex;align-items:center;flex-wrap:wrap;gap:4px;">—</div>
      </div>
      <div class="form-row full">
        <div id="fHolidayAlert" class="holiday-alert-box">
          <div id="fHolidayAlertText"></div>
          <div id="fHolidayAlertActs" style="display:flex;gap:8px;flex-wrap:wrap;">
            <button type="button" id="btnExtendForHolidays" style="background:#fff;color:#0f766e;border-color:#99f6e4;padding:4px 10px;font-size:12px;">⏩ 結束時間順延補回休假</button>
            <button type="button" id="btnHardSplitTask" style="background:#be123c;color:#fff;border-color:#be123c;padding:4px 10px;font-size:12px;">✂️ 實體拆分為前後兩段任務</button>
          </div>
        </div>
      </div>
      <div class="form-row">
        <label>完成進度：<span id="fProgVal">0</span>%</label>
        <input type="range" id="fProgress" min="0" max="100" step="5" value="0">
      </div>
      <div class="form-row">
        <label>優先級</label>
        <select id="fPriority">
          <option value="high">高</option>
          <option value="medium" selected>中</option>
          <option value="low">低</option>
        </select>
      </div>
      <div class="form-row full">
        <label>任務專屬底色（「🎨 分色：依任務」時顯示，儲存後同步團隊）</label>
        <div style="display:flex;align-items:center;gap:10px;padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--soft-bg);">
          <input type="color" id="fColor" value="#2563eb" style="width:38px;height:26px;border:none;padding:0;background:transparent;cursor:pointer;">
          <span id="fColorBadge" class="color-pill" style="background:#2563eb;color:#fff;">自動配色</span>
          <button type="button" id="btnColorAuto" style="padding:3px 9px;font-size:12px;margin-left:auto;">恢復自動色</button>
        </div>
      </div>
      <div class="form-row">
        <label>上層父任務（設為子任務，可展開收合）</label>
        <select id="fParent"><option value="">無（最上層主任務）</option></select>
      </div>
      <div class="form-row">
        <label>前置任務 / 里程碑（相依箭頭）</label>
        <select id="fDep"><option value="">無</option></select>
      </div>
      <div class="form-row full">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
          <label>檔案／資料夾連結（每行一筆，可直接從共用資料夾挑選，點一下即網頁直開）</label>
          <button type="button" id="btnPickSharedFile" style="padding:3px 9px;font-size:12px;background:#f0fdfa;color:#0f766e;border-color:#99f6e4;">📂 瀏覽並挑選檔案／資料夾</button>
        </div>
        <textarea id="fFileLink" rows="2" placeholder="可點右上方「📂 瀏覽並挑選」直接選檔，或手動貼上 https:// 網址／內網路徑"></textarea>
        <div id="fLinkPreview" class="link-preview-box"></div>
      </div>
      <div class="form-row full">
        <label>備註 / 協作留言</label>
        <textarea id="fNotes" rows="3" placeholder="紀錄交接事項、審核回饋或卡關原因..."></textarea>
      </div>
    </div>
    <div class="modal-actions">
      <div style="display:flex;gap:6px;">
        <button class="danger" id="btnDeleteTask" style="display:none;">刪除任務</button>
        <button id="btnAddSubtaskFromModal" style="display:none;">+ 新增子任務</button>
      </div>
      <div style="display:flex;gap:8px;margin-left:auto;">
        <button id="btnCancelTask">關閉</button>
        <button class="primary" id="btnSaveTask">儲存並同步</button>
      </div>
    </div>
  </div>
</div>

<!-- Settings Modal (Adapts to Sysadmin vs Project Manager) -->
<div class="modal-bg" id="settingsModal">
  <div class="modal wide">
    <h3 id="settingsModalTitle">系統與專案設定</h3>
    <div class="sub-tabs">
      <button class="sub-tab active" data-stab="project" id="tabBtnProject">📁 專案管理</button>
      <button class="sub-tab" data-stab="members" id="tabBtnMembers">👥 人員與權限</button>
      <button class="sub-tab" data-stab="shared" id="tabBtnShared">📂 共用資料夾</button>
      <button class="sub-tab" data-stab="cloud" id="tabBtnCloud">☁️ 雲端同步 (GitHub)</button>
      <button class="sub-tab" data-stab="system" id="tabBtnSystem">🔒 安全與備份</button>
    </div>

    <!-- Tab 1: Project Management (Sysadmin + Authorized PM) -->
    <div id="stabProject">
      <div class="form-grid">
        <div class="form-row full">
          <label>目前專案名稱</label>
          <input type="text" id="sProjectName">
        </div>
        <div class="form-row full">
          <label>本專案專屬「專案管理員」（全部不勾選＝所有專案管理員皆可管理；勾選後僅限勾選者與系統管理員管理）</label>
          <div id="sProjectManagers" class="pm-check-list"></div>
        </div>
        <div class="form-row full" style="display:flex;flex-direction:row;justify-content:flex-end;">
          <button class="primary" id="btnSaveProjectSettings">儲存專案設定</button>
        </div>
        <div class="form-row full" style="border-top:1px solid var(--border);padding-top:12px;">
          <label style="color:var(--danger);">危險操作（刪除前系統會自動於 backups/ 存一份還原快照）</label>
          <div>
            <button class="danger" id="btnDeleteProject">刪除目前專案</button>
          </div>
        </div>
      </div>
      <div class="modal-actions">
        <div style="display:flex;gap:8px;margin-left:auto;">
          <button class="btnCloseSettings">關閉</button>
        </div>
      </div>
    </div>

    <!-- Tab 2: Members & Permissions (Sysadmin ONLY) -->
    <div id="stabMembers" style="display:none;">
      <div class="info-note" style="margin-bottom:10px;">
        <strong>四級角色權限：</strong><br>
        • <strong>系統管理員</strong>：最高權限，可管人員帳號／PIN／備份還原，並管理全部專案與任務。<br>
        • <strong>專案管理員</strong>：可建立、改名、刪除自己負責的專案，並管理該專案所有任務（不能改系統成員與還原備份）。<br>
        • <strong>一般成員</strong>：可新增與協作任務（僅能刪除自己負責的任務）。<br>
        • <strong>唯讀檢視</strong>：僅供觀看、複製路徑與匯出 CSV。PIN 碼欄位留空代表保持原密碼不變。
      </div>
      <div id="permList"></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin:10px 0 12px;flex-wrap:wrap;gap:8px;">
        <button id="btnAddMemberRow">+ 新增成員</button>
        <label style="font-size:13px;display:flex;align-items:center;gap:6px;cursor:pointer;">
          <input type="checkbox" id="chkStrictMode">
          <span><strong>嚴格模式</strong>：一般成員僅能編輯與拖曳自己負責的任務（不影響專案管理員）</span>
        </label>
      </div>
      <div class="modal-actions">
        <div style="display:flex;gap:8px;margin-left:auto;">
          <button class="btnCloseSettings">取消</button>
          <button class="primary" id="btnSaveMembers">儲存人員與權限</button>
        </div>
      </div>
    </div>

    <!-- Tab 2.5: Shared Folders Config (Sysadmin ONLY) -->
    <div id="stabShared" style="display:none;">
      <div class="info-note" style="margin-bottom:10px;">
        設定團隊可透過瀏覽器直接瀏覽、預覽與下載的資料夾（支援伺服器本機路徑如 <code>D:\專案資料</code>，或公司內網共用磁碟如 <code>\\192.168.1.10\Shared</code>）。<br>
        設定完成後，成員在編輯任務時按「📂 瀏覽並挑選」即可直接掛載，點擊即可在網頁開啟檔案或資料夾，無需複製貼上路徑。
      </div>
      <div id="sharedRootsList"></div>
      <div style="margin:10px 0 12px;">
        <button id="btnAddRootRow">+ 新增共用資料夾</button>
      </div>
      <div class="modal-actions">
        <div style="display:flex;gap:8px;margin-left:auto;">
          <button class="btnCloseSettings">取消</button>
          <button class="primary" id="btnSaveSharedRoots">儲存共用資料夾</button>
        </div>
      </div>
    </div>

    <!-- Tab 2.8: GitHub Private Repo 2-Way Auto-Sync (Sysadmin ONLY) -->
    <div id="stabCloud" style="display:none;">
      <div class="info-note" style="margin-bottom:10px;">
        <strong>模式 A：純 GitHub 私有倉庫雙向自動同步</strong><br>
        • 公司本地修改後自動推送至您的 GitHub 私有倉庫；外部人員於 GitHub Pages 修改後，公司伺服器每 10 秒自動拉回本地並留存快照備份。<br>
        • Token 僅儲存在伺服器本機 <code>github_sync.json</code>，<strong>絕對不會</strong>寫入或推送至 <code>project_data.json</code>。
      </div>
      <div class="form-grid">
        <div class="form-row full">
          <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text);cursor:pointer;">
            <input type="checkbox" id="chkGhEnabled">
            <span><strong>啟用 GitHub 私有倉庫自動雙向同步</strong></span>
          </label>
        </div>
        <div class="form-row">
          <label>GitHub 帳號（Owner）</label>
          <input type="text" id="ghOwner" placeholder="例如：stansu">
        </div>
        <div class="form-row">
          <label>私有資料倉庫名稱（Private Repo）</label>
          <input type="text" id="ghRepo" placeholder="例如：team-pm-data">
        </div>
        <div class="form-row">
          <label>分支（Branch）</label>
          <input type="text" id="ghBranch" value="main" placeholder="main">
        </div>
        <div class="form-row">
          <label>資料檔案路徑（File Path）</label>
          <input type="text" id="ghFilePath" value="project_data.json" placeholder="project_data.json">
        </div>
        <div class="form-row">
          <label>Fine-grained Token（需勾選該私有庫 Contents: Read &amp; Write）</label>
          <input type="password" id="ghToken" placeholder="貼上 github_pat_...（留空維持原 Token）" autocomplete="new-password">
        </div>
        <div class="form-row">
          <label>自動拉取檢查間隔（秒，建議 10 秒）</label>
          <input type="text" id="ghInterval" value="10" placeholder="10">
        </div>
        <div class="form-row full">
          <div id="ghStatusBox" class="info-note" style="background:#f8fafc;border:1px solid var(--border);">尚未設定 GitHub 同步</div>
        </div>
      </div>
      <div class="modal-actions">
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" id="btnGhSyncNow">🔄 立即雙向同步</button>
          <button type="button" id="btnCopyCloudInvite" style="background:#f0fdfa;color:#0f766e;border-color:#99f6e4;" title="產生給 GitHub Pages 雲端版使用的快速連線碼">📋 產生雲端快速連線碼</button>
        </div>
        <div style="display:flex;gap:8px;margin-left:auto;">
          <button class="btnCloseSettings">關閉</button>
          <button class="primary" id="btnSaveGhSync">儲存並立即測試同步</button>
        </div>
      </div>
    </div>

    <!-- Tab 3: Security (View Password) & System Backup/Restore (Sysadmin ONLY) -->
    <div id="stabSystem" style="display:none;">
      <div class="color-cfg-group" style="margin-bottom:14px;">
        <div class="color-cfg-title"><span>🔒 進入軟體前的觀看密碼保護</span></div>
        <div class="info-note" style="margin-bottom:10px;">
          啟用後，任何人在瀏覽器開啟本系統時都必須先輸入觀看密碼，未驗證前伺服器不會傳送任何專案、成員或檔案資料。
        </div>
        <div class="form-grid">
          <div class="form-row full">
            <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text);cursor:pointer;">
              <input type="checkbox" id="chkViewPassEnabled" checked>
              <span><strong>啟用觀看密碼</strong>（開啟分頁時須先輸入密碼才能瀏覽）</span>
            </label>
          </div>
          <div class="form-row">
            <label>新觀看密碼（留空代表維持原密碼）</label>
            <input type="password" id="sNewViewPass" placeholder="輸入新觀看密碼..." autocomplete="new-password">
          </div>
          <div class="form-row" style="justify-content:flex-end;">
            <label style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--text);cursor:pointer;margin-bottom:6px;">
              <input type="checkbox" id="chkKickOtherViews">
              <span>儲存後強制其他電腦重新輸入密碼</span>
            </label>
          </div>
        </div>
        <div style="margin-top:10px;display:flex;justify-content:flex-end;">
          <button class="primary" id="btnSaveViewPass">儲存觀看密碼設定</button>
        </div>
      </div>

      <div class="color-cfg-group">
        <div class="color-cfg-title"><span>💾 系統完整備份與還原（JSON）</span></div>
        <div class="info-note" style="margin-bottom:10px;">
          此區僅限<strong>系統管理員</strong>操作。匯出檔包含全部專案與設定；匯入還原前系統會自動在 <code>backups/</code> 資料夾建立一份快照。
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">
          <button class="primary" id="btnExportJSON">下載完整 JSON 備份</button>
          <button id="btnImportJSON">從 JSON 還原所有專案</button>
          <input type="file" id="importFile" accept=".json" style="display:none;">
        </div>
      </div>

      <div class="modal-actions">
        <div style="display:flex;gap:8px;margin-left:auto;">
          <button class="btnCloseSettings">關閉</button>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Web Shared Folder Browser & Picker Modal -->
<div class="modal-bg" id="fsModal" style="z-index:80;">
  <div class="modal wide">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px;">
      <h3 id="fsModalTitle" style="margin:0;">📂 共用資料夾瀏覽器</h3>
      <span id="fsModeBadge" class="role-tag role-pm" style="display:none;">挑選模式</span>
    </div>
    <div class="fs-toolbar">
      <label style="font-size:12px;font-weight:700;color:var(--muted);">根目錄：</label>
      <select id="fsRootSelect" style="min-width:170px;"></select>
      <button id="fsBtnUp" title="回上一層資料夾">⬆ 上一層</button>
      <button id="fsBtnRefresh" title="重新整理">🔄</button>
      <input type="text" id="fsSearchInput" placeholder="篩選此層檔名..." style="flex:1;min-width:130px;">
      <button class="primary" id="fsBtnPickCurrentDir" style="display:none;">✔ 插入目前資料夾</button>
    </div>
    <div id="fsCrumbBar" class="fs-crumb-bar"></div>
    <div class="fs-realpath-bar">
      <span id="fsRealPathText" class="fs-realpath-text"></span>
      <button id="fsBtnCopyRealPath" style="padding:2px 8px;font-size:11px;">📋 複製實體路徑</button>
    </div>
    <div id="fsListWrap" class="fs-list-wrap"></div>
    <div class="modal-actions">
      <span id="fsFooterHint" style="font-size:12px;color:var(--muted);">點資料夾可進入；點檔案可於新分頁直接預覽或下載。</span>
      <button id="fsBtnClose" class="primary">關閉</button>
    </div>
  </div>
</div>

<!-- Color Display & Custom Palette Modal -->
<div class="modal-bg" id="colorModal" style="z-index:85;">
  <div class="modal wide">
    <h3>🎨 顏色顯示模式與自訂配色</h3>
    <div class="info-note" style="margin-bottom:12px;">
      選擇最有意義的分色方式，讓甘特圖、看板、清單與標籤底色一眼就能傳達資訊（此設定儲存在您個人瀏覽器，每位同事可各自設定習慣的配色）。
    </div>
    <div class="form-row full" style="margin-bottom:12px;">
      <label>目前套用的分色模式</label>
      <select id="cfgColorMode">
        <option value="status">依任務狀態分色（待辦／進行中／審核中／已完成）</option>
        <option value="task">依任務分色（每項任務獨立專屬底色）</option>
        <option value="assignee">依負責人分色（每位成員專屬顏色）</option>
        <option value="priority">依優先級分色（高／中／低）</option>
        <option value="schedule">依進度偏差分色（提前／準時／延遲／逾期／即將到期／正常）</option>
      </select>
    </div>
    <div id="colorConfigBody"></div>
    <div class="modal-actions">
      <button id="btnResetColors">恢復預設配色</button>
      <div style="display:flex;gap:8px;margin-left:auto;">
        <button id="btnCloseColorModal" class="primary">完成並套用</button>
      </div>
    </div>
  </div>
</div>

<!-- Holiday Calendar & Task Split Management Modal -->
<div class="modal-bg" id="holidayModal" style="z-index:84;">
  <div class="modal wide">
    <h3>📅 休假日與連假管理（任務自動截斷）</h3>
    <div class="info-note" style="margin-bottom:12px;">
      設定公司或國定休假日（例如<strong>農曆過年連假</strong>）後，甘特圖會自動標示紅底休假帶，並將跨越假期的任務<strong>自動截斷成多段顯示</strong>（進度條僅計入實際工作日）；也可以針對跨假任務一鍵<strong>順延結束時間</strong>或<strong>實體拆分為前後兩段任務</strong>。
    </div>
    <div id="holidayAdminToolbar" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button type="button" id="btnAddHolidayRow">+ 新增休假區間</button>
        <button type="button" id="btnPresetTW2027" style="background:#fff1f2;color:#be123c;border-color:#fecdd3;">🧨 匯入 2027 台灣國定假日（含農曆春節）</button>
      </div>
      <button type="button" class="primary" id="btnSaveHolidays">儲存休假日並同步</button>
    </div>
    <div id="holidayList" style="max-height:240px;overflow-y:auto;padding-right:4px;"></div>

    <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:12px;">
      <div style="font-size:13.5px;font-weight:800;margin-bottom:8px;color:#0f172a;">
        🔍 本專案跨越休假日的任務（<span id="holidayImpactCount">0</span> 筆）
      </div>
      <div id="holidayImpactList" style="max-height:200px;overflow-y:auto;"></div>
    </div>

    <div class="modal-actions">
      <span id="holidayPermHint" style="font-size:12px;color:var(--muted);">系統管理員與專案管理員可編輯休假日清單。</span>
      <button type="button" id="btnCloseHolidayModal" class="primary">關閉</button>
    </div>
  </div>
</div>

<script>
const COLORS = ["#2563eb", "#0891b2", "#7c3aed", "#d97706", "#db2777", "#059669", "#dc2626", "#4f46e5", "#0d9488", "#ca8a04", "#9333ea", "#0284c7"];
const STATUS_MAP = { todo: "待辦", doing: "進行中", review: "審核中", done: "已完成" };
const PRIO_MAP = { high: "高", medium: "中", low: "低" };
const SCHEDULE_LABEL = {
  early: "提前完成",
  ontime: "準時完成",
  late: "延遲完成",
  overdue: "進行中逾期",
  soon: "即將到期",
  normal: "正常進行",
};
const COLOR_MODE_LABEL = {
  status: "依任務狀態",
  task: "依任務",
  assignee: "依負責人",
  priority: "依優先級",
  schedule: "依進度偏差",
};
const ZOOM_LABEL = { day: "日", week: "週", month: "月", quarter: "季", year: "年" };
const DEFAULT_PALETTE = {
  status: { todo: "#64748b", doing: "#2563eb", review: "#8b5cf6", done: "#10b981" },
  priority: { high: "#dc2626", medium: "#d97706", low: "#0d9488" },
  schedule: {
    early: "#0d9488",
    ontime: "#16a34a",
    late: "#ea580c",
    overdue: "#dc2626",
    soon: "#d97706",
    normal: "#2563eb",
  },
  assignee: {},
  task: {},
};
const ROLE_LABEL = {
  sysadmin: "系統管理員",
  pm: "專案管理員",
  member: "一般成員",
  viewer: "唯讀檢視",
};

const TW_2027_HOLIDAYS = [
  { id: "h_2026_1225", name: "2026 行憲紀念日連假", start: "2026-12-25", end: "2026-12-27" },
  { id: "h_2027_0101", name: "2027 元旦連假", start: "2027-01-01", end: "2027-01-03" },
  { id: "h_2027_cny",  name: "2027 農曆春節連假", start: "2027-02-04", end: "2027-02-10" },
  { id: "h_2027_0228", name: "2027 和平紀念日連假", start: "2027-02-27", end: "2027-03-01" },
  { id: "h_2027_0404", name: "2027 兒童節與清明節連假", start: "2027-04-03", end: "2027-04-06" },
  { id: "h_2027_0501", name: "2027 勞動節連假", start: "2027-04-30", end: "2027-05-02" },
  { id: "h_2027_0609", name: "2027 端午節", start: "2027-06-09", end: "2027-06-09" },
  { id: "h_2027_0915", name: "2027 中秋節", start: "2027-09-15", end: "2027-09-15" },
  { id: "h_2027_0928", name: "2027 教師節", start: "2027-09-28", end: "2027-09-28" },
  { id: "h_2027_1010", name: "2027 國慶日連假", start: "2027-10-09", end: "2027-10-11" },
  { id: "h_2027_1025", name: "2027 臺灣光復節連假", start: "2027-10-23", end: "2027-10-25" },
  { id: "h_2027_1225", name: "2027 行憲紀念日連假", start: "2027-12-24", end: "2027-12-26" },
  { id: "h_2028_0101", name: "2028 元旦連假", start: "2027-12-31", end: "2028-01-02" },
];
let state = { version: 0, bootId: "", strictMemberEdit: false, viewPassEnabled: true, holidays: [], sharedRoots: [], members: [], projects: [], logs: [], me: null };
let fsState = { mode: "browse", rootId: "", path: "", parent: null, realPath: "", items: [] };
let viewToken = sessionStorage.getItem("pm_lan_view_token") || "";
let viewLocked = true;
let authToken = localStorage.getItem("pm_lan_token") || "";
let currentProjectId = localStorage.getItem("pm_lan_project") || "";
let currentView = "gantt";
let analysisFilter = "all";
let zoomMode = localStorage.getItem("pm_lan_zoom_mode") || "week";
if (!ZOOM_LABEL[zoomMode]) zoomMode = "week";
let ganttMetrics = { rangeStartMs: 0, pxPerHour: 1.75, snapMins: 1440 };
let editingTaskId = null;
let editingTaskHasCustomColor = false;
let dragState = null;
let toastTimer = null;
let selectedLoginMemberId = null;
let tourIndex = -1;
let initialLoginPrompted = false;

let colorMode = localStorage.getItem("pm_lan_color_mode") || "status";
if (!COLOR_MODE_LABEL[colorMode]) colorMode = "status";
let customColors = (() => {
  try {
    const raw = JSON.parse(localStorage.getItem("pm_lan_color_prefs") || "{}");
    return {
      status: { ...(raw.status || {}) },
      priority: { ...(raw.priority || {}) },
      schedule: { ...(raw.schedule || {}) },
      assignee: { ...(raw.assignee || {}) },
      task: { ...(raw.task || {}) },
    };
  } catch (_) {
    return { status: {}, priority: {}, schedule: {}, assignee: {}, task: {} };
  }
})();
let collapsedSet = (() => {
  try {
    return new Set(JSON.parse(localStorage.getItem("pm_lan_collapsed_v6") || "[]"));
  } catch (_) {
    return new Set();
  }
})();

function saveCollapsedSet() {
  try { localStorage.setItem("pm_lan_collapsed_v6", JSON.stringify(Array.from(collapsedSet))); } catch (_) {}
}
function saveColorPrefs() {
  try {
    localStorage.setItem("pm_lan_color_mode", colorMode);
    localStorage.setItem("pm_lan_color_prefs", JSON.stringify(customColors));
  } catch (_) {}
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2800);
}
function parseDateTime(s, fallbackTime = "00:00") {
  const raw = String(s || "").trim().replace(" ", "T");
  const [datePart, timePart] = raw.split("T");
  const dp = (datePart || "").split("-").map(Number);
  const tp = (timePart || fallbackTime).split(":").map(Number);
  return new Date(dp[0] || 2026, (dp[1] || 1) - 1, dp[2] || 1, tp[0] || 0, tp[1] || 0, 0, 0);
}
function fmtDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
function fmtDateTime(d) {
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${fmtDate(d)}T${hh}:${mm}`;
}
function ensureDateTimeStr(s, defaultTime = "09:00") {
  const raw = String(s || "").trim().replace(" ", "T");
  if (!raw) return `${todayStr()}T${defaultTime}`;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(raw)) return raw.slice(0, 16);
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return `${raw}T${defaultTime}`;
  return `${todayStr()}T${defaultTime}`;
}
function fmtDisplayDT(s, short = false) {
  if (!s) return "";
  const norm = ensureDateTimeStr(s, "00:00").replace("T", " ");
  return short ? norm.slice(5) : norm;
}
function addMinutes(s, mins, defaultTime = "09:00") {
  const d = parseDateTime(s, defaultTime);
  d.setMinutes(d.getMinutes() + mins);
  return fmtDateTime(d);
}
function addDaysDT(s, n, defaultTime = "09:00") {
  return addMinutes(s, n * 1440, defaultTime);
}
function diffMinutes(s1, s2, fallback1 = "00:00", fallback2 = "00:00") {
  return Math.round((parseDateTime(s2, fallback2).getTime() - parseDateTime(s1, fallback1).getTime()) / 60000);
}
function fmtDurationMinutes(absMins) {
  const m = Math.max(0, Math.round(absMins));
  if (m < 60) return `${m} 分`;
  if (m < 1440) {
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return rm ? `${h} 時 ${rm} 分` : `${h} 時`;
  }
  const d = Math.floor(m / 1440);
  const h = Math.round((m % 1440) / 60);
  if (h === 24) return `${d + 1} 天`;
  return h ? `${d} 天 ${h} 時` : `${d} 天`;
}
function todayStr() { return fmtDate(new Date()); }
function nowDateTimeStr() { return fmtDateTime(new Date()); }
function getMergedHolidayIntervals() {
  const list = (state.holidays || []).map(h => {
    const s = String(h.start || "").slice(0, 10);
    const e = String(h.end || s).slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s) || !/^\d{4}-\d{2}-\d{2}$/.test(e)) return null;
    const sMs = parseDateTime(`${s}T00:00`).getTime();
    const eD = parseDateTime(`${e}T00:00`);
    eD.setDate(eD.getDate() + 1);
    const eMs = eD.getTime();
    if (eMs <= sMs) return null;
    return { sMs, eMs, name: h.name || "休假日", start: s, end: e };
  }).filter(Boolean).sort((a, b) => a.sMs - b.sMs);

  const merged = [];
  list.forEach(item => {
    if (merged.length && item.sMs <= merged[merged.length - 1].eMs) {
      const prev = merged[merged.length - 1];
      if (item.eMs > prev.eMs) {
        prev.eMs = item.eMs;
        prev.end = item.end;
      }
      if (!prev.names.includes(item.name)) prev.names.push(item.name);
    } else {
      merged.push({ sMs: item.sMs, eMs: item.eMs, start: item.start, end: item.end, names: [item.name] });
    }
  });
  return merged;
}

function splitTaskByHolidaysMs(sMs, eMs, enableSplit = true) {
  const merged = getMergedHolidayIntervals();
  const hits = [];
  let holidayOverlapMs = 0;
  merged.forEach(h => {
    const ovStart = Math.max(sMs, h.sMs);
    const ovEnd = Math.min(eMs, h.eMs);
    if (ovEnd > ovStart) {
      holidayOverlapMs += (ovEnd - ovStart);
      h.names.forEach(nm => { if (!hits.includes(nm)) hits.push(nm); });
    }
  });

  if (!enableSplit || !hits.length || eMs <= sMs) {
    return {
      segments: [{ sMs, eMs }],
      bridges: [],
      hits,
      holidayOverlapMs,
      workMs: Math.max(0, (eMs - sMs) - holidayOverlapMs),
      fullyInHoliday: eMs > sMs && holidayOverlapMs >= (eMs - sMs),
    };
  }

  let segs = [{ sMs, eMs }];
  merged.forEach(h => {
    if (h.eMs <= sMs || h.sMs >= eMs) return;
    const next = [];
    segs.forEach(seg => {
      if (h.eMs <= seg.sMs || h.sMs >= seg.eMs) {
        next.push(seg);
      } else {
        if (seg.sMs < h.sMs) next.push({ sMs: seg.sMs, eMs: h.sMs });
        if (h.eMs < seg.eMs) next.push({ sMs: h.eMs, eMs: seg.eMs });
      }
    });
    segs = next;
  });

  if (!segs.length) {
    return {
      segments: [{ sMs, eMs }],
      bridges: [],
      hits,
      holidayOverlapMs,
      workMs: 0,
      fullyInHoliday: true,
    };
  }

  const bridges = [];
  for (let i = 0; i < segs.length - 1; i++) {
    const gapStart = segs[i].eMs;
    const gapEnd = segs[i + 1].sMs;
    const gapNames = [];
    merged.forEach(h => {
      if (h.eMs > gapStart && h.sMs < gapEnd) {
        h.names.forEach(nm => { if (!gapNames.includes(nm)) gapNames.push(nm); });
      }
    });
    bridges.push({ sMs: gapStart, eMs: gapEnd, label: gapNames.join(" / ") || "休假" });
  }
  const workMs = segs.reduce((acc, g) => acc + (g.eMs - g.sMs), 0);
  return { segments: segs, bridges, hits, holidayOverlapMs, workMs, fullyInHoliday: false };
}

function calcExtendedEndForHolidays(startStr, endStr) {
  const sMs = parseDateTime(startStr, "09:00").getTime();
  const eMs = parseDateTime(endStr || startStr, "18:00").getTime();
  let remaining = Math.max(0, eMs - sMs);
  if (remaining <= 0) return ensureDateTimeStr(endStr || startStr, "18:00");
  const merged = getMergedHolidayIntervals();
  let cur = sMs;
  for (let guard = 0; guard < 120 && remaining > 0; guard++) {
    // 若 cur 剛好落在休假中，直接跳到休假結束點
    const inside = merged.find(h => cur >= h.sMs && cur < h.eMs);
    if (inside) {
      cur = inside.eMs;
      continue;
    }
    const nextH = merged.find(h => h.sMs > cur);
    if (!nextH || cur + remaining <= nextH.sMs) {
      cur += remaining;
      remaining = 0;
    } else {
      remaining -= (nextH.sMs - cur);
      cur = nextH.eMs;
    }
  }
  return fmtDateTime(new Date(cur));
}

function getISOWeekNumber(d) {
  const tmp = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const dayNum = tmp.getUTCDay() || 7;
  tmp.setUTCDate(tmp.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1));
  return Math.ceil((((tmp - yearStart) / 86400000) + 1) / 7);
}
function memberNames() { return (state.members || []).map(m => m.name); }
function memberColor(name) {
  if (customColors.assignee && customColors.assignee[name]) return customColors.assignee[name];
  const idx = Math.max(0, memberNames().indexOf(name));
  return COLORS[idx % COLORS.length];
}
function compareTasksByStart(a, b) {
  const sa = ensureDateTimeStr(a.start, "09:00");
  const sb = ensureDateTimeStr(b.start, "09:00");
  if (sa !== sb) return sa < sb ? -1 : 1;
  const ea = ensureDateTimeStr(a.end || a.start, "18:00");
  const eb = ensureDateTimeStr(b.end || b.start, "18:00");
  if (ea !== eb) return ea < eb ? -1 : 1;
  return String(a.title || "").localeCompare(String(b.title || ""), "zh-Hant");
}
function hexToHsl(hex) {
  const clean = String(hex || "").replace("#", "");
  if (clean.length !== 6) return [220, 0.78, 0.52];
  const r = parseInt(clean.slice(0, 2), 16) / 255;
  const g = parseInt(clean.slice(2, 4), 16) / 255;
  const b = parseInt(clean.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h = 0, s = 0;
  const l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r: h = (g - b) / d + (g < b ? 6 : 0); break;
      case g: h = (b - r) / d + 2; break;
      case b: h = (r - g) / d + 4; break;
    }
    h *= 60;
  }
  return [h, s, l];
}
function hslToHex(h, s, l) {
  const hh = ((h % 360) + 360) % 360;
  const ss = Math.max(0, Math.min(1, s));
  const ll = Math.max(0, Math.min(1, l));
  const c = (1 - Math.abs(2 * ll - 1)) * ss;
  const x = c * (1 - Math.abs(((hh / 60) % 2) - 1));
  const m = ll - c / 2;
  let r = 0, g = 0, b = 0;
  if (hh < 60) { r = c; g = x; }
  else if (hh < 120) { r = x; g = c; }
  else if (hh < 180) { g = c; b = x; }
  else if (hh < 240) { g = x; b = c; }
  else if (hh < 300) { r = x; b = c; }
  else { r = c; b = x; }
  const toHex = v => Math.round((v + m) * 255).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}
function deriveSubtaskTint(baseHex, depth, sibIndex = 0) {
  if (depth <= 0) return baseHex;
  const [h, s, l] = hexToHsl(baseHex);
  const step = sibIndex % 3;
  let targetL, targetS;
  if (l <= 0.62) {
    targetL = Math.min(0.84, l + 0.16 + (depth - 1) * 0.08 + step * 0.035);
    targetS = Math.max(0.32, s * (depth === 1 ? 0.88 : 0.78));
  } else {
    targetL = Math.max(0.28, l - 0.16 - (depth - 1) * 0.08 - step * 0.03);
    targetS = Math.min(0.92, Math.max(0.35, s * 1.05));
  }
  return hslToHex(h, targetS, targetL);
}
function getRootTasksSorted(allTasks) {
  const idSet = new Set(allTasks.map(t => t.id));
  return allTasks
    .filter(t => !t.parentId || !idSet.has(t.parentId) || t.parentId === t.id)
    .sort(compareTasksByStart);
}
function getTaskLineage(task, allTasks) {
  const byId = new Map(allTasks.map(t => [t.id, t]));
  let cur = task;
  let depth = 0;
  const visited = new Set([task.id]);
  while (cur && cur.parentId && byId.has(cur.parentId) && !visited.has(cur.parentId)) {
    visited.add(cur.parentId);
    cur = byId.get(cur.parentId);
    depth++;
  }
  let sibIndex = 0;
  if (depth > 0 && task.parentId) {
    const sibs = allTasks.filter(x => x.parentId === task.parentId).sort(compareTasksByStart);
    const idx = sibs.findIndex(x => x.id === task.id);
    if (idx >= 0) sibIndex = idx;
  }
  return { root: cur || task, depth, sibIndex };
}
function getTaskAutoColor(taskId) {
  const proj = getActiveProject();
  const roots = getRootTasksSorted(proj.tasks || []);
  const idx = roots.findIndex(t => t.id === taskId);
  return COLORS[(idx >= 0 ? idx : roots.length) % COLORS.length];
}
function getRootBaseColor(rootTask) {
  if (!rootTask) return COLORS[0];
  if (customColors.task && customColors.task[rootTask.id]) return customColors.task[rootTask.id];
  if (rootTask.color && /^#[0-9a-fA-F]{6}$/.test(rootTask.color)) return rootTask.color;
  return getTaskAutoColor(rootTask.id);
}
function getTaskOwnColor(task) {
  if (!task) return COLORS[0];
  const proj = getActiveProject();
  const allTasks = proj.tasks || [];
  const { root, depth, sibIndex } = getTaskLineage(task, allTasks);
  const base = getRootBaseColor(root);
  return depth === 0 ? base : deriveSubtaskTint(base, depth, sibIndex);
}
function getPaletteColor(group, key) {
  if (group === "assignee") return memberColor(key);
  if (group === "task") {
    const proj = getActiveProject();
    const t = (proj.tasks || []).find(x => x.id === key);
    return t ? getTaskOwnColor(t) : COLORS[0];
  }
  if (customColors[group] && customColors[group][key]) return customColors[group][key];
  return (DEFAULT_PALETTE[group] && DEFAULT_PALETTE[group][key]) || "#2563eb";
}
function contrastTextColor(hex) {
  const clean = String(hex || "").replace("#", "");
  if (clean.length !== 6) return "#ffffff";
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  const yiq = (r * 299 + g * 587 + b * 114) / 1000;
  return yiq >= 165 ? "#111827" : "#ffffff";
}
function calcScheduleVariance(task) {
  const nowStr = nowDateTimeStr();
  const expected = ensureDateTimeStr(task.end || task.start, "18:00");
  const actualRaw = (task.actualEnd || "").trim();
  const expDisp = fmtDisplayDT(expected);
  if (actualRaw) {
    const actual = ensureDateTimeStr(actualRaw, "18:00");
    const actDisp = fmtDisplayDT(actual);
    const deltaMins = diffMinutes(expected, actual);
    const deltaDays = deltaMins / 1440;
    if (deltaMins < 0) {
      const dur = fmtDurationMinutes(Math.abs(deltaMins));
      return { key: "early", deltaMins, delta: deltaDays, shortLabel: `提前 ${dur}`, fullLabel: `提前 ${dur}完成（預期 ${expDisp} → 實際 ${actDisp}）`, completed: true };
    }
    if (deltaMins === 0) {
      return { key: "ontime", deltaMins: 0, delta: 0, shortLabel: "準時完成", fullLabel: `準時完成（${actDisp}）`, completed: true };
    }
    const dur = fmtDurationMinutes(deltaMins);
    return { key: "late", deltaMins, delta: deltaDays, shortLabel: `延遲 +${dur}`, fullLabel: `延遲 ${dur}完成（預期 ${expDisp} → 實際 ${actDisp}）`, completed: true };
  }
  if (task.status === "done") {
    return { key: "ontime", deltaMins: null, delta: null, shortLabel: "已完成(未填時)", fullLabel: "狀態為已完成，尚未填寫實際完成時間", completed: true };
  }
  const remMins = diffMinutes(nowStr, expected);
  if (remMins < 0) {
    const ovMins = Math.abs(remMins);
    const dur = fmtDurationMinutes(ovMins);
    return { key: "overdue", deltaMins: ovMins, delta: ovMins / 1440, shortLabel: `逾期 +${dur}`, fullLabel: `已逾期 ${dur}（預期結束 ${expDisp}）`, completed: false };
  }
  if (remMins <= 48 * 60) {
    const dur = remMins < 1 ? "即將到期" : `剩 ${fmtDurationMinutes(remMins)}`;
    return { key: "soon", deltaMins: 0, delta: 0, shortLabel: dur, fullLabel: `即將到期（預期結束 ${expDisp}）`, completed: false };
  }
  return { key: "normal", deltaMins: 0, delta: 0, shortLabel: `剩 ${fmtDurationMinutes(remMins)}`, fullLabel: `進度正常（預期結束 ${expDisp}）`, completed: false };
}
function getTaskSemanticColor(task) {
  if (colorMode === "task") {
    const bg = getTaskOwnColor(task);
    return { bg, fg: contrastTextColor(bg), badge: task.assignee, title: `依任務分色：${task.title}` };
  }
  if (colorMode === "assignee") {
    const bg = getPaletteColor("assignee", task.assignee);
    return { bg, fg: contrastTextColor(bg), badge: task.assignee, title: `負責人：${task.assignee}` };
  }
  if (colorMode === "priority") {
    const k = task.priority || "medium";
    const bg = getPaletteColor("priority", k);
    return { bg, fg: contrastTextColor(bg), badge: `${PRIO_MAP[k] || k}優先`, title: `優先級：${PRIO_MAP[k] || k}` };
  }
  if (colorMode === "schedule") {
    const v = calcScheduleVariance(task);
    const bg = getPaletteColor("schedule", v.key);
    return { bg, fg: contrastTextColor(bg), badge: v.shortLabel, title: v.fullLabel };
  }
  const st = task.status || "todo";
  const bg = getPaletteColor("status", st);
  return { bg, fg: contrastTextColor(bg), badge: STATUS_MAP[st] || st, title: `狀態：${STATUS_MAP[st] || st}` };
}
function renderColorLegendBar() {
  const bar = document.getElementById("colorLegendBar");
  if (!bar) return;
  let items = [];
  let extraNote = "";
  if (colorMode === "task") {
    const proj = getActiveProject();
    const allTasks = proj.tasks || [];
    const roots = getRootTasksSorted(allTasks);
    const maxShow = 10;
    items = roots.slice(0, maxShow).map(t => ({
      label: t.title.length > 10 ? t.title.slice(0, 10) + "…" : t.title,
      bg: getRootBaseColor(t),
    }));
    if (roots.length > maxShow) {
      extraNote = `<span style="color:var(--muted);font-size:11.5px;">…共 ${roots.length} 個主任務色系（子任務同色系）</span>`;
    } else if (roots.length) {
      extraNote = `<span style="color:var(--muted);font-size:11.5px;">（子任務自動沿用同色系）</span>`;
    }
  } else if (colorMode === "assignee") {
    items = memberNames().map(name => ({ label: name, bg: getPaletteColor("assignee", name) }));
  } else if (colorMode === "priority") {
    items = ["high", "medium", "low"].map(k => ({ label: `${PRIO_MAP[k]}優先`, bg: getPaletteColor("priority", k) }));
  } else if (colorMode === "schedule") {
    items = ["early", "ontime", "late", "overdue", "soon", "normal"].map(k => ({ label: SCHEDULE_LABEL[k], bg: getPaletteColor("schedule", k) }));
  } else {
    items = ["todo", "doing", "review", "done"].map(k => ({ label: STATUS_MAP[k], bg: getPaletteColor("status", k) }));
  }
  const chips = items.map(it =>
    `<span class="legend-chip" style="background:${it.bg};color:${contrastTextColor(it.bg)};" onclick="openColorModal()" title="點擊自訂配色">${esc(it.label)}</span>`
  ).join("");
  bar.innerHTML = `<span class="legend-title">底色意義（${esc(COLOR_MODE_LABEL[colorMode])}）：</span>${chips}${extraNote}<span style="margin-left:auto;color:var(--muted);font-size:11.5px;">點色塊或「調色盤」可自訂顏色</span>`;
}
function buildTaskTreeRows(allTasks, filteredTasks, respectCollapse = true) {
  const byId = new Map();
  const childrenMap = new Map();
  allTasks.forEach(t => {
    byId.set(t.id, t);
    childrenMap.set(t.id, []);
  });
  const roots = [];
  allTasks.forEach(t => {
    const pid = t.parentId && byId.has(t.parentId) && t.parentId !== t.id ? t.parentId : "";
    if (pid) childrenMap.get(pid).push(t);
    else roots.push(t);
  });
  roots.sort(compareTasksByStart);
  childrenMap.forEach(kids => kids.sort(compareTasksByStart));

  const statsMap = new Map();
  function calcStats(tid, visited = new Set()) {
    if (statsMap.has(tid)) return statsMap.get(tid);
    if (visited.has(tid)) return { total: 0, done: 0 };
    visited.add(tid);
    const kids = childrenMap.get(tid) || [];
    let total = 0, done = 0;
    kids.forEach(k => {
      total += 1;
      if (k.status === "done") done += 1;
      const sub = calcStats(k.id, visited);
      total += sub.total;
      done += sub.done;
    });
    const res = { total, done };
    statsMap.set(tid, res);
    return res;
  }
  allTasks.forEach(t => calcStats(t.id));

  const matchedIds = new Set(filteredTasks.map(t => t.id));
  const includeIds = new Set(matchedIds);
  matchedIds.forEach(id => {
    let cur = byId.get(id);
    const guard = new Set();
    while (cur && cur.parentId && byId.has(cur.parentId) && !guard.has(cur.parentId)) {
      guard.add(cur.parentId);
      includeIds.add(cur.parentId);
      cur = byId.get(cur.parentId);
    }
  });

  const rows = [];
  const emitted = new Set();
  const hiddenByCollapse = new Set();
  function markDescendantsHidden(nodeId) {
    const kids = childrenMap.get(nodeId) || [];
    kids.forEach(k => {
      if (!hiddenByCollapse.has(k.id)) {
        hiddenByCollapse.add(k.id);
        markDescendantsHidden(k.id);
      }
    });
  }
  function walk(node, depth) {
    if (!includeIds.has(node.id) || emitted.has(node.id) || hiddenByCollapse.has(node.id)) return;
    emitted.add(node.id);
    const allKids = childrenMap.get(node.id) || [];
    const visKids = allKids.filter(k => includeIds.has(k.id));
    const hasChildren = visKids.length > 0;
    const isCollapsed = respectCollapse && collapsedSet.has(node.id);
    const st = statsMap.get(node.id) || { total: 0, done: 0 };
    rows.push({
      task: node,
      depth,
      hasChildren,
      totalChildren: st.total,
      doneChildren: st.done,
      isCollapsed,
    });
    if (hasChildren) {
      if (isCollapsed) {
        markDescendantsHidden(node.id);
      } else {
        visKids.forEach(k => walk(k, depth + 1));
      }
    }
  }
  roots.forEach(r => walk(r, 0));
  [...allTasks].sort(compareTasksByStart).forEach(t => { if (includeIds.has(t.id) && !emitted.has(t.id) && !hiddenByCollapse.has(t.id)) walk(t, 0); });
  return { rows, byId, childrenMap, statsMap };
}
window.toggleSubtasks = function(tid, ev) {
  if (ev) ev.stopPropagation();
  if (collapsedSet.has(tid)) collapsedSet.delete(tid);
  else collapsedSet.add(tid);
  saveCollapsedSet();
  render();
};
window.expandAllSubtasks = function() {
  collapsedSet.clear();
  saveCollapsedSet();
  render();
};
window.collapseAllSubtasks = function() {
  const proj = getActiveProject();
  const tasks = proj.tasks || [];
  const hasKids = new Set();
  tasks.forEach(t => { if (t.parentId) hasKids.add(t.parentId); });
  collapsedSet = hasKids;
  saveCollapsedSet();
  render();
};

// ---------- 四級權限判定 ----------
function getMe() { return state.me || null; }
function isSysAdmin() { return Boolean(state.me && state.me.role === "sysadmin"); }
function canCreateProject() { return Boolean(state.me && (state.me.role === "sysadmin" || state.me.role === "pm")); }
function canManageProject(proj = getActiveProject()) {
  const me = getMe();
  if (!me || !proj) return false;
  if (me.role === "sysadmin") return true;
  if (me.role === "pm") {
    const mgrs = proj.managerIds || [];
    return mgrs.length === 0 || mgrs.includes(me.id);
  }
  return false;
}
function canCreateTask() {
  const me = getMe();
  return Boolean(me && (me.role === "sysadmin" || me.role === "pm" || me.role === "member"));
}
function canEditTask(task, proj = getActiveProject()) {
  const me = getMe();
  if (!me) return false;
  if (canManageProject(proj)) return true;
  if (me.role !== "pm" && me.role !== "member") return false;
  if (!task) return true;
  if (state.strictMemberEdit && task.assignee !== me.name) return false;
  return true;
}
function canDeleteTask(task, proj = getActiveProject()) {
  const me = getMe();
  if (!me || !task) return false;
  if (canManageProject(proj)) return true;
  if ((me.role === "pm" || me.role === "member") && task.assignee === me.name) return true;
  return false;
}

function getActiveProject() {
  if (!state.projects || !state.projects.length) return { id: "", name: "預設專案", managerIds: [], tasks: [] };
  let p = state.projects.find(x => x.id === currentProjectId);
  if (!p) {
    p = state.projects[0];
    currentProjectId = p.id;
    localStorage.setItem("pm_lan_project", currentProjectId);
  }
  return p;
}
function getProjectManagerLabel(proj) {
  const mgrs = proj.managerIds || [];
  if (!mgrs.length) return "專案管理：所有專案管理員";
  const names = mgrs
    .map(id => (state.members || []).find(m => m.id === id))
    .filter(Boolean)
    .map(m => m.name);
  return names.length ? `本專案管理員：${names.join("、")}` : "專案管理：所有專案管理員";
}
function parseLinks(raw) {
  return String(raw || "").split(/\r?\n/).map(s => s.trim()).filter(Boolean);
}
function isWebUrl(str) { return /^https?:\/\//i.test(str); }
function normSlashLower(s) {
  return String(s || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}
function parseShareLink(str) {
  const m = String(str || "").match(/^share:\/\/([^\/]+)(\/.*)?$/i);
  if (m) {
    const rootId = m[1];
    const rest = m[2] || "/";
    const isDir = rest === "/" || rest.endsWith("/");
    const rel = rest.replace(/^\/+|\/+$/g, "");
    const rootObj = (state.sharedRoots || []).find(r => r.id === rootId);
    const rootLabel = rootObj ? rootObj.label : rootId;
    const baseName = rel ? rel.split("/").pop() : rootLabel;
    return {
      type: isDir ? "share-dir" : "share-file",
      rootId,
      rel,
      label: baseName,
      fullLabel: rel ? `${rootLabel}/${rel}` : rootLabel,
    };
  }
  // 智慧比對：若手動貼上的實體/內網路徑剛好位於已設定的共用資料夾內，也自動升級為可直開！
  const normIn = normSlashLower(str);
  if (normIn && !isWebUrl(str)) {
    const rawNoSlash = String(str).replace(/\\/g, "/");
    const trailingSlash = rawNoSlash.endsWith("/");
    for (const r of (state.sharedRoots || [])) {
      const nr = normSlashLower(r.path);
      if (!nr) continue;
      if (normIn === nr || normIn.startsWith(nr + "/")) {
        const rel = rawNoSlash.slice(r.path.replace(/\\/g, "/").replace(/\/+$/, "").length).replace(/^\/+|\/+$/g, "");
        const last = rel ? rel.split("/").pop() : "";
        const looksLikeFile = Boolean(last && !trailingSlash && /\.[a-z0-9]{1,8}$/i.test(last));
        return {
          type: looksLikeFile ? "share-file" : "share-dir",
          rootId: r.id,
          rel,
          label: last || r.label,
          fullLabel: rel ? `${r.label}/${rel}` : r.label,
        };
      }
    }
  }
  return null;
}
function buildFsFileUrl(rootId, rel, download = false) {
  const vt = viewToken ? `&vtoken=${encodeURIComponent(viewToken)}` : "";
  return `/api/fs/file?root=${encodeURIComponent(rootId)}&path=${encodeURIComponent(rel)}${download ? "&download=1" : ""}${vt}`;
}
function shortLinkLabel(str) {
  if (isWebUrl(str)) {
    try {
      const u = new URL(str);
      const tail = u.pathname && u.pathname !== "/" ? u.pathname.split("/").filter(Boolean).pop() : "";
      return tail ? `${u.hostname}/.../${tail}` : u.hostname;
    } catch (_) { return str; }
  }
  const sh = parseShareLink(str);
  if (sh) return sh.label;
  const parts = str.split(/[\\/]+/).filter(Boolean);
  return parts.length >= 2 ? `...\\${parts[parts.length - 2]}\\${parts[parts.length - 1]}` : str;
}
function renderLinkChipsHtml(raw) {
  const links = parseLinks(raw);
  if (!links.length) return "";
  return links.map(link => {
    if (isWebUrl(link)) {
      return `<a class="link-chip" href="${esc(link)}" target="_blank" rel="noopener noreferrer" data-stop="1" title="開啟網址：${esc(link)}">🔗 ${esc(shortLinkLabel(link))}</a>`;
    }
    const sh = parseShareLink(link);
    if (sh && sh.type === "share-dir") {
      return `<span class="link-chip share-dir" data-open-dir-root="${esc(sh.rootId)}" data-open-dir-path="${esc(sh.rel)}" data-stop="1" title="直接於網頁開啟資料夾：${esc(sh.fullLabel)}">📂 ${esc(sh.label)}</span>`;
    }
    if (sh && sh.type === "share-file") {
      const url = buildFsFileUrl(sh.rootId, sh.rel, false);
      return `<a class="link-chip share-file" href="${esc(url)}" target="_blank" rel="noopener noreferrer" data-stop="1" title="直接於網頁預覽或下載：${esc(sh.fullLabel)}">📄 ${esc(sh.label)}</a>`;
    }
    return `<span class="link-chip path" data-copy-link="${esc(link)}" data-stop="1" title="點擊複製路徑（貼到檔案總管網址列開啟）：${esc(link)}">📁 ${esc(shortLinkLabel(link))}</span>`;
  }).join("");
}
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => {
      showToast("已複製路徑！請貼到檔案總管網址列按 Enter 開啟");
    }).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}
function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand("copy");
    showToast("已複製路徑！請貼到檔案總管網址列按 Enter 開啟");
  } catch (e) {
    prompt("請按 Ctrl+C 複製路徑：", text);
  }
  document.body.removeChild(ta);
}

document.addEventListener("click", e => {
  const dirEl = e.target.closest("[data-open-dir-root]");
  if (dirEl) {
    e.stopPropagation();
    openFsModal("browse", dirEl.dataset.openDirRoot, dirEl.dataset.openDirPath || "");
    return;
  }
  const copyEl = e.target.closest("[data-copy-link]");
  if (copyEl) {
    e.stopPropagation();
    copyText(copyEl.dataset.copyLink);
    return;
  }
  const stopEl = e.target.closest("[data-stop]");
  if (stopEl) e.stopPropagation();
});

function setSyncStatus(ok, text) {
  const b = document.getElementById("syncBadge");
  b.textContent = text;
  b.className = ok ? "sync-badge" : "sync-badge err";
}

function authHeaders(extra = {}) {
  const h = { ...extra };
  if (viewToken) h["X-View-Token"] = viewToken;
  if (authToken) h["X-Auth-Token"] = authToken;
  return h;
}

function sanitizeGhFilePath(raw) {
  var s = String(raw == null ? "" : raw).trim().replace(/\\/g, "/").replace(/\u0000/g, "");
  if (!s) return "project_data.json";
  if (/^(?:[A-Za-z]:|\/|~\/)/.test(s)) { s = s.split("/").pop() || ""; }
  var parts = s.split("/").filter(function (seg) { return seg && seg !== "." && seg !== ".."; });
  return parts.join("/") || "project_data.json";
}

function showViewLockScreen(errMsg = "") {
  viewLocked = true;
  viewToken = "";
  sessionStorage.removeItem("pm_lan_view_token");
  state.projects = [];
  state.logs = [];
  document.getElementById("mainArea").innerHTML = "";
  ["taskModal", "settingsModal", "loginModal", "fsModal", "colorModal", "holidayModal"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("open");
  });
  endTour();
  document.getElementById("viewLockErr").textContent = errMsg;
  const ov = document.getElementById("viewLockOverlay");
  ov.classList.remove("hidden");
  const inp = document.getElementById("viewPassInput");
  inp.value = "";
  setTimeout(() => inp.focus(), 50);
}

function hideViewLockScreen() {
  viewLocked = false;
  document.getElementById("viewLockOverlay").classList.add("hidden");
  document.getElementById("viewLockErr").textContent = "";
}

async function submitViewUnlock() {
  const inp = document.getElementById("viewPassInput");
  const errEl = document.getElementById("viewLockErr");
  const pw = inp.value.trim();
  errEl.textContent = "";
  try {
    const res = await fetch("/api/unlock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const json = await res.json();
    if (!json.ok) {
      errEl.textContent = json.error || "觀看密碼錯誤";
      inp.select();
      return;
    }
    viewToken = json.viewToken || "";
    if (viewToken) sessionStorage.setItem("pm_lan_view_token", viewToken);
    hideViewLockScreen();
    await fetchState(true);
  } catch (e) {
    errEl.textContent = "無法連線至伺服器，請確認伺服器已啟動";
  }
}

async function fetchState(force = false) {
  if (!force && (viewLocked || dragState || isAnyModalOpen())) return;
  try {
    const curCTick = (state.cloudSync && state.cloudSync.tick != null) ? state.cloudSync.tick : "";
    const url = force
      ? "/api/state"
      : `/api/state?since=${state.version}&boot=${encodeURIComponent(state.bootId || "")}&ctick=${encodeURIComponent(curCTick)}`;
    const res = await fetch(url, { headers: authHeaders() });
    if (res.status === 304) {
      setSyncStatus(true, `已同步 v${state.version}`);
      return;
    }
    if (res.status === 401) {
      const errJson = await res.json().catch(() => ({}));
      if (errJson.locked) {
        showViewLockScreen("");
        setSyncStatus(true, "已鎖定");
        return;
      }
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    hideViewLockScreen();
    state = await res.json();
    if (!state.me && authToken) {
      authToken = "";
      localStorage.removeItem("pm_lan_token");
      await tryAutoLoginSavedPinlessMember();
    }
    setSyncStatus(true, `已同步 v${state.version}`);
    refreshHeaderControls();
    render();
    if (!initialLoginPrompted) {
      initialLoginPrompted = true;
      if (!localStorage.getItem("pm_lan_tour_seen")) {
        document.getElementById("btnTour").classList.add("pulse");
      }
      if (!state.me) {
        openLoginModal();
      }
    }
  } catch (e) {
    setSyncStatus(false, "與伺服器斷線");
  }
}

async function tryAutoLoginSavedPinlessMember() {
  const lastId = localStorage.getItem("pm_lan_last_member_id");
  if (!lastId) return;
  const m = (state.members || []).find(x => x.id === lastId);
  if (m && !m.hasPin) {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ memberId: m.id, pin: "" }),
    });
    const json = await res.json();
    if (json.ok) {
      authToken = json.token;
      localStorage.setItem("pm_lan_token", authToken);
      state = json.data;
    }
  }
}

async function apiPost(path, payload) {
  if (!payload.projectId) payload.projectId = getActiveProject().id;
  const res = await fetch(path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  const json = await res.json();
  if (!json.ok) {
    if (res.status === 401 && json.locked) {
      showViewLockScreen(json.error || "觀看密碼已變更，請重新輸入");
      return null;
    }
    showToast(json.error || "操作失敗");
    if (res.status === 401) openLoginModal();
    return null;
  }
  if (json.token) {
    authToken = json.token;
    localStorage.setItem("pm_lan_token", authToken);
  }
  if (json.data) {
    state = json.data;
  }
  if (json.projectId) {
    currentProjectId = json.projectId;
    localStorage.setItem("pm_lan_project", currentProjectId);
  }
  setSyncStatus(true, `已同步 v${state.version}`);
  refreshHeaderControls();
  render();
  return json;
}

function isAnyModalOpen() {
  return document.getElementById("taskModal").classList.contains("open") ||
         document.getElementById("settingsModal").classList.contains("open") ||
         document.getElementById("loginModal").classList.contains("open") ||
         document.getElementById("fsModal").classList.contains("open") ||
         document.getElementById("colorModal").classList.contains("open") ||
         document.getElementById("holidayModal").classList.contains("open");
}

function refreshHeaderControls() {
  const proj = getActiveProject();
  const me = getMe();
  document.title = `${proj.name} - 團隊專案排程`;

  const projSel = document.getElementById("projectSelect");
  projSel.innerHTML = (state.projects || []).map(p =>
    `<option value="${esc(p.id)}">${esc(p.name)} (${(p.tasks || []).length})</option>`
  ).join("");
  projSel.value = proj.id;

  const nameEl = document.getElementById("identityName");
  const roleEl = document.getElementById("identityRole");
  if (me) {
    nameEl.textContent = me.name;
    roleEl.textContent = ROLE_LABEL[me.role] || me.role;
    roleEl.className = `role-tag role-${me.role}`;
  } else {
    nameEl.textContent = "點此選擇身分";
    roleEl.textContent = "訪客";
    roleEl.className = "role-tag role-viewer";
  }

  document.getElementById("btnNewProject").style.display = canCreateProject() ? "inline-block" : "none";

  const btnSettings = document.getElementById("btnSettings");
  if (isSysAdmin()) {
    btnSettings.style.display = "inline-block";
    btnSettings.textContent = "系統設定";
    btnSettings.title = "系統管理員設定（人員權限、專案管理、備份還原）";
  } else if (canManageProject(proj)) {
    btnSettings.style.display = "inline-block";
    btnSettings.textContent = "專案設定";
    btnSettings.title = "專案管理員設定（更名、指定負責人、刪除本專案）";
  } else {
    btnSettings.style.display = "none";
  }

  const btnNewTask = document.getElementById("btnNewTask");
  btnNewTask.disabled = !canCreateTask();
  btnNewTask.title = canCreateTask() ? "新增任務或里程碑" : "請登入管理員或一般成員身分以新增任務";

  const banner = document.getElementById("readonlyBanner");
  const bannerMsg = document.getElementById("readonlyMsg");
  if (!me) {
    banner.classList.add("show");
    bannerMsg.textContent = "目前為「唯讀訪客」模式，僅供瀏覽排程。若需編輯或新增任務，請先選擇您的身分。";
  } else if (me.role === "viewer") {
    banner.classList.add("show");
    bannerMsg.textContent = `您已登入為「${me.name}（唯讀檢視）」，可自由瀏覽所有專案、複製資料夾路徑與匯出 Excel。`;
  } else if (!canManageProject(proj) && state.strictMemberEdit) {
    banner.classList.add("show");
    bannerMsg.textContent = `嚴格模式已開啟：您於本專案僅能編輯與拖曳負責人為「${me.name}」的任務。`;
  } else {
    banner.classList.remove("show");
  }

  const filterSel = document.getElementById("filterAssignee");
  const curFilter = filterSel.value;
  const names = memberNames();
  filterSel.innerHTML = `<option value="">全部成員</option>` +
    names.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  if (names.includes(curFilter)) filterSel.value = curFilter;

  const cb = document.getElementById("cloudBadge");
  const cs = state.cloudSync || {};
  if (cs.enabled) {
    cb.style.display = "inline-block";
    if (cs.status === "synced") {
      cb.className = "sync-badge";
      cb.textContent = "☁️ 雲端已同步";
      cb.title = `倉庫：${cs.repo} (${cs.branch})\n最後同步：${cs.lastSync || "剛剛"}\n點擊立即雙向同步`;
    } else if (cs.status === "syncing") {
      cb.className = "sync-badge";
      cb.style.background = "#dbeafe";
      cb.style.color = "#1d4ed8";
      cb.textContent = "☁️ 同步中...";
    } else if (cs.status === "error") {
      cb.className = "sync-badge err";
      cb.style.background = "";
      cb.style.color = "";
      cb.textContent = "☁️ 雲端同步異常";
      cb.title = `錯誤：${cs.lastError || "未知錯誤"}\n點擊重試同步`;
    } else {
      cb.className = "sync-badge";
      cb.textContent = "☁️ 雲端待命";
    }
    if (cs.status !== "syncing") { cb.style.background = ""; cb.style.color = ""; }
  } else {
    cb.style.display = "none";
  }

  document.getElementById("btnLockScreen").style.display = state.viewPassEnabled ? "inline-block" : "none";
  document.getElementById("colorModeSelect").value = colorMode;
  renderColorLegendBar();
}

function getFilteredTasks() {
  const proj = getActiveProject();
  const assignee = document.getElementById("filterAssignee").value;
  const kw = document.getElementById("searchBox").value.trim().toLowerCase();
  return (proj.tasks || []).filter(t => {
    if (assignee && t.assignee !== assignee) return false;
    if (kw) {
      const hay = `${t.title} ${t.notes || ""} ${t.fileLink || ""}`.toLowerCase();
      if (!hay.includes(kw)) return false;
    }
    return true;
  });
}

function render() {
  renderColorLegendBar();
  if (currentView === "gantt") renderGantt();
  else if (currentView === "kanban") renderKanban();
  else if (currentView === "list") renderList();
  else if (currentView === "analysis") renderAnalysis();
  else renderLogs();
  if (tourIndex >= 0) positionTour();
}

function buildGanttTimeline(tasks) {
  const nowDate = new Date();
  const nowMs = nowDate.getTime();
  let minMs = nowMs;
  let maxMs = nowMs + 14 * 86400000;

  tasks.forEach(t => {
    const sMs = parseDateTime(t.start, "09:00").getTime();
    const eMs = parseDateTime(t.end || t.start, "18:00").getTime();
    if (sMs < minMs) minMs = sMs;
    if (eMs > maxMs) maxMs = eMs;
    if (t.actualEnd) {
      const aMs = parseDateTime(t.actualEnd, "18:00").getTime();
      if (aMs < minMs) minMs = aMs;
      if (aMs > maxMs) maxMs = aMs;
    }
  });

  const minD = new Date(minMs);
  const maxD = new Date(maxMs);
  const startOfDay = d => new Date(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, 0);
  const addDaysDate = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, 0, 0, 0, 0);

  let rangeStartDate, rangeEndDate, pxPerHour, snapMins;
  let topCells = [];
  let botCells = [];
  let bgCols = [];
  let dayModeCapped = false;

  if (zoomMode === "day") {
    pxPerHour = 22; // 528px/day, 44px per 2-hour block
    snapMins = 30;
    rangeStartDate = addDaysDate(startOfDay(minD), -1);
    rangeEndDate = addDaysDate(startOfDay(maxD), 2);
    let spanDays = Math.round((rangeEndDate - rangeStartDate) / 86400000);
    if (spanDays < 4) {
      rangeEndDate = addDaysDate(rangeStartDate, 4);
      spanDays = 4;
    }
    if (spanDays > 60) {
      dayModeCapped = true;
      const anchor = (nowMs >= minMs && nowMs <= maxMs) ? startOfDay(nowDate) : startOfDay(minD);
      rangeStartDate = addDaysDate(anchor, -15);
      rangeEndDate = addDaysDate(anchor, 45);
      spanDays = 60;
    }
    const wdNames = "日一二三四五六";
    const todayIso = todayStr();
    for (let i = 0; i < spanDays; i++) {
      const cur = addDaysDate(rangeStartDate, i);
      const iso = fmtDate(cur);
      const wk = cur.getDay() === 0 || cur.getDay() === 6;
      const isT = iso === todayIso;
      const dayW = 24 * pxPerHour;
      topCells.push({
        label: `${cur.getFullYear()}/${cur.getMonth() + 1}/${cur.getDate()} (${wdNames[cur.getDay()]})${isT ? " 今天" : ""}`,
        w: dayW,
        cls: isT ? "today" : (wk ? "wknd" : ""),
      });
      for (let h = 0; h < 24; h += 2) {
        const blockW = 2 * pxPerHour;
        const off = wk || h < 8 || h >= 18;
        const isLast = h === 22;
        botCells.push({
          label: `${String(h).padStart(2, "0")}:00`,
          sub: "",
          w: blockW,
          cls: `${off ? "wknd" : ""} ${isT && nowDate.getHours() >= h && nowDate.getHours() < h + 2 ? "today" : ""}`,
        });
        bgCols.push({
          w: blockW,
          cls: `${off ? "offhour" : ""} ${isLast ? "day-sep" : ""}`,
        });
      }
    }
  } else if (zoomMode === "week") {
    const pxPerDay = 44;
    pxPerHour = pxPerDay / 24;
    snapMins = 1440;
    let s0 = addDaysDate(startOfDay(minD), -5);
    const dow = (s0.getDay() + 6) % 7; // Monday = 0
    rangeStartDate = addDaysDate(s0, -dow);
    let e0 = addDaysDate(startOfDay(maxD), 12);
    const eDow = (e0.getDay() + 6) % 7;
    rangeEndDate = addDaysDate(e0, 7 - eDow);
    let spanDays = Math.round((rangeEndDate - rangeStartDate) / 86400000);
    if (spanDays < 28) {
      rangeEndDate = addDaysDate(rangeStartDate, 28);
      spanDays = 28;
    }
    const wdNames = "日一二三四五六";
    const todayIso = todayStr();
    for (let i = 0; i < spanDays; i += 7) {
      const wStart = addDaysDate(rangeStartDate, i);
      const wEnd = addDaysDate(rangeStartDate, i + 6);
      const wn = getISOWeekNumber(wStart);
      topCells.push({
        label: `${wStart.getFullYear()} 第${wn}週 (${wStart.getMonth() + 1}/${wStart.getDate()}~${wEnd.getMonth() + 1}/${wEnd.getDate()})`,
        w: 7 * pxPerDay,
        cls: "",
      });
    }
    for (let i = 0; i < spanDays; i++) {
      const cur = addDaysDate(rangeStartDate, i);
      const iso = fmtDate(cur);
      const wk = cur.getDay() === 0 || cur.getDay() === 6;
      const isT = iso === todayIso;
      botCells.push({
        label: String(cur.getDate()),
        sub: wdNames[cur.getDay()],
        w: pxPerDay,
        cls: `${wk ? "wknd" : ""} ${isT ? "today" : ""}`,
      });
      bgCols.push({
        w: pxPerDay,
        cls: `${wk ? "wknd" : ""} ${cur.getDay() === 0 ? "day-sep" : ""}`,
      });
    }
  } else if (zoomMode === "month") {
    const pxPerDay = 18;
    pxPerHour = pxPerDay / 24;
    snapMins = 1440;
    const sBase = addDaysDate(startOfDay(minD), -10);
    rangeStartDate = new Date(sBase.getFullYear(), sBase.getMonth(), 1, 0, 0, 0, 0);
    const eBase = addDaysDate(startOfDay(maxD), 25);
    rangeEndDate = new Date(eBase.getFullYear(), eBase.getMonth() + 1, 1, 0, 0, 0, 0);
    if ((rangeEndDate - rangeStartDate) / 86400000 < 92) {
      rangeEndDate = new Date(rangeStartDate.getFullYear(), rangeStartDate.getMonth() + 3, 1, 0, 0, 0, 0);
    }
    // Top row: Months
    let curM = new Date(rangeStartDate.getFullYear(), rangeStartDate.getMonth(), 1);
    while (curM < rangeEndDate) {
      const nextM = new Date(curM.getFullYear(), curM.getMonth() + 1, 1);
      const daysInM = Math.round((nextM - curM) / 86400000);
      topCells.push({
        label: `${curM.getFullYear()}年${curM.getMonth() + 1}月`,
        w: daysInM * pxPerDay,
        cls: "",
      });
      curM = nextM;
    }
    // Bottom row: Weeks clipped to [rangeStartDate, rangeEndDate]
    let curW = new Date(rangeStartDate.getTime());
    const todayIso = todayStr();
    while (curW < rangeEndDate) {
      const dow = (curW.getDay() + 6) % 7;
      let nextW = addDaysDate(curW, 7 - dow);
      if (nextW > rangeEndDate) nextW = new Date(rangeEndDate.getTime());
      const daysInW = Math.round((nextW - curW) / 86400000);
      const wPx = daysInW * pxPerDay;
      const containsToday = fmtDate(curW) <= todayIso && todayIso < fmtDate(nextW);
      const wn = getISOWeekNumber(curW);
      botCells.push({
        label: wPx >= 54 ? `W${wn} (${curW.getMonth() + 1}/${curW.getDate()})` : (wPx >= 28 ? `${curW.getMonth() + 1}/${curW.getDate()}` : ""),
        sub: "",
        w: wPx,
        cls: containsToday ? "today" : "",
      });
      bgCols.push({ w: wPx, cls: "" });
      curW = nextW;
    }
  } else if (zoomMode === "quarter") {
    // quarter zoom (3個月一季): top = 季 (Q1~Q4, 3個月一組), bottom = 各月份
    const pxPerDay = 9.5;
    pxPerHour = pxPerDay / 24;
    snapMins = 1440;
    const startQMonth = Math.floor(minD.getMonth() / 3) * 3;
    rangeStartDate = new Date(minD.getFullYear(), startQMonth, 1, 0, 0, 0, 0);
    const endQMonth = Math.floor(maxD.getMonth() / 3) * 3 + 3;
    rangeEndDate = new Date(maxD.getFullYear(), endQMonth, 1, 0, 0, 0, 0);
    // 至少顯示兩季（6 個月）方便跨季捲動檢視
    const minEnd = new Date(rangeStartDate.getFullYear(), rangeStartDate.getMonth() + 6, 1, 0, 0, 0, 0);
    if (rangeEndDate < minEnd) rangeEndDate = minEnd;

    const curYr = nowDate.getFullYear();
    const curMo = nowDate.getMonth();
    const curQ = Math.floor(curMo / 3);
    let curQStart = new Date(rangeStartDate.getFullYear(), rangeStartDate.getMonth(), 1);
    while (curQStart < rangeEndDate) {
      const y = curQStart.getFullYear();
      const qStartM = curQStart.getMonth();
      const qIdx = Math.floor(qStartM / 3);
      const qNum = qIdx + 1;
      const nextQStart = new Date(y, qStartM + 3, 1);
      const qDays = Math.round((nextQStart - curQStart) / 86400000);
      const isCurQ = (y === curYr && qIdx === curQ);
      topCells.push({
        label: `${y}年 Q${qNum}（${qStartM + 1}~${qStartM + 3}月）`,
        w: qDays * pxPerDay,
        cls: isCurQ ? "today" : "",
      });
      for (let off = 0; off < 3; off++) {
        const m = qStartM + off;
        const mStart = new Date(y, m, 1);
        const mEnd = new Date(y, m + 1, 1);
        const mDays = Math.round((mEnd - mStart) / 86400000);
        const mPx = mDays * pxPerDay;
        const isCurM = (y === curYr && m === curMo);
        botCells.push({
          label: `${m + 1}月`,
          sub: `${mDays}天`,
          w: mPx,
          cls: isCurM ? "today" : (qIdx % 2 === 1 ? "wknd" : ""),
        });
        bgCols.push({
          w: mPx,
          cls: `${qIdx % 2 === 1 ? "wknd" : ""} ${off === 2 ? "day-sep" : ""}`,
        });
      }
      curQStart = nextQStart;
    }
  } else {
    // year zoom: top = years, bottom = months
    const pxPerDay = 3.8;
    pxPerHour = pxPerDay / 24;
    snapMins = 1440;
    const startYear = minD.getFullYear();
    const endYear = Math.max(startYear, maxD.getFullYear());
    rangeStartDate = new Date(startYear, 0, 1, 0, 0, 0, 0);
    rangeEndDate = new Date(endYear + 1, 0, 1, 0, 0, 0, 0);
    const curYr = nowDate.getFullYear();
    const curMo = nowDate.getMonth();
    for (let y = startYear; y <= endYear; y++) {
      const yStart = new Date(y, 0, 1);
      const yEnd = new Date(y + 1, 0, 1);
      const yDays = Math.round((yEnd - yStart) / 86400000);
      topCells.push({ label: `${y}年`, w: yDays * pxPerDay, cls: "" });
      for (let m = 0; m < 12; m++) {
        const mStart = new Date(y, m, 1);
        const mEnd = new Date(y, m + 1, 1);
        const mDays = Math.round((mEnd - mStart) / 86400000);
        const mPx = mDays * pxPerDay;
        const isCurM = (y === curYr && m === curMo);
        botCells.push({
          label: `${m + 1}月`,
          sub: "",
          w: mPx,
          cls: isCurM ? "today" : "",
        });
        bgCols.push({
          w: mPx,
          cls: `${m % 2 === 1 ? "wknd" : ""} ${m === 11 ? "day-sep" : ""}`,
        });
      }
    }
  }

  const rangeStartMs = rangeStartDate.getTime();
  const rangeEndMs = rangeEndDate.getTime();
  const totalW = Math.round(((rangeEndMs - rangeStartMs) / 3600000) * pxPerHour);
  return { rangeStartMs, rangeEndMs, pxPerHour, snapMins, totalW, topCells, botCells, bgCols, dayModeCapped };
}

function msToX(ms) {
  return ((ms - ganttMetrics.rangeStartMs) / 3600000) * ganttMetrics.pxPerHour;
}

/* ---------- 甘特圖左欄寬度（可拖曳、記住設定） ---------- */
function clampGanttLeftWidth(px) {
  const vw = window.innerWidth;
  const max = vw < 820
    ? Math.max(120, Math.min(vw * 0.75, vw - 120))
    : Math.max(240, Math.min(760, vw - 220));
  const min = vw < 820 ? 100 : 150;
  return Math.max(min, Math.min(max, Math.round(px)));
}
function ganttSavedLeftWidth() {
  try { return parseInt(localStorage.getItem("pm_left_w") || "0", 10) || 0; } catch (e) { return 0; }
}
function applyGanttLeftWidth(px) {
  if (px == null) { document.documentElement.style.removeProperty("--left-w"); return; }
  document.documentElement.style.setProperty("--left-w", clampGanttLeftWidth(px) + "px");
}
function restoreGanttLeftWidth() {
  const saved = ganttSavedLeftWidth();
  if (saved) applyGanttLeftWidth(saved);
}
window.addEventListener("resize", () => {
  const saved = ganttSavedLeftWidth();
  if (saved) applyGanttLeftWidth(saved);
});
restoreGanttLeftWidth();

function wireGanttSplitter() {
  const splitter = document.getElementById("ganttSplitter");
  if (!splitter) return;
  splitter.addEventListener("pointerdown", e => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    const leftEl = document.querySelector(".gantt-left");
    if (!leftEl) return;
    const startX = e.clientX;
    const startW = leftEl.getBoundingClientRect().width;
    splitter.classList.add("dragging");
    const onMove = ev => applyGanttLeftWidth(startW + (ev.clientX - startX));
    const onEnd = () => {
      splitter.classList.remove("dragging");
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onEnd);
      document.removeEventListener("pointercancel", onEnd);
      const el = document.querySelector(".gantt-left");
      if (el) {
        const w = clampGanttLeftWidth(el.getBoundingClientRect().width);
        try { localStorage.setItem("pm_left_w", String(w)); } catch (e) {}
      }
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onEnd);
    document.addEventListener("pointercancel", onEnd);
  });
  splitter.addEventListener("dblclick", () => {
    try { localStorage.removeItem("pm_left_w"); } catch (e) {}
    document.documentElement.style.removeProperty("--left-w");
  });
}

function renderGantt() {
  const proj = getActiveProject();
  const allTasks = proj.tasks || [];
  const filteredTasks = getFilteredTasks();
  const tree = buildTaskTreeRows(allTasks, filteredTasks, true);
  const rows = tree.rows;
  const tasks = rows.map(r => r.task);

  const tl = buildGanttTimeline(tasks);
  ganttMetrics = { rangeStartMs: tl.rangeStartMs, pxPerHour: tl.pxPerHour, snapMins: tl.snapMins };
  const totalW = tl.totalW;
  const rowH = 44;

  const topHtml = tl.topCells.map(c =>
    `<div class="gantt-month-cell ${c.cls}" style="width:${c.w}px;min-width:${c.w}px;">${esc(c.label)}</div>`
  ).join("");
  const botHtml = tl.botCells.map(c =>
    `<div class="gantt-day-cell ${c.cls}" style="width:${c.w}px;min-width:${c.w}px;">${esc(c.label)}${c.sub ? `<span style="font-size:9px;opacity:.75;">${esc(c.sub)}</span>` : ""}</div>`
  ).join("");
  const bgLayerHtml = tl.bgCols.map(c =>
    `<div class="gantt-bg-col ${c.cls}" style="width:${c.w}px;min-width:${c.w}px;"></div>`
  ).join("");

  const canAddSub = canCreateTask();
  const leftRows = rows.map(r => {
    const t = r.task;
    const links = parseLinks(t.fileLink);
    let linkBtn = "";
    if (links.length) {
      const first = links[0];
      const cnt = links.length > 1 ? links.length : "";
      if (isWebUrl(first)) {
        linkBtn = `<a class="link-icon-btn" href="${esc(first)}" target="_blank" rel="noopener noreferrer" data-stop="1" title="開啟連結：${esc(first)}">🔗${cnt}</a>`;
      } else {
        const sh = parseShareLink(first);
        if (sh && sh.type === "share-dir") {
          linkBtn = `<button class="link-icon-btn" style="background:#f0fdfa;color:#0f766e;border-color:#99f6e4;" data-open-dir-root="${esc(sh.rootId)}" data-open-dir-path="${esc(sh.rel)}" data-stop="1" title="網頁直開資料夾：${esc(sh.fullLabel)}">📂${cnt}</button>`;
        } else if (sh && sh.type === "share-file") {
          const url = buildFsFileUrl(sh.rootId, sh.rel, false);
          linkBtn = `<a class="link-icon-btn" style="background:#eef2ff;color:#3730a3;border-color:#c7d2fe;text-decoration:none;" href="${esc(url)}" target="_blank" rel="noopener noreferrer" data-stop="1" title="網頁預覽/下載：${esc(sh.fullLabel)}">📄${cnt}</a>`;
        } else {
          linkBtn = `<button class="link-icon-btn" data-copy-link="${esc(first)}" data-stop="1" title="複製路徑：${esc(first)}">📁${cnt}</button>`;
        }
      }
    }
    const indentPx = 8 + r.depth * 18;
    const toggleBtn = r.hasChildren
      ? `<button type="button" class="tree-toggle" onclick="toggleSubtasks('${esc(t.id)}', event)" title="${r.isCollapsed ? "展開子任務" : "收合子任務"}">${r.isCollapsed ? "▸" : "▾"}</button>`
      : `<span class="tree-spacer">${r.depth > 0 ? "└" : ""}</span>`;
    const subBadge = r.totalChildren > 0
      ? `<span class="sub-count-badge" title="子任務完成進度：${r.doneChildren} / ${r.totalChildren}">${r.doneChildren}/${r.totalChildren}</span>`
      : "";
    const addSubBtn = canAddSub
      ? `<button type="button" class="sub-add-btn" onclick="openTaskModal(null, '${esc(t.id)}'); event.stopPropagation();" title="在此任務下新增子任務">＋</button>`
      : "";
    const msTag = t.isMilestone ? `<span class="ms-badge">◆</span>` : "";
    const sem = getTaskSemanticColor(t);
    const progText = t.isMilestone ? (t.status === "done" ? "達成" : "節點") : `${t.progress}%`;
    return `
      <div class="gantt-row-label ${r.totalChildren > 0 ? "is-parent" : ""} ${r.depth > 0 ? "is-subtask" : ""}" data-id="${esc(t.id)}" style="padding-left:${indentPx}px;">
        ${toggleBtn}
        ${msTag}
        <span class="gantt-row-title" title="${esc(t.title)} (${esc(fmtDisplayDT(t.start))} ~ ${esc(fmtDisplayDT(t.end))})">${esc(t.title)}</span>
        ${subBadge}
        ${addSubBtn}
        ${linkBtn}
        <span class="color-pill" style="background:${sem.bg};color:${sem.fg};" title="${esc(sem.title)}">${esc(sem.badge)}</span>
        <span class="gantt-prog-text" style="font-size:11px;color:var(--muted);width:30px;text-align:right;">${progText}</span>
      </div>
    `;
  }).join("");

  const nowMs = Date.now();
  const taskPos = {};
  const rightRows = rows.map((r, rowIdx) => {
    const t = r.task;
    const sMs = parseDateTime(t.start, "09:00").getTime();
    const eMs = parseDateTime(t.end || t.start, "18:00").getTime();
    const yMid = rowIdx * rowH + rowH / 2;
    const locked = !canEditTask(t, proj);
    const sem = getTaskSemanticColor(t);
    const vInfo = calcScheduleVariance(t);

    let actualMarkerHtml = "";
    if (t.actualEnd) {
      const aMs = parseDateTime(t.actualEnd, "18:00").getTime();
      const actX = msToX(aMs);
      if (vInfo.key === "late") {
        const expRight = msToX(eMs);
        const lateW = Math.max(22, actX - expRight);
        actualMarkerHtml = `<div class="gantt-actual-late" style="left:${expRight}px;width:${lateW}px;" title="${esc(vInfo.fullLabel)}">${esc(vInfo.shortLabel)}</div>`;
      } else if (vInfo.key === "early") {
        actualMarkerHtml = `<div class="gantt-actual-flag" style="left:${actX}px;background:#0d9488;" title="${esc(vInfo.fullLabel)}"><span class="gantt-actual-flag-tag" style="background:#0d9488;color:#fff;">✓${esc(vInfo.shortLabel)}</span></div>`;
      } else {
        actualMarkerHtml = `<div class="gantt-actual-flag" style="left:${actX}px;background:#16a34a;" title="${esc(vInfo.fullLabel)}"><span class="gantt-actual-flag-tag" style="background:#16a34a;color:#fff;">✓準時</span></div>`;
      }
    } else if (vInfo.key === "overdue") {
      const expRight = msToX(eMs);
      const nowX = msToX(nowMs);
      const ovW = Math.max(22, nowX - expRight);
      actualMarkerHtml = `<div class="gantt-actual-overdue" style="left:${expRight}px;width:${ovW}px;" title="${esc(vInfo.fullLabel)}">${esc(vInfo.shortLabel)}</div>`;
    }

    if (t.isMilestone) {
      const centerX = msToX(sMs);
      const leftPos = centerX - 9;
      taskPos[t.id] = { left: centerX - 13, right: centerX + 13, y: yMid };
      return `
        <div class="gantt-grid-row" style="width:${totalW}px;">
          ${actualMarkerHtml}
          <div class="gantt-ms-wrap ${locked ? "locked" : ""}" data-id="${esc(t.id)}" data-ms="1" style="left:${leftPos}px;" title="${esc(t.title)} (${esc(fmtDisplayDT(t.start))})｜${esc(vInfo.fullLabel)}">
            <div class="gantt-ms-diamond ${t.status === "done" ? "done" : ""}" style="${t.status === "done" ? "" : `background:${sem.bg};border-color:${sem.bg};`}"></div>
            <span class="gantt-ms-label">${esc(t.title)} (${esc(fmtDisplayDT(t.start, true))})</span>
          </div>
        </div>
      `;
    }

    const enableSplit = t.splitByHolidays !== false;
    const sp = splitTaskByHolidaysMs(sMs, eMs, enableSplit);
    const segs = sp.segments;
    const firstLeft = msToX(segs[0].sMs);
    const lastRight = Math.max(firstLeft + 12, msToX(segs[segs.length - 1].eMs));
    const barCls = `${locked ? "locked" : ""} ${r.totalChildren > 0 ? "is-parent-bar" : ""} ${r.depth > 0 ? "is-sub-bar" : ""}`;
    taskPos[t.id] = { left: firstLeft, right: lastRight, y: yMid };

    const totalWorkMs = segs.reduce((a, g) => a + Math.max(1, g.eMs - g.sMs), 0);
    const doneWorkMs = totalWorkMs * ((t.progress || 0) / 100);
    let accWorkMs = 0;
    const holTip = sp.hits.length
      ? `｜跨越休假：${sp.hits.join("、")}（扣除 ${fmtDurationMinutes(sp.holidayOverlapMs / 60000)}，淨工期 ${fmtDurationMinutes(sp.workMs / 60000)}）`
      : "";
    const fullTip = `${t.title} (${fmtDisplayDT(t.start)} ~ ${fmtDisplayDT(t.end)})｜${sem.title}｜${vInfo.fullLabel}${holTip}`;

    const bridgesHtml = sp.bridges.map(br => {
      const bL = msToX(br.sMs);
      const bW = Math.max(4, msToX(br.eMs) - bL);
      const showTag = bW >= 34;
      return `<div class="gantt-split-bridge" data-bridge-of="${esc(t.id)}" style="left:${bL}px;width:${bW}px;" title="休假截斷：${esc(br.label)}">${showTag ? `<span class="gantt-split-bridge-tag">休假</span>` : ""}</div>`;
    }).join("");

    const barsHtml = segs.map((seg, si) => {
      const segDur = Math.max(1, seg.eMs - seg.sMs);
      const fillMs = Math.max(0, Math.min(segDur, doneWorkMs - accWorkMs));
      accWorkMs += segDur;
      const segProg = Math.round((fillMs / segDur) * 100);
      const segL = msToX(seg.sMs);
      const segW = Math.max(10, msToX(seg.eMs) - segL);
      const isFirst = si === 0;
      const isLast = si === segs.length - 1;
      const segRadius = segs.length > 1
        ? (isFirst ? "6px 2px 2px 6px" : (isLast ? "2px 6px 6px 2px" : "2px"))
        : "6px";
      const segLabel = segs.length > 1
        ? (isFirst ? `${t.title} (${t.progress}%)` : `續：${t.title}`)
        : `${t.title} (${t.progress}%)`;
      const warnBorder = sp.fullyInHoliday ? "border:1.5px dashed #fecdd3;" : "";
      return `
        <div class="gantt-bar ${barCls}" data-id="${esc(t.id)}" data-seg="${si}" data-seg-count="${segs.length}" style="left:${segL}px;width:${segW}px;background:${sem.bg};color:${sem.fg};border-radius:${segRadius};${warnBorder}" title="${esc(fullTip)}">
          <div class="gantt-bar-prog" style="width:${segProg}%;"></div>
          ${isFirst ? '<div class="resize-handle resize-l" data-mode="resize-l"></div>' : ""}
          <span class="gantt-bar-text">${esc(segLabel)}</span>
          ${isLast ? '<div class="resize-handle resize-r" data-mode="resize-r"></div>' : ""}
        </div>
      `;
    }).join("");

    return `
      <div class="gantt-grid-row" data-row-id="${esc(t.id)}" style="width:${totalW}px;">
        ${actualMarkerHtml}
        ${bridgesHtml}
        ${barsHtml}
      </div>
    `;
  }).join("");

  let pathsHtml = "";
  tasks.forEach(t => {
    if (t.dep && taskPos[t.dep] && taskPos[t.id]) {
      const p1 = taskPos[t.dep], p2 = taskPos[t.id];
      const midX = p1.right + 10;
      const dPath = p2.left >= p1.right + 12
        ? `M ${p1.right} ${p1.y} L ${midX} ${p1.y} L ${midX} ${p2.y} L ${p2.left - 3} ${p2.y}`
        : `M ${p1.right} ${p1.y} L ${p1.right + 10} ${p1.y} L ${p1.right + 10} ${(p1.y + p2.y) / 2} L ${p2.left - 12} ${(p1.y + p2.y) / 2} L ${p2.left - 12} ${p2.y} L ${p2.left - 3} ${p2.y}`;
      pathsHtml += `<path d="${dPath}" fill="none" stroke="#64748b" stroke-width="1.5" marker-end="url(#arrow)" />`;
    }
  });

  const holidayBandsHtml = getMergedHolidayIntervals().map(h => {
    if (h.eMs <= tl.rangeStartMs || h.sMs >= tl.rangeEndMs) return "";
    const hL = msToX(Math.max(h.sMs, tl.rangeStartMs));
    const hR = msToX(Math.min(h.eMs, tl.rangeEndMs));
    const hW = Math.max(3, hR - hL);
    const label = h.names.join(" / ");
    const showLbl = hW >= 26;
    return `<div class="gantt-holiday-band" style="left:${hL}px;width:${hW}px;" title="休假日：${esc(label)} (${h.start} ~ ${h.end})">${showLbl ? `<span class="gantt-holiday-tag">休 ${esc(label)}</span>` : ""}</div>`;
  }).join("");

  const nowLeft = msToX(nowMs);
  const svgH = Math.max(200, tasks.length * rowH);
  const snapHint = zoomMode === "day"
    ? "日檢視（每格2小時，拖曳自動對齊30分鐘）"
    : (zoomMode === "quarter"
      ? "季檢視（3個月一季，拖曳以天為單位並保留原時分，按住 Shift 可調小時）"
      : `${ZOOM_LABEL[zoomMode]}檢視（拖曳以天為單位並保留原時分，按住 Shift 可調小時）`);

  document.getElementById("mainArea").innerHTML = `
    <div class="gantt-toolbar">
      <span class="pm-pill-info">${esc(getProjectManagerLabel(proj))}</span>
      <button onclick="expandAllSubtasks()" style="padding:2px 8px;">▾ 全部展開</button>
      <button onclick="collapseAllSubtasks()" style="padding:2px 8px;">▸ 全部收合</button>
      <span>${esc(snapHint)}${tl.dayModeCapped ? "（跨度較大，日檢視顯示近期 60 天）" : ""}</span>
      <span style="margin-left:auto;">縮放：</span>
      <div class="zoom-group">
        <button class="zoom-btn ${zoomMode === "day" ? "active" : ""}" onclick="setZoomMode('day')">日</button>
        <button class="zoom-btn ${zoomMode === "week" ? "active" : ""}" onclick="setZoomMode('week')">週</button>
        <button class="zoom-btn ${zoomMode === "month" ? "active" : ""}" onclick="setZoomMode('month')">月</button>
        <button class="zoom-btn ${zoomMode === "quarter" ? "active" : ""}" onclick="setZoomMode('quarter')" title="3個月一季顯示">季</button>
        <button class="zoom-btn ${zoomMode === "year" ? "active" : ""}" onclick="setZoomMode('year')">年</button>
      </div>
      <button onclick="scrollToToday()" style="padding:3px 9px;">跳至現在</button>
    </div>
    <div class="gantt-wrap">
      <div class="gantt-left">
        <div class="gantt-left-head">任務階層 / 連結 / 底色標記 / 進度</div>
        <div class="gantt-left-body" id="ganttLeftBody">${leftRows || '<div style="padding:16px;color:var(--muted);font-size:13px;">此專案尚無任務</div>'}</div>
      </div>
      <div class="gantt-splitter" id="ganttSplitter" title="拖曳調整左欄寬度（雙擊回復預設）"></div>
      <div class="gantt-right" id="ganttRight">
        <div class="gantt-canvas" style="width:${totalW}px;">
          <div class="gantt-header">
            <div class="gantt-months">${topHtml}</div>
            <div class="gantt-days">${botHtml}</div>
          </div>
          <div class="gantt-grid" id="ganttGrid" style="width:${totalW}px;min-height:${svgH}px;">
            <div class="gantt-bg-layer" style="width:${totalW}px;">${bgLayerHtml}</div>
            <div class="gantt-holiday-layer" style="width:${totalW}px;">${holidayBandsHtml}</div>
            <div class="today-line" style="left:${nowLeft}px;" title="現在時間：${esc(fmtDisplayDT(nowDateTimeStr()))}"></div>
            <svg class="gantt-svg" width="${totalW}" height="${svgH}">
              <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="#64748b"/></marker></defs>
              ${pathsHtml}
            </svg>
            ${rightRows}
          </div>
        </div>
      </div>
    </div>
  `;

  const rightEl = document.getElementById("ganttRight");
  const leftEl = document.getElementById("ganttLeftBody");
  rightEl.addEventListener("scroll", () => { leftEl.scrollTop = rightEl.scrollTop; });
  rightEl.scrollLeft = Math.max(0, nowLeft - 220);

  wireGanttSplitter();

  leftEl.querySelectorAll(".gantt-row-label").forEach(el => {
    el.addEventListener("click", e => {
      if (e.target.closest(".tree-toggle, .sub-add-btn, .link-icon-btn")) return;
      openTaskModal(el.dataset.id);
    });
  });

  rightEl.querySelectorAll(".gantt-bar, .gantt-ms-wrap").forEach(item => {
    item.addEventListener("pointerdown", e => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      const id = item.dataset.id;
      const task = allTasks.find(t => t.id === id);
      if (!task) return;
      if (!canEditTask(task, proj)) {
        openTaskModal(id);
        return;
      }
      const isMs = item.dataset.ms === "1";
      const mode = isMs ? "move" : (e.target.dataset.mode || "move");
      const segCount = Number(item.dataset.segCount || 1);
      const rowEl = item.closest(".gantt-grid-row");
      const groupEls = (!isMs && segCount > 1 && rowEl)
        ? Array.from(rowEl.querySelectorAll(".gantt-bar, .gantt-split-bridge"))
        : [item];
      dragState = {
        id, task, el: item, groupEls, segCount, mode, isMs,
        startX: e.clientX,
        origLeft: parseFloat(item.style.left) || 0,
        origWidth: parseFloat(item.style.width) || 12,
        origStart: ensureDateTimeStr(task.start, "09:00"),
        origEnd: ensureDateTimeStr(task.end || task.start, "18:00"),
        newStart: ensureDateTimeStr(task.start, "09:00"),
        newEnd: ensureDateTimeStr(task.end || task.start, "18:00"),
        deltaMins: 0,
      };
    });
  });
}

window.setZoomMode = function(m) {
  if (!ZOOM_LABEL[m]) return;
  zoomMode = m;
  try { localStorage.setItem("pm_lan_zoom_mode", zoomMode); } catch (_) {}
  renderGantt();
};
window.scrollToToday = function() {
  const rightEl = document.getElementById("ganttRight");
  const line = document.querySelector(".today-line");
  if (rightEl && line) rightEl.scrollLeft = Math.max(0, parseFloat(line.style.left) - 220);
};

document.addEventListener("pointermove", e => {
  if (!dragState) return;
  const dx = e.clientX - dragState.startX;
  const rawMins = (dx / ganttMetrics.pxPerHour) * 60;
  const snap = (zoomMode !== "day" && e.shiftKey) ? 60 : ganttMetrics.snapMins;
  let deltaMins = Math.round(rawMins / snap) * snap;
  const minDur = Math.min(snap, 30);

  let ns = dragState.origStart;
  let ne = dragState.origEnd;

  if (dragState.mode === "move") {
    ns = addMinutes(dragState.origStart, deltaMins);
    ne = dragState.isMs ? ns : addMinutes(dragState.origEnd, deltaMins);
  } else if (dragState.mode === "resize-r") {
    const origDur = diffMinutes(dragState.origStart, dragState.origEnd);
    const clampedDur = Math.max(minDur, origDur + deltaMins);
    deltaMins = clampedDur - origDur;
    ne = addMinutes(dragState.origStart, clampedDur);
  } else if (dragState.mode === "resize-l") {
    const origDur = diffMinutes(dragState.origStart, dragState.origEnd);
    const maxShift = Math.max(0, origDur - minDur);
    deltaMins = Math.min(maxShift, deltaMins);
    ns = addMinutes(dragState.origStart, deltaMins);
  }

  dragState.deltaMins = deltaMins;
  dragState.newStart = ns;
  dragState.newEnd = ne;

  const sMs = parseDateTime(ns).getTime();
  const eMs = parseDateTime(ne).getTime();
  if (dragState.isMs) {
    dragState.el.style.left = (msToX(sMs) - 9) + "px";
  } else if (dragState.segCount > 1) {
    const shiftPx = (deltaMins / 60) * ganttMetrics.pxPerHour;
    if (dragState.mode === "move") {
      dragState.groupEls.forEach(g => { g.style.transform = `translateX(${shiftPx}px)`; });
    } else if (dragState.mode === "resize-l") {
      const newL = dragState.origLeft + shiftPx;
      const newW = Math.max(10, dragState.origWidth - shiftPx);
      dragState.el.style.left = newL + "px";
      dragState.el.style.width = newW + "px";
    } else if (dragState.mode === "resize-r") {
      dragState.el.style.width = Math.max(10, dragState.origWidth + shiftPx) + "px";
    }
  } else {
    const left = msToX(sMs);
    const width = Math.max(12, msToX(eMs) - left);
    dragState.el.style.left = left + "px";
    dragState.el.style.width = width + "px";
  }

  const tip = document.getElementById("dragTip");
  tip.style.display = "block";
  tip.style.left = (e.clientX + 12) + "px";
  tip.style.top = (e.clientY + 12) + "px";
  tip.textContent = dragState.isMs ? `◆ ${fmtDisplayDT(ns)}` : `${fmtDisplayDT(ns)} ~ ${fmtDisplayDT(ne)}`;
});

document.addEventListener("pointerup", async () => {
  if (!dragState) return;
  const ds = dragState;
  dragState = null;
  document.getElementById("dragTip").style.display = "none";

  if (ds.deltaMins === 0) {
    if (ds.mode === "move") openTaskModal(ds.id);
    return;
  }
  const updated = { ...ds.task, start: ds.newStart, end: ds.isMs ? ds.newStart : ds.newEnd };
  await apiPost("/api/task", { task: updated });
});

document.addEventListener("pointercancel", () => {
  if (!dragState) return;
  dragState = null;
  const tip = document.getElementById("dragTip");
  if (tip) tip.style.display = "none";
});


function renderKanban() {
  const proj = getActiveProject();
  const allTasks = proj.tasks || [];
  const tasks = getFilteredTasks();
  const tree = buildTaskTreeRows(allTasks, tasks, false);
  const cols = ["todo", "doing", "review", "done"];
  const html = cols.map(st => {
    const list = tasks.filter(t => t.status === st).sort(compareTasksByStart);
    const cards = list.map(t => {
      const linksHtml = renderLinkChipsHtml(t.fileLink);
      const dateText = t.isMilestone ? `◆ ${fmtDisplayDT(t.start, true)}` : `${fmtDisplayDT(t.start, true)} ~ ${fmtDisplayDT(t.end, true)}`;
      const editable = canEditTask(t, proj);
      const sem = getTaskSemanticColor(t);
      const vInfo = calcScheduleVariance(t);
      const vBg = getPaletteColor("schedule", vInfo.key);
      const parentObj = t.parentId ? tree.byId.get(t.parentId) : null;
      const subStat = tree.statsMap.get(t.id) || { total: 0, done: 0 };
      return `
        <div class="k-card ${t.isMilestone ? "milestone" : ""}" draggable="${editable ? "true" : "false"}" data-id="${esc(t.id)}" style="border-left-color:${sem.bg};">
          ${parentObj ? `<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">↳ 屬於：${esc(parentObj.title)}</div>` : ""}
          <div class="k-card-title">
            ${t.isMilestone ? '<span class="ms-badge">◆ 里程碑</span>' : ""}
            <span>${esc(t.title)}</span>
          </div>
          <div class="k-card-meta" style="gap:6px;flex-wrap:wrap;">
            <span class="color-pill" style="background:${sem.bg};color:${sem.fg};" title="${esc(sem.title)}">${esc(sem.badge)}</span>
            ${(colorMode !== "assignee" && colorMode !== "task") ? `<span class="member-pill" style="background:${memberColor(t.assignee)};color:${contrastTextColor(memberColor(t.assignee))};">${esc(t.assignee)}</span>` : ""}
            <span style="margin-left:auto;font-size:11px;">${esc(dateText)}</span>
          </div>
          <div style="display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap;font-size:11px;">
            <span class="color-pill" style="background:${vBg};color:${contrastTextColor(vBg)};" title="${esc(vInfo.fullLabel)}">${esc(vInfo.shortLabel)}</span>
            ${t.actualEnd ? `<span style="color:var(--muted);">實際：${esc(fmtDisplayDT(t.actualEnd, true))}</span>` : ""}
            ${subStat.total > 0 ? `<span class="sub-count-badge">子任務 ${subStat.done}/${subStat.total}</span>` : ""}
          </div>
          ${linksHtml ? `<div class="k-card-links">${linksHtml}</div>` : ""}
          ${t.isMilestone ? "" : `<div class="prog-mini"><div style="width:${t.progress}%"></div></div>`}
        </div>
      `;
    }).join("");
    return `
      <div class="kanban-col" data-status="${st}">
        <div class="kanban-head"><span>${STATUS_MAP[st]}</span><span>${list.length}</span></div>
        <div class="kanban-cards">${cards}</div>
      </div>
    `;
  }).join("");
  document.getElementById("mainArea").innerHTML = `<div class="kanban-wrap">${html}</div>`;

  document.querySelectorAll(".k-card").forEach(card => {
    card.addEventListener("click", () => openTaskModal(card.dataset.id));
    card.addEventListener("dragstart", e => {
      const task = allTasks.find(t => t.id === card.dataset.id);
      if (!canEditTask(task, proj)) { e.preventDefault(); return; }
      e.dataTransfer.setData("text/plain", card.dataset.id);
    });
  });
  document.querySelectorAll(".kanban-col").forEach(col => {
    col.addEventListener("dragover", e => { e.preventDefault(); col.classList.add("drag-over"); });
    col.addEventListener("dragleave", () => col.classList.remove("drag-over"));
    col.addEventListener("drop", async e => {
      e.preventDefault();
      col.classList.remove("drag-over");
      const id = e.dataTransfer.getData("text/plain");
      const task = allTasks.find(t => t.id === id);
      if (!task || task.status === col.dataset.status) return;
      if (!canEditTask(task, proj)) {
        showToast("目前身分無權限移動此任務");
        return;
      }
      const updated = { ...task, status: col.dataset.status };
      if (updated.status === "done") {
        updated.progress = 100;
        if (!updated.actualEnd) updated.actualEnd = nowDateTimeStr();
      }
      await apiPost("/api/task", { task: updated });
    });
  });
}

function renderList() {
  const proj = getActiveProject();
  const allTasks = proj.tasks || [];
  const tree = buildTaskTreeRows(allTasks, getFilteredTasks(), true);
  const canAddSub = canCreateTask();

  const rows = tree.rows.map(r => {
    const t = r.task;
    const linksHtml = renderLinkChipsHtml(t.fileLink) || '<span style="color:#9ca3af;">—</span>';
    const dateText = t.isMilestone ? `◆ ${fmtDisplayDT(t.start)}` : `${fmtDisplayDT(t.start)} ~ ${fmtDisplayDT(t.end)}`;
    const editable = canEditTask(t, proj);
    const vInfo = calcScheduleVariance(t);
    const vBg = getPaletteColor("schedule", vInfo.key);
    const stBg = getPaletteColor("status", t.status);
    const prBg = getPaletteColor("priority", t.priority);
    const asBg = memberColor(t.assignee);
    const ownBg = getTaskOwnColor(t);

    const toggleBtn = r.hasChildren
      ? `<button type="button" class="tree-toggle" onclick="toggleSubtasks('${esc(t.id)}', event)">${r.isCollapsed ? "▸" : "▾"}</button>`
      : `<span class="tree-spacer">${r.depth > 0 ? "└" : ""}</span>`;
    const subBadge = r.totalChildren > 0
      ? `<span class="sub-count-badge">${r.doneChildren}/${r.totalChildren}</span>`
      : "";
    const colorDot = `<span style="width:10px;height:10px;border-radius:3px;background:${ownBg};display:inline-block;flex-shrink:0;" title="任務專屬底色"></span>`;

    return `
      <tr style="${r.depth > 0 ? "background:var(--row-sub-bg);" : ""}">
        <td>
          <div style="display:flex;align-items:center;gap:6px;padding-left:${r.depth * 20}px;">
            ${toggleBtn}
            ${colorDot}
            ${t.isMilestone ? '<span class="ms-badge">◆</span>' : ""}
            <strong>${esc(t.title)}</strong>
            ${subBadge}
            ${(() => {
              if (t.isMilestone) return "";
              const sp = splitTaskByHolidaysMs(parseDateTime(t.start, "09:00").getTime(), parseDateTime(t.end || t.start, "18:00").getTime(), t.splitByHolidays !== false);
              return sp.hits.length ? `<span class="holiday-badge" title="跨越休假：${esc(sp.hits.join("、"))}（休假 ${fmtDurationMinutes(sp.holidayOverlapMs / 60000)}，淨工期 ${fmtDurationMinutes(sp.workMs / 60000)}）">✂ 跨假</span>` : "";
            })()}
            ${canAddSub ? `<button type="button" class="sub-add-btn" onclick="openTaskModal(null, '${esc(t.id)}')" title="新增子任務">＋子項</button>` : ""}
          </div>
          ${t.notes ? `<div style="font-size:12px;color:var(--muted);margin-top:2px;padding-left:${r.depth * 20 + 26}px;">${esc(t.notes)}</div>` : ""}
        </td>
        <td data-label="負責人"><span class="color-pill" style="background:${asBg};color:${contrastTextColor(asBg)};">${esc(t.assignee)}</span></td>
        <td data-label="狀態"><span class="color-pill" style="background:${stBg};color:${contrastTextColor(stBg)};">${STATUS_MAP[t.status] || t.status}</span></td>
        <td data-label="優先級"><span class="color-pill" style="background:${prBg};color:${contrastTextColor(prBg)};">${PRIO_MAP[t.priority] || t.priority}</span></td>
        <td data-label="預期時間" style="white-space:nowrap;font-size:12px;">${esc(dateText)}</td>
        <td data-label="實際完成" style="white-space:nowrap;font-size:12px;">${t.actualEnd ? esc(fmtDisplayDT(t.actualEnd)) : '<span style="color:#9ca3af;">—</span>'}</td>
        <td data-label="偏差"><span class="color-pill" style="background:${vBg};color:${contrastTextColor(vBg)};" title="${esc(vInfo.fullLabel)}">${esc(vInfo.shortLabel)}</span></td>
        <td data-label="進度">${t.isMilestone ? (t.status === "done" ? "已達成" : "未達成") : `${t.progress}%`}</td>
        <td data-label="檔案連結"><div style="display:flex;flex-wrap:wrap;gap:4px;">${linksHtml}</div></td>
        <td data-label="操作"><button onclick="openTaskModal('${esc(t.id)}')">${editable ? "編輯" : "檢視"}</button></td>
      </tr>
    `;
  }).join("");

  document.getElementById("mainArea").innerHTML = `
    <div class="panel-wrap">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <button onclick="expandAllSubtasks()">▾ 全部展開</button>
        <button onclick="collapseAllSubtasks()">▸ 全部收合</button>
        <span style="font-size:12px;color:var(--muted);">共顯示 ${tree.rows.length} 筆項目（含小時與分鐘時間、子任務收合與實際完成比較）</span>
      </div>
      <table class="list-table">
        <thead>
          <tr>
            <th>任務 / 子任務階層</th><th>負責人</th><th>狀態</th><th>優先級</th>
            <th>預期時間</th><th>實際完成時間</th><th>預期 vs 實際</th><th>進度</th><th>檔案連結</th><th>操作</th>
          </tr>
        </thead>
        <tbody>${rows || '<tr><td colspan="10" style="text-align:center;color:var(--muted);">尚無任務</td></tr>'}</tbody>
      </table>
    </div>
  `;
}

function renderAnalysis() {
  const proj = getActiveProject();
  const allTasks = proj.tasks || [];
  const filtered = getFilteredTasks();
  const tree = buildTaskTreeRows(allTasks, filtered, false);

  let total = filtered.length;
  let doneCount = 0;
  let datedDone = 0;
  let earlyCount = 0;
  let ontimeCount = 0;
  let lateCount = 0;
  let overdueCount = 0;
  let deltaMinsSum = 0;

  const byMember = {};
  memberNames().forEach(n => {
    byMember[n] = { name: n, total: 0, done: 0, early: 0, ontime: 0, late: 0, overdue: 0, deltaMinsSum: 0, datedDone: 0 };
  });

  filtered.forEach(t => {
    const v = calcScheduleVariance(t);
    if (!byMember[t.assignee]) {
      byMember[t.assignee] = { name: t.assignee, total: 0, done: 0, early: 0, ontime: 0, late: 0, overdue: 0, deltaMinsSum: 0, datedDone: 0 };
    }
    const m = byMember[t.assignee];
    m.total++;
    if (t.status === "done" || t.actualEnd) {
      doneCount++;
      m.done++;
    }
    if (t.actualEnd) {
      datedDone++;
      m.datedDone++;
      deltaMinsSum += v.deltaMins;
      m.deltaMinsSum += v.deltaMins;
      if (v.key === "early") { earlyCount++; m.early++; }
      else if (v.key === "ontime") { ontimeCount++; m.ontime++; }
      else if (v.key === "late") { lateCount++; m.late++; }
    } else if (v.key === "overdue") {
      overdueCount++;
      m.overdue++;
    }
  });

  const completionRate = total ? Math.round((doneCount / total) * 100) : 0;
  const ontimeRate = datedDone ? Math.round(((earlyCount + ontimeCount) / datedDone) * 100) : null;
  const avgMins = datedDone ? Math.round(deltaMinsSum / datedDone) : null;
  const avgDeltaText = avgMins === null
    ? "尚無資料"
    : (avgMins === 0 ? "準時 0 分" : (avgMins < 0 ? `平均提前 ${fmtDurationMinutes(Math.abs(avgMins))}` : `平均延遲 +${fmtDurationMinutes(avgMins)}`));
  const avgDeltaColor = avgMins === null ? "#64748b" : (avgMins <= 0 ? "#0d9488" : "#dc2626");

  const memberRowsHtml = Object.values(byMember).filter(m => m.total > 0).map(m => {
    const avgM = m.datedDone ? Math.round(m.deltaMinsSum / m.datedDone) : null;
    const avgStr = avgM === null ? "—" : (avgM === 0 ? "準時" : (avgM < 0 ? `提前 ${fmtDurationMinutes(Math.abs(avgM))}` : `延遲 +${fmtDurationMinutes(avgM)}`));
    const asBg = memberColor(m.name);
    return `
      <tr>
        <td><span class="color-pill" style="background:${asBg};color:${contrastTextColor(asBg)};">${esc(m.name)}</span></td>
        <td>${m.total}</td>
        <td>${m.done} (${Math.round((m.done / m.total) * 100)}%)</td>
        <td style="color:#0d9488;font-weight:700;">${m.early}</td>
        <td style="color:#16a34a;font-weight:700;">${m.ontime}</td>
        <td style="color:#ea580c;font-weight:700;">${m.late}</td>
        <td style="color:#dc2626;font-weight:700;">${m.overdue}</td>
        <td style="font-weight:700;color:${avgM !== null && avgM > 0 ? "#dc2626" : "#0f766e"};">${avgStr}</td>
      </tr>
    `;
  }).join("");

  const detailRows = tree.rows.filter(r => {
    const v = calcScheduleVariance(r.task);
    if (analysisFilter === "risk") return v.key === "late" || v.key === "overdue";
    if (analysisFilter === "good") return v.key === "early" || v.key === "ontime";
    if (analysisFilter === "open") return r.task.status !== "done" && !r.task.actualEnd;
    return true;
  });

  const detailHtml = detailRows.map(r => {
    const t = r.task;
    const v = calcScheduleVariance(t);
    const vBg = getPaletteColor("schedule", v.key);
    const asBg = memberColor(t.assignee);
    const planMins = Math.max(0, diffMinutes(t.start, t.end || t.start, "09:00", "18:00"));
    const actualMins = t.actualEnd ? Math.max(0, diffMinutes(t.start, t.actualEnd, "09:00", "18:00")) : null;
    const barPct = v.deltaMins !== null ? Math.min(100, Math.round((Math.abs(v.deltaMins) / 1440) * 20)) : 0;
    const editable = canEditTask(t, proj);
    return `
      <tr>
        <td>
          <div style="padding-left:${r.depth * 18}px;display:flex;align-items:center;gap:6px;">
            ${r.depth > 0 ? '<span style="color:#94a3b8;">└</span>' : ""}
            ${t.isMilestone ? '<span class="ms-badge">◆</span>' : ""}
            <strong>${esc(t.title)}</strong>
          </div>
        </td>
        <td><span class="color-pill" style="background:${asBg};color:${contrastTextColor(asBg)};">${esc(t.assignee)}</span></td>
        <td style="white-space:nowrap;font-size:12px;">${esc(fmtDisplayDT(t.start))} ~ ${esc(fmtDisplayDT(t.end))} <span style="color:var(--muted);font-size:11px;">(${fmtDurationMinutes(planMins)})</span></td>
        <td style="white-space:nowrap;font-size:12px;">${t.actualEnd ? `${esc(fmtDisplayDT(t.actualEnd))} <span style="color:var(--muted);font-size:11px;">(${fmtDurationMinutes(actualMins)})</span>` : '<span style="color:#9ca3af;">尚未完成</span>'}</td>
        <td>
          <div class="var-bar-wrap">
            <span class="color-pill" style="background:${vBg};color:${contrastTextColor(vBg)};" title="${esc(v.fullLabel)}">${esc(v.shortLabel)}</span>
            ${barPct > 0 ? `<div class="var-bar-track"><div class="var-bar-fill" style="width:${barPct}%;background:${vBg};"></div></div>` : ""}
          </div>
        </td>
        <td><button onclick="openTaskModal('${esc(t.id)}')">${editable ? (t.actualEnd ? "編輯" : "補填時間") : "檢視"}</button></td>
      </tr>
    `;
  }).join("");

  document.getElementById("mainArea").innerHTML = `
    <div class="panel-wrap">
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-label">專案完成進度</div>
          <div class="kpi-val">${doneCount} / ${total} <span style="font-size:14px;color:var(--primary);">(${completionRate}%)</span></div>
          <div class="kpi-sub">已填寫實際完成時間：${datedDone} 筆</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">準時／提前達成率</div>
          <div class="kpi-val" style="color:${ontimeRate !== null && ontimeRate >= 70 ? "#16a34a" : "#d97706"};">${ontimeRate !== null ? ontimeRate + "%" : "—"}</div>
          <div class="kpi-sub">提前 ${earlyCount} 筆 ・ 準時 ${ontimeCount} 筆 ・ 延遲 ${lateCount} 筆</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">預期 vs 實際平均偏差</div>
          <div class="kpi-val" style="font-size:19px;color:${avgDeltaColor};">${avgDeltaText}</div>
          <div class="kpi-sub">精準計算至小時與分鐘</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">進行中逾期項目</div>
          <div class="kpi-val" style="color:${overdueCount > 0 ? "#dc2626" : "#16a34a"};">${overdueCount} 筆</div>
          <div class="kpi-sub">${overdueCount > 0 ? "尚未完成且已超過預期結束時間" : "目前無逾期項目"}</div>
        </div>
      </div>

      <div class="analysis-section">
        <div class="analysis-head"><h4>👥 依負責人準時與偏差統計</h4></div>
        <table>
          <thead>
            <tr><th>負責人</th><th>總任務</th><th>已完成</th><th>提前完成</th><th>準時完成</th><th>延遲完成</th><th>進行中逾期</th><th>平均偏差</th></tr>
          </thead>
          <tbody>${memberRowsHtml || '<tr><td colspan="8" style="text-align:center;color:var(--muted);">尚無資料</td></tr>'}</tbody>
        </table>
      </div>

      <div class="analysis-section">
        <div class="analysis-head">
          <h4>📅 預期 vs. 實際完成時間明細分析</h4>
          <div class="filter-pills">
            <button class="filter-pill ${analysisFilter === "all" ? "active" : ""}" onclick="setAnalysisFilter('all')">全部 (${tree.rows.length})</button>
            <button class="filter-pill ${analysisFilter === "risk" ? "active" : ""}" onclick="setAnalysisFilter('risk')">延遲／逾期</button>
            <button class="filter-pill ${analysisFilter === "good" ? "active" : ""}" onclick="setAnalysisFilter('good')">提前／準時</button>
            <button class="filter-pill ${analysisFilter === "open" ? "active" : ""}" onclick="setAnalysisFilter('open')">尚未完成</button>
          </div>
        </div>
        <table>
          <thead>
            <tr><th>任務 / 子任務</th><th>負責人</th><th>預期時間（預期歷時）</th><th>實際完成時間（實際歷時）</th><th>預期 vs 實際差異</th><th>操作</th></tr>
          </thead>
          <tbody>${detailHtml || '<tr><td colspan="6" style="text-align:center;color:var(--muted);">沒有符合篩選條件的任務</td></tr>'}</tbody>
        </table>
      </div>
    </div>
  `;
}

window.setAnalysisFilter = function(f) {
  analysisFilter = f;
  renderAnalysis();
};

function renderLogs() {
  const html = (state.logs || []).map(l => `
    <div class="log-item">
      <span class="log-time">${esc(l.time)}</span>
      <span class="log-proj">${esc(l.projectName || "系統")}</span>
      <span class="member-pill" style="background:${memberColor(l.user)}">${esc(l.user)}</span>
      <span>${esc(l.text)}</span>
    </div>
  `).join("");
  document.getElementById("mainArea").innerHTML = `<div class="panel-wrap">${html}</div>`;
}

// ---------- Identity / Login Modal ----------
function openLoginModal() {
  selectedLoginMemberId = null;
  document.getElementById("loginPinBox").classList.remove("show");
  document.getElementById("loginPinInput").value = "";

  const me = getMe();
  const grid = document.getElementById("loginMemberGrid");
  grid.innerHTML = (state.members || []).map(m => {
    const isCur = me && me.id === m.id;
    return `
      <div class="member-card ${isCur ? "selected" : ""}" data-mid="${esc(m.id)}">
        <div class="member-card-name">
          <span>${esc(m.name)}</span>
          <span>${m.hasPin ? "🔒" : ""}</span>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <span class="role-tag role-${m.role}">${ROLE_LABEL[m.role] || m.role}</span>
          ${isCur ? '<span style="font-size:11px;color:var(--primary);font-weight:700;">使用中</span>' : ""}
        </div>
      </div>
    `;
  }).join("");

  grid.querySelectorAll(".member-card").forEach(card => {
    card.addEventListener("click", async () => {
      const m = state.members.find(x => x.id === card.dataset.mid);
      if (!m) return;
      grid.querySelectorAll(".member-card").forEach(c => c.classList.remove("selected"));
      card.classList.add("selected");
      selectedLoginMemberId = m.id;
      if (!m.hasPin) {
        await submitLogin(m.id, "");
      } else {
        document.getElementById("loginPinTarget").textContent = m.name;
        document.getElementById("loginPinBox").classList.add("show");
        document.getElementById("loginPinInput").value = "";
        document.getElementById("loginPinInput").focus();
      }
    });
  });

  const myPinSec = document.getElementById("myPinSection");
  if (me) {
    myPinSec.style.display = "block";
    document.getElementById("myPinUser").textContent = me.name;
    document.getElementById("oldPinWrap").style.display = me.hasPin ? "flex" : "none";
    document.getElementById("oldPinInput").value = "";
    document.getElementById("newPinInput").value = "";
  } else {
    myPinSec.style.display = "none";
  }

  document.getElementById("loginModal").classList.add("open");
}

async function submitLogin(memberId, pin) {
  const res = await apiPost("/api/login", { memberId, pin });
  if (res && res.ok) {
    localStorage.setItem("pm_lan_last_member_id", memberId);
    document.getElementById("loginModal").classList.remove("open");
    showToast(`已切換身分為「${state.me.name}（${ROLE_LABEL[state.me.role]}）」`);
  }
}

document.getElementById("btnIdentity").addEventListener("click", openLoginModal);
document.getElementById("btnBannerLogin").addEventListener("click", openLoginModal);
document.getElementById("btnCloseLogin").addEventListener("click", () => {
  document.getElementById("loginModal").classList.remove("open");
});
document.getElementById("btnConfirmPin").addEventListener("click", () => {
  if (selectedLoginMemberId) {
    submitLogin(selectedLoginMemberId, document.getElementById("loginPinInput").value);
  }
});
document.getElementById("loginPinInput").addEventListener("keydown", e => {
  if (e.key === "Enter" && selectedLoginMemberId) {
    submitLogin(selectedLoginMemberId, e.target.value);
  }
});
document.getElementById("btnGuestBrowse").addEventListener("click", async () => {
  await apiPost("/api/logout", {});
  authToken = "";
  localStorage.removeItem("pm_lan_token");
  localStorage.removeItem("pm_lan_last_member_id");
  document.getElementById("loginModal").classList.remove("open");
  showToast("已切換為唯讀訪客瀏覽模式");
});
document.getElementById("btnSaveMyPin").addEventListener("click", async () => {
  const oldPin = document.getElementById("oldPinInput").value;
  const newPin = document.getElementById("newPinInput").value;
  const res = await apiPost("/api/my-pin", { oldPin, newPin });
  if (res && res.ok) {
    showToast("您的登入 PIN 碼已更新");
    document.getElementById("loginModal").classList.remove("open");
  }
});

// ---------- Task Modal ----------
function updateMilestoneFormUI() {
  const isMs = document.getElementById("fMilestone").checked;
  document.getElementById("lblStart").textContent = isMs ? "預期里程碑時間" : "預期開始時間";
  document.getElementById("endDateRow").style.display = isMs ? "none" : "flex";
  document.getElementById("splitHolidayWrap").style.display = isMs ? "none" : "flex";
  if (isMs) document.getElementById("fEnd").value = document.getElementById("fStart").value;
  updateVariancePreview();
}
function updateHolidayAlertInModal() {
  const box = document.getElementById("fHolidayAlert");
  const isMs = document.getElementById("fMilestone").checked;
  if (isMs) { box.classList.remove("show"); return; }
  const start = document.getElementById("fStart").value;
  const end = document.getElementById("fEnd").value;
  if (!start || !end) { box.classList.remove("show"); return; }
  const sMs = parseDateTime(start, "09:00").getTime();
  const eMs = parseDateTime(end, "18:00").getTime();
  if (eMs <= sMs) { box.classList.remove("show"); return; }
  const splitOn = document.getElementById("fSplitHolidays").checked;
  const sp = splitTaskByHolidaysMs(sMs, eMs, splitOn);
  if (!sp.hits.length) { box.classList.remove("show"); return; }

  const editable = !document.getElementById("fTitle").disabled;
  const ovTxt = fmtDurationMinutes(sp.holidayOverlapMs / 60000);
  const wkTxt = fmtDurationMinutes(sp.workMs / 60000);
  const segNote = sp.segments.length >= 2
    ? `甘特圖將自動截斷為 <strong>${sp.segments.length} 段</strong>顯示`
    : (sp.fullyInHoliday ? "整段期間皆落在休假日內" : "未開啟截斷顯示");
  document.getElementById("fHolidayAlertText").innerHTML =
    `📅 此任務跨越休假日 <strong>「${esc(sp.hits.join("、"))}」</strong>（重疊休假 ${esc(ovTxt)}，淨工作時間 ${esc(wkTxt)}；${segNote}）。`;
  document.getElementById("fHolidayAlertActs").style.display = editable ? "flex" : "none";
  document.getElementById("btnHardSplitTask").style.display = (editable && editingTaskId && sp.segments.length >= 2) ? "inline-block" : "none";
  box.classList.add("show");
}
function updateLinkPreview() {
  document.getElementById("fLinkPreview").innerHTML = renderLinkChipsHtml(document.getElementById("fFileLink").value);
}
function updateTaskColorUI(hex, isCustom, inheritedRootTitle = "") {
  document.getElementById("fColor").value = hex;
  const badge = document.getElementById("fColorBadge");
  badge.style.background = hex;
  badge.style.color = contrastTextColor(hex);
  if (inheritedRootTitle) {
    badge.textContent = `同色系：跟隨「${inheritedRootTitle}」`;
  } else {
    badge.textContent = isCustom ? `自訂色 (${hex})` : "自動配色";
  }
}
function refreshTaskModalColorByParent(editable = true) {
  const proj = getActiveProject();
  const tasks = proj.tasks || [];
  const pid = document.getElementById("fParent").value;
  if (pid) {
    const fakeTask = { id: editingTaskId || "__new__", parentId: pid, start: document.getElementById("fStart").value || `${todayStr()}T09:00` };
    const { root, depth, sibIndex } = getTaskLineage(fakeTask, tasks);
    const derived = deriveSubtaskTint(getRootBaseColor(root), Math.max(1, depth), sibIndex);
    updateTaskColorUI(derived, false, root.title);
    document.getElementById("fColor").disabled = true;
    document.getElementById("btnColorAuto").style.display = "none";
  } else {
    const task = editingTaskId ? tasks.find(t => t.id === editingTaskId) : null;
    const baseHex = editingTaskHasCustomColor
      ? document.getElementById("fColor").value
      : (task && task.color && /^#[0-9a-fA-F]{6}$/.test(task.color) ? task.color : getTaskAutoColor(editingTaskId || "__new__"));
    updateTaskColorUI(baseHex, editingTaskHasCustomColor, "");
    document.getElementById("fColor").disabled = !editable;
    document.getElementById("btnColorAuto").style.display = editable ? "inline-block" : "none";
  }
}
function updateVariancePreview() {
  const isMs = document.getElementById("fMilestone").checked;
  const start = document.getElementById("fStart").value || `${todayStr()}T09:00`;
  const end = isMs ? start : (document.getElementById("fEnd").value || start);
  const actualEnd = document.getElementById("fActualEnd").value || "";
  const status = document.getElementById("fStatus").value;
  const v = calcScheduleVariance({ start, end, actualEnd, status });
  const bg = getPaletteColor("schedule", v.key);
  document.getElementById("fVariancePreview").innerHTML =
    `<span class="color-pill" style="background:${bg};color:${contrastTextColor(bg)};">${esc(v.shortLabel)}</span><span style="margin-left:6px;font-size:11.5px;color:var(--muted);">${esc(v.fullLabel)}</span>`;
  updateHolidayAlertInModal();
}
function collectDescendantIds(tasks, rootId) {
  const res = new Set();
  if (!rootId) return res;
  const queue = [rootId];
  while (queue.length) {
    const cur = queue.shift();
    tasks.forEach(t => {
      if (t.parentId === cur && !res.has(t.id)) {
        res.add(t.id);
        queue.push(t.id);
      }
    });
  }
  return res;
}

window.openTaskModal = function(id = null, presetParentId = "") {
  editingTaskId = id;
  const proj = getActiveProject();
  const tasks = proj.tasks || [];
  const task = id ? tasks.find(t => t.id === id) : null;
  const parentPreset = (!task && presetParentId) ? tasks.find(t => t.id === presetParentId) : null;
  const editable = task ? canEditTask(task, proj) : canCreateTask();
  const deletable = task ? canDeleteTask(task, proj) : false;

  document.getElementById("modalTitle").textContent = task
    ? `${editable ? "編輯" : "檢視"}項目（${proj.name}）`
    : (parentPreset ? `新增子任務 → 屬於「${parentPreset.title}」` : `新增項目（${proj.name}）`);
  document.getElementById("taskReadonlyHint").style.display = editable ? "none" : "block";
  document.getElementById("btnSaveTask").style.display = editable ? "inline-block" : "none";
  document.getElementById("btnDeleteTask").style.display = deletable ? "inline-block" : "none";
  document.getElementById("btnAddSubtaskFromModal").style.display = (task && canCreateTask()) ? "inline-block" : "none";

  const fAssignee = document.getElementById("fAssignee");
  const names = memberNames();
  fAssignee.innerHTML = names.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join("");

  const forbiddenParents = collectDescendantIds(tasks, id);
  if (id) forbiddenParents.add(id);
  const fParent = document.getElementById("fParent");
  fParent.innerHTML = `<option value="">無（最上層主任務）</option>` +
    tasks.filter(t => !forbiddenParents.has(t.id)).map(t =>
      `<option value="${esc(t.id)}">${t.parentId ? "└ " : ""}${esc(t.title)}</option>`
    ).join("");

  const fDep = document.getElementById("fDep");
  fDep.innerHTML = `<option value="">無</option>` +
    tasks.filter(t => t.id !== id).map(t =>
      `<option value="${esc(t.id)}">${t.isMilestone ? "◆ " : ""}${esc(t.title)}</option>`
    ).join("");

  const me = getMe();
  const defaultStart = `${todayStr()}T09:00`;
  const defaultEnd = `${addDaysDT(`${todayStr()}T18:00`, 3).slice(0, 10)}T18:00`;
  document.getElementById("fTitle").value = task ? task.title : "";
  document.getElementById("fMilestone").checked = task ? Boolean(task.isMilestone) : false;
  document.getElementById("fSplitHolidays").checked = task ? (task.splitByHolidays !== false) : true;
  fAssignee.value = task ? task.assignee : (me ? me.name : (names[0] || ""));
  document.getElementById("fStatus").value = task ? task.status : "todo";
  document.getElementById("fStart").value = task
    ? ensureDateTimeStr(task.start, "09:00")
    : (parentPreset ? ensureDateTimeStr(parentPreset.start, "09:00") : defaultStart);
  document.getElementById("fEnd").value = task
    ? ensureDateTimeStr(task.end || task.start, "18:00")
    : (parentPreset ? ensureDateTimeStr(parentPreset.end || parentPreset.start, "18:00") : defaultEnd);
  document.getElementById("fActualEnd").value = (task && task.actualEnd) ? ensureDateTimeStr(task.actualEnd, "18:00") : "";
  document.getElementById("fProgress").value = task ? task.progress : 0;
  document.getElementById("fProgVal").textContent = task ? task.progress : 0;
  document.getElementById("fPriority").value = task ? task.priority : "medium";
  fParent.value = task ? (task.parentId || "") : (parentPreset ? parentPreset.id : "");
  fDep.value = task ? (task.dep || "") : "";
  document.getElementById("fFileLink").value = task ? (task.fileLink || "") : "";
  document.getElementById("fNotes").value = task ? (task.notes || "") : "";

  editingTaskHasCustomColor = Boolean(task && !task.parentId && task.color && /^#[0-9a-fA-F]{6}$/.test(task.color));
  const initColor = editingTaskHasCustomColor ? task.color : getTaskAutoColor(id || "__new__");
  updateTaskColorUI(initColor, editingTaskHasCustomColor);

  const fields = ["fTitle", "fMilestone", "fSplitHolidays", "fAssignee", "fStatus", "fStart", "fEnd", "fActualEnd", "fProgress", "fPriority", "fColor", "fParent", "fDep", "fFileLink", "fNotes"];
  fields.forEach(fid => { document.getElementById(fid).disabled = !editable; });
  document.getElementById("btnPickSharedFile").style.display = editable ? "inline-block" : "none";
  document.getElementById("btnActualToday").style.display = editable ? "inline-block" : "none";
  document.getElementById("btnActualClear").style.display = editable ? "inline-block" : "none";
  document.getElementById("btnColorAuto").style.display = editable ? "inline-block" : "none";
  if (editable && me && !canManageProject(proj) && state.strictMemberEdit) {
    fAssignee.value = me.name;
    fAssignee.disabled = true;
  }

  refreshTaskModalColorByParent(editable);
  updateMilestoneFormUI();
  updateLinkPreview();
  updateVariancePreview();
  document.getElementById("taskModal").classList.add("open");
};

document.getElementById("fColor").addEventListener("input", e => {
  editingTaskHasCustomColor = true;
  updateTaskColorUI(e.target.value, true);
});
document.getElementById("btnColorAuto").addEventListener("click", () => {
  editingTaskHasCustomColor = false;
  updateTaskColorUI(getTaskAutoColor(editingTaskId || "__new__"), false);
});
document.getElementById("fParent").addEventListener("change", () => {
  const editable = !document.getElementById("fTitle").disabled;
  refreshTaskModalColorByParent(editable);
});
document.getElementById("fMilestone").addEventListener("change", updateMilestoneFormUI);
document.getElementById("fStart").addEventListener("change", () => {
  if (document.getElementById("fMilestone").checked) {
    document.getElementById("fEnd").value = document.getElementById("fStart").value;
  }
  updateVariancePreview();
});
document.getElementById("fEnd").addEventListener("change", updateVariancePreview);
document.getElementById("fActualEnd").addEventListener("change", updateVariancePreview);
document.getElementById("fStatus").addEventListener("change", e => {
  if (e.target.value === "done") {
    document.getElementById("fProgress").value = 100;
    document.getElementById("fProgVal").textContent = "100";
    if (!document.getElementById("fActualEnd").value) {
      document.getElementById("fActualEnd").value = nowDateTimeStr();
    }
  }
  updateVariancePreview();
});
document.getElementById("btnActualToday").addEventListener("click", () => {
  document.getElementById("fActualEnd").value = nowDateTimeStr();
  updateVariancePreview();
});
document.getElementById("btnActualClear").addEventListener("click", () => {
  document.getElementById("fActualEnd").value = "";
  updateVariancePreview();
});
document.getElementById("btnAddSubtaskFromModal").addEventListener("click", () => {
  if (editingTaskId) {
    const pid = editingTaskId;
    collapsedSet.delete(pid);
    saveCollapsedSet();
    openTaskModal(null, pid);
  }
});
document.getElementById("fFileLink").addEventListener("input", updateLinkPreview);
document.getElementById("fProgress").addEventListener("input", e => {
  document.getElementById("fProgVal").textContent = e.target.value;
});
document.getElementById("btnNewTask").addEventListener("click", () => {
  if (!canCreateTask()) { openLoginModal(); return; }
  openTaskModal(null);
});
document.getElementById("btnCancelTask").addEventListener("click", () => {
  document.getElementById("taskModal").classList.remove("open");
});
document.getElementById("btnSaveTask").addEventListener("click", async () => {
  const title = document.getElementById("fTitle").value.trim();
  if (!title) { alert("請輸入名稱"); return; }
  const isMilestone = document.getElementById("fMilestone").checked;
  const start = document.getElementById("fStart").value;
  const end = isMilestone ? start : document.getElementById("fEnd").value;
  const parentId = document.getElementById("fParent").value;
  const color = (!parentId && editingTaskHasCustomColor) ? document.getElementById("fColor").value : "";
  const task = {
    id: editingTaskId || undefined,
    title,
    isMilestone,
    assignee: document.getElementById("fAssignee").value,
    status: document.getElementById("fStatus").value,
    start,
    end,
    actualEnd: document.getElementById("fActualEnd").value || "",
    progress: Number(document.getElementById("fProgress").value),
    priority: document.getElementById("fPriority").value,
    color,
    splitByHolidays: document.getElementById("fSplitHolidays").checked,
    parentId,
    dep: document.getElementById("fDep").value,
    fileLink: document.getElementById("fFileLink").value.trim(),
    notes: document.getElementById("fNotes").value.trim(),
  };
  if (editingTaskId && customColors.task && customColors.task[editingTaskId]) {
    delete customColors.task[editingTaskId];
    saveColorPrefs();
  }
  if (parentId) {
    collapsedSet.delete(parentId);
    saveCollapsedSet();
  }
  if (await apiPost("/api/task", { task })) {
    document.getElementById("taskModal").classList.remove("open");
    showToast("已儲存並同步至團隊畫面");
  }
});
document.getElementById("btnDeleteTask").addEventListener("click", async () => {
  if (!editingTaskId || !confirm("確定要刪除此項目嗎？")) return;
  if (await apiPost("/api/task/delete", { id: editingTaskId })) {
    document.getElementById("taskModal").classList.remove("open");
    showToast("已刪除項目");
  }
});

// ---------- Multi-project & Settings (Sysadmin / PM) ----------
document.getElementById("projectSelect").addEventListener("change", e => {
  currentProjectId = e.target.value;
  localStorage.setItem("pm_lan_project", currentProjectId);
  refreshHeaderControls();
  render();
});
document.getElementById("btnNewProject").addEventListener("click", async () => {
  if (!canCreateProject()) { showToast("僅系統管理員或專案管理員可建立新專案"); return; }
  const name = prompt("請輸入新專案名稱（例如：專案 C：新產品設計）：");
  if (!name || !name.trim()) return;
  if (await apiPost("/api/project/create", { name: name.trim() })) {
    showToast(`已建立專案「${name.trim()}」`);
  }
});

function createPermRowEl(m = { id: "", name: "", role: "member", hasPin: false }) {
  const row = document.createElement("div");
  row.className = "perm-row";
  row.dataset.mid = m.id || "";
  row.innerHTML = `
    <input type="text" class="p-name" value="${esc(m.name)}" placeholder="成員姓名">
    <select class="p-role">
      <option value="sysadmin" ${m.role === "sysadmin" ? "selected" : ""}>系統管理員（最高權限）</option>
      <option value="pm" ${m.role === "pm" ? "selected" : ""}>專案管理員（專案＋任務）</option>
      <option value="member" ${m.role === "member" ? "selected" : ""}>一般成員（任務協作）</option>
      <option value="viewer" ${m.role === "viewer" ? "selected" : ""}>唯讀檢視（僅觀看）</option>
    </select>
    <div class="perm-pin-cell">
      <input type="password" class="p-pin" placeholder="${m.hasPin ? "重設 PIN..." : "設 PIN (選填)"}" autocomplete="new-password">
      <label style="font-size:11px;white-space:nowrap;display:flex;align-items:center;gap:2px;color:var(--muted);" title="勾選後儲存將清除此人 PIN 碼">
        <input type="checkbox" class="p-clear-pin"> 清除
      </label>
    </div>
    <button class="danger p-del" style="padding:4px 8px;">移除</button>
  `;
  row.querySelector(".p-del").addEventListener("click", () => row.remove());
  return row;
}

function switchSettingsTab(tab) {
  document.querySelectorAll(".sub-tab").forEach(b => b.classList.toggle("active", b.dataset.stab === tab));
  document.getElementById("stabProject").style.display = tab === "project" ? "block" : "none";
  document.getElementById("stabMembers").style.display = tab === "members" ? "block" : "none";
  document.getElementById("stabShared").style.display = tab === "shared" ? "block" : "none";
  document.getElementById("stabCloud").style.display = tab === "cloud" ? "block" : "none";
  document.getElementById("stabSystem").style.display = tab === "system" ? "block" : "none";
}

function createSharedRootRowEl(r = { id: "", label: "", path: "", available: null }) {
  const row = document.createElement("div");
  row.className = "root-row";
  row.dataset.rid = r.id || "";
  const statusHtml = r.available === true
    ? '<span class="root-status ok">✓ 可存取</span>'
    : (r.available === false ? '<span class="root-status bad">✕ 找不到</span>' : '<span class="root-status" style="background:#f1f5f9;color:#475569;">未測試</span>');
  row.innerHTML = `
    <input type="text" class="r-label" value="${esc(r.label)}" placeholder="顯示名稱（如：設計區）">
    <input type="text" class="r-path" value="${esc(r.path)}" placeholder="伺服器路徑（如 D:\\Shared 或 \\\\IP\\Shared）">
    <div style="display:flex;align-items:center;gap:6px;">
      ${statusHtml}
      <button type="button" class="r-test" style="padding:4px 8px;font-size:12px;">測試</button>
    </div>
    <button type="button" class="danger r-del" style="padding:4px 8px;">移除</button>
  `;
  row.querySelector(".r-test").addEventListener("click", async () => {
    const p = row.querySelector(".r-path").value.trim();
    const res = await apiPost("/api/fs/check-path", { path: p });
    if (!res) return;
    const badge = row.querySelector(".root-status");
    if (res.readable) {
      badge.className = "root-status ok";
      badge.textContent = "✓ 可存取";
    } else {
      badge.className = "root-status bad";
      badge.textContent = "✕ 無法存取";
    }
    showToast(res.message || "測試完成");
  });
  row.querySelector(".r-del").addEventListener("click", () => row.remove());
  return row;
}

function renderProjectManagerCheckboxes(proj) {
  const box = document.getElementById("sProjectManagers");
  const pms = (state.members || []).filter(m => m.role === "pm");
  const curMgrs = proj.managerIds || [];
  if (!pms.length) {
    box.innerHTML = '<span style="font-size:12px;color:var(--muted);">目前尚未指定任何「專案管理員」角色（系統管理員可在「人員與權限」中指派）。</span>';
    return;
  }
  box.innerHTML = pms.map(m => `
    <label class="pm-check-item">
      <input type="checkbox" class="pm-mgr-chk" value="${esc(m.id)}" ${curMgrs.includes(m.id) ? "checked" : ""}>
      <span>${esc(m.name)}</span>
    </label>
  `).join("");
}

document.getElementById("btnSettings").addEventListener("click", () => {
  const proj = getActiveProject();
  const sysAdm = isSysAdmin();
  const projMgr = canManageProject(proj);
  if (!sysAdm && !projMgr) {
    showToast("僅系統管理員或本專案管理員可開啟設定");
    return;
  }

  document.getElementById("settingsModalTitle").textContent = sysAdm ? "系統與專案設定（系統管理員）" : `專案設定（${proj.name}）`;
  document.getElementById("tabBtnProject").style.display = projMgr ? "inline-block" : "none";
  document.getElementById("tabBtnMembers").style.display = sysAdm ? "inline-block" : "none";
  document.getElementById("tabBtnShared").style.display = sysAdm ? "inline-block" : "none";
  document.getElementById("tabBtnCloud").style.display = sysAdm ? "inline-block" : "none";
  document.getElementById("tabBtnSystem").style.display = sysAdm ? "inline-block" : "none";

  document.getElementById("sProjectName").value = proj.name;
  renderProjectManagerCheckboxes(proj);

  if (sysAdm) {
    document.getElementById("chkStrictMode").checked = Boolean(state.strictMemberEdit);
    const list = document.getElementById("permList");
    list.innerHTML = "";
    (state.members || []).forEach(m => list.appendChild(createPermRowEl(m)));

    const rList = document.getElementById("sharedRootsList");
    rList.innerHTML = "";
    (state.sharedRoots || []).forEach(r => rList.appendChild(createSharedRootRowEl(r)));

    document.getElementById("chkViewPassEnabled").checked = Boolean(state.viewPassEnabled);
    document.getElementById("sNewViewPass").value = "";
    document.getElementById("chkKickOtherViews").checked = false;
    loadGithubSyncSettingsUI();
  }

  switchSettingsTab("project");
  document.getElementById("settingsModal").classList.add("open");
});

document.querySelectorAll(".sub-tab").forEach(btn => {
  btn.addEventListener("click", () => switchSettingsTab(btn.dataset.stab));
});

document.getElementById("btnAddMemberRow").addEventListener("click", () => {
  document.getElementById("permList").appendChild(createPermRowEl());
});

document.querySelectorAll(".btnCloseSettings").forEach(b => {
  b.addEventListener("click", () => document.getElementById("settingsModal").classList.remove("open"));
});

document.getElementById("btnSaveMembers").addEventListener("click", async () => {
  const rows = Array.from(document.querySelectorAll("#permList .perm-row"));
  const members = rows.map(r => ({
    id: r.dataset.mid,
    name: r.querySelector(".p-name").value.trim(),
    role: r.querySelector(".p-role").value,
    newPin: r.querySelector(".p-pin").value.trim(),
    clearPin: r.querySelector(".p-clear-pin").checked,
  })).filter(m => m.name);

  if (!members.length) { alert("請至少保留一位成員"); return; }
  const strictMemberEdit = document.getElementById("chkStrictMode").checked;
  if (await apiPost("/api/settings/members", { members, strictMemberEdit })) {
    renderProjectManagerCheckboxes(getActiveProject());
    document.getElementById("settingsModal").classList.remove("open");
    showToast("人員與權限設定已更新並同步");
  }
});

document.getElementById("btnSaveProjectSettings").addEventListener("click", async () => {
  const projectName = document.getElementById("sProjectName").value.trim();
  if (!projectName) { alert("請輸入專案名稱"); return; }
  const managerIds = Array.from(document.querySelectorAll("#sProjectManagers .pm-mgr-chk:checked")).map(c => c.value);
  if (await apiPost("/api/settings/project", { projectName, managerIds })) {
    document.getElementById("settingsModal").classList.remove("open");
    showToast("專案設定已更新並同步");
  }
});

document.getElementById("btnDeleteProject").addEventListener("click", async () => {
  const proj = getActiveProject();
  if (!confirm(`確定要刪除專案「${proj.name}」及其內部所有任務嗎？\n（系統會自動先在 backups/ 留一份快照備份）`)) return;
  if (await apiPost("/api/project/delete", { projectId: proj.id })) {
    document.getElementById("settingsModal").classList.remove("open");
    showToast("已刪除專案（快照備份已存至 backups 資料夾）");
  }
});

document.getElementById("btnExportCSV").addEventListener("click", () => {
  const proj = getActiveProject();
  const allTasks = proj.tasks || [];
  const tree = buildTaskTreeRows(allTasks, allTasks, false);
  const header = ["專案名稱", "層級", "上層任務", "類型", "名稱", "負責人", "狀態", "優先級", "預期開始", "預期結束", "實際完成日", "預期與實際差異", "進度(%)", "檔案連結", "備註"];
  const lines = [header.join(",")].concat(tree.rows.map(r => {
    const t = r.task;
    const pObj = t.parentId ? tree.byId.get(t.parentId) : null;
    const v = calcScheduleVariance(t);
    return [
      proj.name,
      r.depth === 0 ? "主任務" : `子任務(L${r.depth})`,
      pObj ? pObj.title : "",
      t.isMilestone ? "里程碑" : "任務",
      t.title,
      t.assignee,
      STATUS_MAP[t.status] || t.status,
      PRIO_MAP[t.priority] || t.priority,
      fmtDisplayDT(t.start),
      fmtDisplayDT(t.end),
      t.actualEnd ? fmtDisplayDT(t.actualEnd) : "",
      v.shortLabel,
      t.progress,
      (t.fileLink || "").replace(/\r?\n/g, " | "),
      t.notes || "",
    ].map(val => `"${String(val).replace(/"/g, '""')}"`).join(",");
  }));
  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${proj.name}_${todayStr()}.csv`;
  a.click();
});

document.getElementById("btnExportJSON").addEventListener("click", async () => {
  const res = await fetch("/api/backup/export", { headers: authHeaders() });
  const json = await res.json();
  if (!json.ok) { showToast(json.error || "匯出失敗"); return; }
  const blob = new Blob([JSON.stringify(json.backup, null, 2)], { type: "application/json;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `all_projects_backup_${todayStr()}.json`;
  a.click();
});

document.getElementById("btnImportJSON").addEventListener("click", () => {
  document.getElementById("importFile").click();
});
document.getElementById("importFile").addEventListener("change", e => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const parsed = JSON.parse(reader.result);
      if (confirm("匯入將覆蓋目前全部專案任務（系統會自動先在 backups 留一份快照），確定嗎？")) {
        if (await apiPost("/api/import", { data: parsed })) {
          document.getElementById("settingsModal").classList.remove("open");
          showToast("已還原專案資料");
        }
      }
    } catch (err) {
      alert("JSON 檔案格式錯誤");
    }
    e.target.value = "";
  };
  reader.readAsText(file, "utf-8");
});

async function loadGithubSyncSettingsUI() {
  try {
    const res = await fetch("/api/settings/github", { headers: authHeaders() });
    const json = await res.json();
    if (!json.ok) return;
    const c = json.config || {};
    const st = json.status || {};
    document.getElementById("chkGhEnabled").checked = Boolean(c.enabled);
    document.getElementById("ghOwner").value = c.owner || "";
    document.getElementById("ghRepo").value = c.repo || "";
    document.getElementById("ghBranch").value = c.branch || "main";
    document.getElementById("ghFilePath").value = sanitizeGhFilePath(c.filePath);
    document.getElementById("ghInterval").value = String(c.intervalSec || 10);
    document.getElementById("ghToken").value = "";
    document.getElementById("ghToken").placeholder = c.hasToken
      ? `已儲存 Token (${c.tokenMask})，留空維持不變`
      : "貼上 github_pat_...";
    const box = document.getElementById("ghStatusBox");
    if (!c.enabled) {
      box.textContent = "目前狀態：未啟用 GitHub 雲端雙向同步";
    } else if (st.status === "error") {
      box.innerHTML = `<strong style="color:var(--danger);">同步失敗：</strong>${esc(st.lastError || "請檢查倉庫名稱與 Token 權限")}`;
    } else {
      box.innerHTML = `<strong style="color:#166534;">已啟用並連線：</strong>${esc(st.repo)} (${esc(st.branch)}) ｜ 最後同步：${esc(st.lastSync || "等待同步中")}`;
    }
  } catch (_) {}
}

document.getElementById("btnSaveGhSync").addEventListener("click", async () => {
  const enabled = document.getElementById("chkGhEnabled").checked;
  const owner = document.getElementById("ghOwner").value.trim();
  const repo = document.getElementById("ghRepo").value.trim();
  const branch = document.getElementById("ghBranch").value.trim() || "main";
  const filePath = sanitizeGhFilePath(document.getElementById("ghFilePath").value);
  const token = document.getElementById("ghToken").value.trim();
  const intervalSec = Number(document.getElementById("ghInterval").value) || 10;
  const box = document.getElementById("ghStatusBox");
  box.textContent = "正在儲存並測試連線 GitHub...";
  const res = await apiPost("/api/settings/github", { enabled, owner, repo, branch, filePath, token, intervalSec });
  if (res && res.ok) {
    await loadGithubSyncSettingsUI();
    showToast(res.syncMessage || "GitHub 同步設定已儲存");
  }
});

async function triggerManualCloudSync() {
  showToast("正在與 GitHub 私有倉庫同步...");
  const res = await apiPost("/api/settings/github/sync-now", {});
  if (res && res.ok) {
    if (document.getElementById("settingsModal").classList.contains("open")) {
      await loadGithubSyncSettingsUI();
    }
    showToast(res.syncMessage || "同步完成");
  }
}
document.getElementById("btnGhSyncNow").addEventListener("click", triggerManualCloudSync);
document.getElementById("cloudBadge").addEventListener("click", triggerManualCloudSync);

document.getElementById("btnCopyCloudInvite").addEventListener("click", () => {
  const owner = document.getElementById("ghOwner").value.trim();
  const repo = document.getElementById("ghRepo").value.trim();
  const branch = document.getElementById("ghBranch").value.trim() || "main";
  const filePath = sanitizeGhFilePath(document.getElementById("ghFilePath").value);
  const token = document.getElementById("ghToken").value.trim();
  if (!owner || !repo) {
    alert("請先填寫 GitHub 帳號與私有倉庫名稱");
    return;
  }
  if (!token) {
    alert("基於安全考量，伺服器不會把已存 Token 傳回網頁。請在上方 Token 欄位貼上您的 GitHub Token 後再按一次產生連線碼。");
    return;
  }
  const obj = { owner, repo, branch, filePath, token };
  const code = "PMGH:" + btoa(unescape(encodeURIComponent(JSON.stringify(obj))));
  copyText(code);
  showToast("已複製團隊雲端快速連線碼！請私訊給 3 位同事貼到 GitHub Pages 畫面即可連線");
});

document.getElementById("btnSaveViewPass").addEventListener("click", async () => {
  const viewPassEnabled = document.getElementById("chkViewPassEnabled").checked;
  const newPassword = document.getElementById("sNewViewPass").value.trim();
  const kickOthers = document.getElementById("chkKickOtherViews").checked;
  const res = await apiPost("/api/settings/view-pass", { viewPassEnabled, newPassword, kickOthers });
  if (res && res.ok) {
    document.getElementById("sNewViewPass").value = "";
    document.getElementById("chkKickOtherViews").checked = false;
    showToast(viewPassEnabled ? "觀看密碼設定已儲存並生效" : "已停用觀看密碼保護");
  }
});

document.getElementById("btnUnlockView").addEventListener("click", submitViewUnlock);
document.getElementById("viewPassInput").addEventListener("keydown", e => {
  if (e.key === "Enter") submitViewUnlock();
});
document.getElementById("btnLockScreen").addEventListener("click", async () => {
  try {
    await fetch("/api/lock-view", { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: "{}" });
  } catch (_) {}
  authToken = "";
  localStorage.removeItem("pm_lan_token");
  initialLoginPrompted = false;
  showViewLockScreen("已鎖定畫面，請輸入觀看密碼重新進入。");
});

document.getElementById("btnAddRootRow").addEventListener("click", () => {
  document.getElementById("sharedRootsList").appendChild(createSharedRootRowEl());
});
document.getElementById("btnSaveSharedRoots").addEventListener("click", async () => {
  const rows = Array.from(document.querySelectorAll("#sharedRootsList .root-row"));
  const sharedRoots = rows.map(r => ({
    id: r.dataset.rid,
    label: r.querySelector(".r-label").value.trim(),
    path: r.querySelector(".r-path").value.trim(),
  })).filter(r => r.path);
  if (!sharedRoots.length) { alert("請至少填寫一個共用資料夾路徑"); return; }
  if (await apiPost("/api/settings/shared-roots", { sharedRoots })) {
    document.getElementById("settingsModal").classList.remove("open");
    showToast("共用資料夾設定已更新並同步");
  }
});

// ---------- Web Shared Folder Browser & Picker Modal ----------
function fmtFileSize(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let u = 0, v = bytes;
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
  return (u === 0 ? v : v.toFixed(1)) + " " + units[u];
}
function fileIconByExt(ext) {
  if ([".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"].includes(ext)) return "🖼️";
  if (ext === ".pdf") return "📕";
  if ([".xls", ".xlsx", ".csv"].includes(ext)) return "📊";
  if ([".doc", ".docx", ".txt", ".md"].includes(ext)) return "📝";
  if ([".ppt", ".pptx"].includes(ext)) return "📽️";
  if ([".zip", ".rar", ".7z"].includes(ext)) return "📦";
  if ([".mp4", ".webm", ".mov", ".mp3", ".wav"].includes(ext)) return "🎬";
  return "📄";
}
function appendLinkToTaskInput(shareUri, label) {
  const ta = document.getElementById("fFileLink");
  const lines = parseLinks(ta.value);
  if (!lines.includes(shareUri)) {
    lines.push(shareUri);
    ta.value = lines.join("\n");
    updateLinkPreview();
    showToast(`已加入「${label}」（記得按儲存同步）`);
  } else {
    showToast(`「${label}」已在連結清單中`);
  }
}

async function openFsModal(mode = "browse", rootId = "", relPath = "") {
  const roots = state.sharedRoots || [];
  if (!roots.length) {
    showToast("尚未設定共用資料夾，請聯絡系統管理員至「系統設定 → 共用資料夾」新增。");
    return;
  }
  const targetRoot = roots.find(r => r.id === rootId) ? rootId : roots[0].id;
  fsState.mode = mode;
  fsState.rootId = targetRoot;
  fsState.path = relPath || "";

  document.getElementById("fsModalTitle").textContent = mode === "picker"
    ? "📂 挑選檔案或資料夾加入任務"
    : "📂 內網共用資料夾瀏覽器";
  document.getElementById("fsModeBadge").style.display = mode === "picker" ? "inline-block" : "none";
  document.getElementById("fsBtnPickCurrentDir").style.display = mode === "picker" ? "inline-block" : "none";
  document.getElementById("fsFooterHint").textContent = mode === "picker"
    ? "點「＋ 選取」可一次加入多個檔案或資料夾，選完按右下角「關閉」回到任務視窗。"
    : "點資料夾可進入；點檔案可直接在瀏覽器預覽，或點「⬇ 下載」存到本機。";

  const rootSel = document.getElementById("fsRootSelect");
  rootSel.innerHTML = roots.map(r =>
    `<option value="${esc(r.id)}">${esc(r.label)}${r.available ? "" : " (無法存取)"}</option>`
  ).join("");
  rootSel.value = targetRoot;
  document.getElementById("fsSearchInput").value = "";
  document.getElementById("fsModal").classList.add("open");
  await loadFsDir(targetRoot, fsState.path);
}

async function loadFsDir(rootId, relPath) {
  const wrap = document.getElementById("fsListWrap");
  wrap.innerHTML = '<div style="padding:24px;text-align:center;color:var(--muted);">載入中...</div>';
  try {
    const url = `/api/fs/list?root=${encodeURIComponent(rootId)}&path=${encodeURIComponent(relPath || "")}`;
    const res = await fetch(url, { headers: authHeaders() });
    const json = await res.json();
    if (!json.ok) {
      wrap.innerHTML = `<div style="padding:24px;text-align:center;color:var(--danger);">${esc(json.error || "讀取失敗")}</div>`;
      document.getElementById("fsRealPathText").textContent = "";
      return;
    }
    if (json.isFileTarget) {
      window.open(buildFsFileUrl(rootId, json.path, false), "_blank", "noopener");
      await loadFsDir(rootId, json.parent || "");
      return;
    }
    fsState.rootId = json.root.id;
    fsState.path = json.path || "";
    fsState.parent = json.parent;
    fsState.realPath = json.realPath || "";
    fsState.items = json.items || [];

    document.getElementById("fsRootSelect").value = fsState.rootId;
    document.getElementById("fsBtnUp").disabled = fsState.parent === null;
    document.getElementById("fsRealPathText").textContent = fsState.realPath;
    renderFsBreadcrumbs(json.root);
    renderFsItems();
  } catch (e) {
    wrap.innerHTML = '<div style="padding:24px;text-align:center;color:var(--danger);">無法連線至伺服器讀取資料夾</div>';
  }
}

function renderFsBreadcrumbs(rootObj) {
  const bar = document.getElementById("fsCrumbBar");
  const segs = fsState.path ? fsState.path.split("/").filter(Boolean) : [];
  let html = `<button class="fs-crumb" data-fs-nav="">📂 ${esc(rootObj.label)}</button>`;
  let acc = "";
  segs.forEach(seg => {
    acc = acc ? `${acc}/${seg}` : seg;
    html += `<span style="color:#94a3b8;">/</span><button class="fs-crumb" data-fs-nav="${esc(acc)}">${esc(seg)}</button>`;
  });
  bar.innerHTML = html;
  bar.querySelectorAll("[data-fs-nav]").forEach(btn => {
    btn.addEventListener("click", () => loadFsDir(fsState.rootId, btn.dataset.fsNav));
  });
}

function renderFsItems() {
  const kw = document.getElementById("fsSearchInput").value.trim().toLowerCase();
  const list = (fsState.items || []).filter(it => !kw || it.name.toLowerCase().includes(kw));
  const wrap = document.getElementById("fsListWrap");
  if (!list.length) {
    wrap.innerHTML = `<div style="padding:28px;text-align:center;color:var(--muted);">${kw ? "沒有符合搜尋條件的檔案" : "此資料夾目前是空的"}</div>`;
    return;
  }
  const isPicker = fsState.mode === "picker";
  wrap.innerHTML = list.map(it => {
    const icon = it.isDir ? "📁" : fileIconByExt(it.ext);
    const meta = it.isDir ? `資料夾 ・ ${esc(it.mtime)}` : `${fmtFileSize(it.size)} ・ ${esc(it.mtime)}`;
    if (it.isDir) {
      return `
        <div class="fs-item">
          <div class="fs-item-main" data-fs-enter="${esc(it.rel)}">
            <span style="font-size:17px;">${icon}</span>
            <span class="fs-item-name">${esc(it.name)}</span>
          </div>
          <span class="fs-item-meta">${meta}</span>
          <div class="fs-item-acts">
            <button type="button" data-fs-enter="${esc(it.rel)}" style="padding:3px 9px;font-size:12px;">開啟</button>
            ${isPicker ? `<button type="button" class="primary" data-fs-pick-dir="${esc(it.rel)}" data-fs-pick-name="${esc(it.name)}" style="padding:3px 9px;font-size:12px;">＋ 選取</button>` : ""}
          </div>
        </div>
      `;
    }
    const openUrl = buildFsFileUrl(fsState.rootId, it.rel, false);
    const dlUrl = buildFsFileUrl(fsState.rootId, it.rel, true);
    return `
      <div class="fs-item">
        <a class="fs-item-main" href="${esc(openUrl)}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;color:inherit;">
          <span style="font-size:17px;">${icon}</span>
          <span class="fs-item-name">${esc(it.name)}</span>
        </a>
        <span class="fs-item-meta">${meta}</span>
        <div class="fs-item-acts">
          <a class="link-icon-btn" href="${esc(openUrl)}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;padding:4px 9px;">${it.previewable ? "👁 預覽" : "開啟"}</a>
          <a class="link-icon-btn" href="${esc(dlUrl)}" style="text-decoration:none;padding:4px 8px;" title="下載檔案">⬇</a>
          ${isPicker ? `<button type="button" class="primary" data-fs-pick-file="${esc(it.rel)}" data-fs-pick-name="${esc(it.name)}" style="padding:3px 9px;font-size:12px;">＋ 選取</button>` : ""}
        </div>
      </div>
    `;
  }).join("");

  wrap.querySelectorAll("[data-fs-enter]").forEach(el => {
    el.addEventListener("click", () => loadFsDir(fsState.rootId, el.dataset.fsEnter));
  });
  wrap.querySelectorAll("[data-fs-pick-dir]").forEach(btn => {
    btn.addEventListener("click", () => {
      const rel = btn.dataset.fsPickDir;
      const uri = `share://${fsState.rootId}/${rel}/`;
      appendLinkToTaskInput(uri, btn.dataset.fsPickName);
    });
  });
  wrap.querySelectorAll("[data-fs-pick-file]").forEach(btn => {
    btn.addEventListener("click", () => {
      const rel = btn.dataset.fsPickFile;
      const uri = `share://${fsState.rootId}/${rel}`;
      appendLinkToTaskInput(uri, btn.dataset.fsPickName);
    });
  });
}

document.getElementById("btnOpenSharedFiles").addEventListener("click", () => openFsModal("browse"));
document.getElementById("btnPickSharedFile").addEventListener("click", () => openFsModal("picker"));
document.getElementById("fsBtnClose").addEventListener("click", () => {
  document.getElementById("fsModal").classList.remove("open");
});
document.getElementById("fsRootSelect").addEventListener("change", e => {
  loadFsDir(e.target.value, "");
});
document.getElementById("fsBtnUp").addEventListener("click", () => {
  if (fsState.parent !== null) loadFsDir(fsState.rootId, fsState.parent);
});
document.getElementById("fsBtnRefresh").addEventListener("click", () => {
  loadFsDir(fsState.rootId, fsState.path);
});
document.getElementById("fsSearchInput").addEventListener("input", renderFsItems);
document.getElementById("fsBtnCopyRealPath").addEventListener("click", () => {
  if (fsState.realPath) copyText(fsState.realPath);
});
document.getElementById("fsBtnPickCurrentDir").addEventListener("click", () => {
  const uri = fsState.path ? `share://${fsState.rootId}/${fsState.path}/` : `share://${fsState.rootId}/`;
  const rootObj = (state.sharedRoots || []).find(r => r.id === fsState.rootId);
  const label = fsState.path ? fsState.path.split("/").pop() : (rootObj ? rootObj.label : fsState.rootId);
  appendLinkToTaskInput(uri, label);
});

// ---------- Holiday Calendar & Task Split Modal Logic ----------
document.getElementById("fSplitHolidays").addEventListener("change", updateHolidayAlertInModal);

document.getElementById("btnExtendForHolidays").addEventListener("click", () => {
  const start = document.getElementById("fStart").value;
  const end = document.getElementById("fEnd").value;
  if (!start || !end) return;
  const extEnd = calcExtendedEndForHolidays(start, end);
  document.getElementById("fEnd").value = extEnd;
  updateVariancePreview();
  showToast(`已將預期結束時間順延至 ${fmtDisplayDT(extEnd)}（記得按「儲存並同步」）`);
});

document.getElementById("btnHardSplitTask").addEventListener("click", async () => {
  if (!editingTaskId) return;
  if (!confirm("確定要依休假日將此任務實體拆分為前後多段獨立任務嗎？\n（會自動建立前後相依箭頭，並按比例分配目前進度）")) return;
  const res = await apiPost("/api/task/split-holidays", { id: editingTaskId });
  if (res && res.ok) {
    document.getElementById("taskModal").classList.remove("open");
    showToast(`已依休假日將任務實體拆分為 ${res.segments} 段`);
  }
});

function createHolidayRowEl(h = { id: "", name: "", start: "", end: "" }, editable = true) {
  const row = document.createElement("div");
  row.className = "holiday-row";
  row.dataset.hid = h.id || "";
  const calcDaysText = (s, e) => {
    if (!s || !e) return "—";
    const d = Math.round((parseDateTime(`${e}T00:00`) - parseDateTime(`${s}T00:00`)) / 86400000) + 1;
    return d >= 1 ? `共 ${d} 天` : "日期錯誤";
  };
  row.innerHTML = `
    <input type="text" class="h-name" value="${esc(h.name)}" placeholder="假期名稱（如：農曆春節連假）" ${editable ? "" : "disabled"}>
    <input type="date" class="h-start" value="${esc(h.start)}" ${editable ? "" : "disabled"}>
    <input type="date" class="h-end" value="${esc(h.end)}" ${editable ? "" : "disabled"}>
    <span class="holiday-badge h-days">${calcDaysText(h.start, h.end)}</span>
    ${editable ? '<button type="button" class="danger h-del" style="padding:4px 8px;">移除</button>' : '<span></span>'}
  `;
  const sEl = row.querySelector(".h-start");
  const eEl = row.querySelector(".h-end");
  const dEl = row.querySelector(".h-days");
  const syncDays = () => {
    if (sEl.value && (!eEl.value || eEl.value < sEl.value)) eEl.value = sEl.value;
    dEl.textContent = calcDaysText(sEl.value, eEl.value);
  };
  sEl.addEventListener("change", syncDays);
  eEl.addEventListener("change", syncDays);
  const delBtn = row.querySelector(".h-del");
  if (delBtn) delBtn.addEventListener("click", () => row.remove());
  return row;
}

function renderHolidayImpactList() {
  const proj = getActiveProject();
  const tasks = (proj.tasks || []).filter(t => !t.isMilestone).sort(compareTasksByStart);
  const impacted = [];
  tasks.forEach(t => {
    const sMs = parseDateTime(t.start, "09:00").getTime();
    const eMs = parseDateTime(t.end || t.start, "18:00").getTime();
    const sp = splitTaskByHolidaysMs(sMs, eMs, true);
    if (sp.hits.length) impacted.push({ task: t, sp });
  });
  document.getElementById("holidayImpactCount").textContent = String(impacted.length);
  const wrap = document.getElementById("holidayImpactList");
  if (!impacted.length) {
    wrap.innerHTML = '<div style="padding:14px;text-align:center;color:var(--muted);font-size:12.5px;">目前專案內沒有跨越休假日的任務。</div>';
    return;
  }
  wrap.innerHTML = impacted.map(({ task, sp }) => {
    const editable = canEditTask(task, proj);
    const canHardSplit = editable && sp.segments.length >= 2;
    return `
      <div class="holiday-impact-row">
        <div>
          <strong>${esc(task.title)}</strong>
          <span class="holiday-badge" style="margin-left:6px;">${esc(sp.hits.join("、"))}</span>
          <div style="font-size:11.5px;color:var(--muted);margin-top:2px;">
            ${esc(fmtDisplayDT(task.start))} ~ ${esc(fmtDisplayDT(task.end))}（跨假 ${fmtDurationMinutes(sp.holidayOverlapMs / 60000)}，淨工期 ${fmtDurationMinutes(sp.workMs / 60000)}）
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          ${editable ? `<button type="button" onclick="quickExtendTaskHoliday('${esc(task.id)}')" style="padding:3px 8px;font-size:11.5px;">⏩ 順延補回</button>` : ""}
          ${canHardSplit ? `<button type="button" onclick="quickHardSplitTask('${esc(task.id)}')" style="padding:3px 8px;font-size:11.5px;background:#be123c;color:#fff;border-color:#be123c;">✂️ 實體拆分</button>` : ""}
          <button type="button" onclick="document.getElementById('holidayModal').classList.remove('open');openTaskModal('${esc(task.id)}')" style="padding:3px 8px;font-size:11.5px;">${editable ? "編輯" : "檢視"}</button>
        </div>
      </div>
    `;
  }).join("");
}

window.quickExtendTaskHoliday = async function(tid) {
  const proj = getActiveProject();
  const task = (proj.tasks || []).find(t => t.id === tid);
  if (!task) return;
  const newEnd = calcExtendedEndForHolidays(task.start, task.end || task.start);
  if (newEnd === task.end) { showToast("此任務無需順延"); return; }
  const updated = { ...task, end: newEnd };
  if (await apiPost("/api/task", { task: updated })) {
    renderHolidayImpactList();
    showToast(`已將「${task.title}」結束時間順延至 ${fmtDisplayDT(newEnd)}`);
  }
};

window.quickHardSplitTask = async function(tid) {
  const proj = getActiveProject();
  const task = (proj.tasks || []).find(t => t.id === tid);
  if (!task) return;
  if (!confirm(`確定要依休假日將「${task.title}」實體拆分為多段任務嗎？`)) return;
  const res = await apiPost("/api/task/split-holidays", { id: tid });
  if (res && res.ok) {
    renderHolidayImpactList();
    showToast(`已將「${task.title}」實體拆分為 ${res.segments} 段`);
  }
};

function openHolidayModal() {
  const me = getMe();
  const canEditHol = Boolean(me && (me.role === "sysadmin" || me.role === "pm"));
  document.getElementById("holidayAdminToolbar").style.display = canEditHol ? "flex" : "none";
  document.getElementById("holidayPermHint").textContent = canEditHol
    ? "新增或修改後請按「儲存休假日並同步」套用至全隊甘特圖。"
    : "目前為唯讀檢視（僅系統管理員與專案管理員可修改休假日行事曆）。";

  const list = document.getElementById("holidayList");
  list.innerHTML = "";
  const hols = state.holidays || [];
  if (!hols.length) {
    list.innerHTML = '<div style="padding:14px;text-align:center;color:var(--muted);font-size:12.5px;">尚未設定任何休假日，可點上方按鈕新增或匯入 2027 台灣國定假日。</div>';
  } else {
    hols.forEach(h => list.appendChild(createHolidayRowEl(h, canEditHol)));
  }
  renderHolidayImpactList();
  document.getElementById("holidayModal").classList.add("open");
}

document.getElementById("btnHolidays").addEventListener("click", openHolidayModal);
document.getElementById("btnCloseHolidayModal").addEventListener("click", () => {
  document.getElementById("holidayModal").classList.remove("open");
});
document.getElementById("btnAddHolidayRow").addEventListener("click", () => {
  const list = document.getElementById("holidayList");
  if (!list.querySelector(".holiday-row")) list.innerHTML = "";
  const row = createHolidayRowEl({ id: "", name: "", start: todayStr(), end: todayStr() }, true);
  list.appendChild(row);
  row.querySelector(".h-name").focus();
});
document.getElementById("btnPresetTW2027").addEventListener("click", () => {
  const list = document.getElementById("holidayList");
  if (!list.querySelector(".holiday-row")) list.innerHTML = "";
  const existingKeys = new Set(Array.from(list.querySelectorAll(".holiday-row")).map(r =>
    `${r.querySelector(".h-start").value}_${r.querySelector(".h-end").value}`
  ));
  let added = 0;
  TW_2027_HOLIDAYS.forEach(h => {
    const k = `${h.start}_${h.end}`;
    if (!existingKeys.has(k)) {
      list.appendChild(createHolidayRowEl(h, true));
      existingKeys.add(k);
      added++;
    }
  });
  showToast(added ? `已匯入 ${added} 筆 2027 台灣國定假日（請按「儲存休假日並同步」）` : "2027 台灣國定假日皆已在清單中");
});
document.getElementById("btnSaveHolidays").addEventListener("click", async () => {
  const rows = Array.from(document.querySelectorAll("#holidayList .holiday-row"));
  const holidays = rows.map(r => ({
    id: r.dataset.hid,
    name: r.querySelector(".h-name").value.trim(),
    start: r.querySelector(".h-start").value,
    end: r.querySelector(".h-end").value || r.querySelector(".h-start").value,
  })).filter(h => h.start);
  const res = await apiPost("/api/settings/holidays", { holidays });
  if (res && res.ok) {
    openHolidayModal();
    showToast(`已儲存 ${holidays.length} 筆休假日並同步全隊甘特圖`);
  }
});

// ---------- Color Display Mode & Custom Palette Modal ----------
function buildColorCfgGroupHtml(groupKey, title, entries) {
  const items = entries.map(e => {
    const bg = getPaletteColor(groupKey, e.key);
    const fg = contrastTextColor(bg);
    return `
      <div class="color-cfg-item">
        <span class="color-pill" data-preview-group="${esc(groupKey)}" data-preview-key="${esc(e.key)}" style="background:${bg};color:${fg};">${esc(e.label)}</span>
        <input type="color" value="${esc(bg)}" data-cfg-group="${esc(groupKey)}" data-cfg-key="${esc(e.key)}" title="選擇「${esc(e.label)}」的底色">
      </div>
    `;
  }).join("");
  return `
    <div class="color-cfg-group">
      <div class="color-cfg-title">
        <span>${esc(title)}</span>
        ${colorMode === groupKey ? '<span class="role-tag role-pm">目前使用中</span>' : ""}
      </div>
      <div class="color-cfg-grid">${items}</div>
    </div>
  `;
}

window.openColorModal = function() {
  document.getElementById("cfgColorMode").value = colorMode;
  const proj = getActiveProject();
  const allTasks = proj.tasks || [];
  const tree = buildTaskTreeRows(allTasks, allTasks, false);
  const roots = getRootTasksSorted(allTasks);
  const taskEntries = roots.map(t => {
    const st = tree.statsMap.get(t.id) || { total: 0 };
    const shortT = t.title.length > 10 ? t.title.slice(0, 10) + "…" : t.title;
    return {
      key: t.id,
      label: st.total > 0 ? `${shortT} (含${st.total}子項)` : shortT,
    };
  });
  const body = document.getElementById("colorConfigBody");
  const groups = [
    buildColorCfgGroupHtml("status", "依任務狀態配色", [
      { key: "todo", label: "待辦" },
      { key: "doing", label: "進行中" },
      { key: "review", label: "審核中" },
      { key: "done", label: "已完成" },
    ]),
    buildColorCfgGroupHtml("task", `依任務配色（${proj.name}｜設定主任務底色，子任務自動同色系漸層）`, taskEntries),
    buildColorCfgGroupHtml("assignee", "依負責人配色", memberNames().map(n => ({ key: n, label: n }))),
    buildColorCfgGroupHtml("priority", "依優先級配色", [
      { key: "high", label: "高優先" },
      { key: "medium", label: "中優先" },
      { key: "low", label: "低優先" },
    ]),
    buildColorCfgGroupHtml("schedule", "依進度偏差配色（預期 vs 實際）", [
      { key: "early", label: "提前完成" },
      { key: "ontime", label: "準時完成" },
      { key: "late", label: "延遲完成" },
      { key: "overdue", label: "進行中逾期" },
      { key: "soon", label: "即將到期" },
      { key: "normal", label: "正常進行" },
    ]),
  ];
  body.innerHTML = groups.join("");

  body.querySelectorAll('input[type="color"]').forEach(inp => {
    inp.addEventListener("input", () => {
      const grp = inp.dataset.cfgGroup;
      const key = inp.dataset.cfgKey;
      const val = inp.value;
      customColors[grp] = customColors[grp] || {};
      customColors[grp][key] = val;
      saveColorPrefs();
      const pill = inp.parentElement.querySelector(".color-pill");
      if (pill) {
        pill.style.background = val;
        pill.style.color = contrastTextColor(val);
      }
      render();
    });
  });

  document.getElementById("colorModal").classList.add("open");
};

document.getElementById("colorModeSelect").addEventListener("change", e => {
  colorMode = e.target.value;
  saveColorPrefs();
  render();
  showToast(`已切換底色顯示模式：${COLOR_MODE_LABEL[colorMode]}`);
});
document.getElementById("btnColorConfig").addEventListener("click", openColorModal);
document.getElementById("cfgColorMode").addEventListener("change", e => {
  colorMode = e.target.value;
  document.getElementById("colorModeSelect").value = colorMode;
  saveColorPrefs();
  openColorModal();
  render();
});
document.getElementById("btnResetColors").addEventListener("click", () => {
  customColors = { status: {}, priority: {}, schedule: {}, assignee: {}, task: {} };
  saveColorPrefs();
  openColorModal();
  render();
  showToast("已恢復為系統預設配色");
});
document.getElementById("btnCloseColorModal").addEventListener("click", () => {
  document.getElementById("colorModal").classList.remove("open");
});

// ---------- Built-in 8-Step Interactive Spotlight Tour ----------
const TOUR_STEPS = [
  {
    title: "① 切換與管理不同專案",
    body: "如果同時有好幾個案子，點這裡就能切換，每個專案的排程都分開管理、不會混在一起。",
    tip: "小技巧：系統管理員與專案管理員旁邊會顯示「+ 專案」與「專案設定」，可以新增、改名或刪除專案。",
    getEl: () => document.getElementById("projectSelect"),
  },
  {
    title: "② 確認綠燈「已同步」",
    body: "只要看到這個綠燈，就代表你跟其他同事看到的是同一份最新進度。改完資料會自動存檔，不需要按任何存檔鍵。",
    tip: "小技巧：如果變成紅燈「與伺服器斷線」，請提醒開機的同事檢查小黑窗有沒有不小心被關掉。",
    getEl: () => document.getElementById("syncBadge"),
  },
  {
    title: "③ 切換 5 種看法（含進度偏差分析）",
    body: "同一份資料可切換「甘特圖」、「看板」、「清單」、「📊 進度分析」（看預期與實際完成日期的提前／延遲統計）與「動態紀錄」。",
    tip: "小技巧：右側「🎨 分色」選單與「調色盤」可依任務、負責人、狀態、優先級或進度偏差自訂文字底色！",
    getEl: () => document.querySelector(".tabs"),
  },
  {
    title: "④ 選擇自己的名字與四級權限",
    body: "點這裡切換成你的名字。系統分為 4 種角色：系統管理員（管帳號與全部設定）、專案管理員（管專案與排程）、一般成員（編輯任務）、唯讀檢視（僅觀看）。",
    tip: "小技巧：有鎖頭 🔒 的名字需要輸入 PIN 碼；登入後也可以在裡面自行設定或修改 PIN 碼。",
    getEl: () => document.getElementById("btnIdentity"),
  },
  {
    title: "⑤ 新增任務或金色里程碑",
    body: "點這個藍色按鈕就能建立新工作。填好名稱、負責人與日期按下儲存，同事的畫面 3 秒內就會自動出現。",
    tip: "小技巧：遇到「提案日、截稿日、驗收日」這種重要單日節點，記得勾選「◆ 設為里程碑」，圖上會出現金色菱形！",
    getEl: () => document.getElementById("btnNewTask"),
  },
  {
    title: "⑥ 甘特圖「日／週／月／季／年」縮放、時分排程與子任務收合",
    body: "右上方縮放鈕可切換「日（每2小時）／週／月／季（3個月一季）／年」五種視野；點左側 ▾／▸ 可展開或收合子任務；填寫精準到時分的「實際完成時間」後，甘特圖會自動標示提前／延遲！",
    tip: "小技巧：點頂部「📅 休假日」可設定農曆過年等連假，跨假任務會自動截斷顯示，也可一鍵順延或實體拆分！",
    getEl: () => document.querySelector(".gantt-bar, .gantt-ms-wrap") || document.getElementById("ganttRight"),
    needGantt: true,
  },
  {
    title: "⑦ 網頁直接開共用資料夾與檔案",
    body: "任務名稱旁邊若出現 📂 或 📄 小按鈕，直接點一下就能在瀏覽器裡展開資料夾、預覽 PDF／圖片或下載檔案，不用再手動找資料夾！",
    tip: "小技巧：編輯任務時按「📂 瀏覽並挑選檔案／資料夾」就能直接掛載檔案；點頂部「📂 共用檔案」也能隨時逛整個共用資料夾。",
    getEl: () => document.querySelector(".link-icon-btn") || document.getElementById("btnOpenSharedFiles"),
    needGantt: true,
  },
  {
    title: "⑧ 匯出 Excel 報表與重新看教學",
    body: "開會或跟主管報告前，點「匯出 CSV」就能下載報表直接用 Excel 打開（已處理中文不亂碼）。",
    tip: "小技巧：忘記怎麼操作隨時點旁邊的「❓ 操作教學」就能再看一次！也可以按鍵盤左右鍵切換步驟。",
    getEl: () => document.getElementById("btnExportCSV"),
  },
];

function startTour(idx = 0) {
  document.getElementById("btnTour").classList.remove("pulse");
  localStorage.setItem("pm_lan_tour_seen", "1");
  document.getElementById("loginModal").classList.remove("open");
  document.getElementById("taskModal").classList.remove("open");
  document.getElementById("settingsModal").classList.remove("open");
  document.getElementById("fsModal").classList.remove("open");
  document.getElementById("colorModal").classList.remove("open");
  document.getElementById("holidayModal").classList.remove("open");

  tourIndex = idx;
  if (TOUR_STEPS[tourIndex].needGantt && currentView !== "gantt") {
    currentView = "gantt";
    document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.view === "gantt"));
    render();
  }
  positionTour();
}

function endTour() {
  tourIndex = -1;
  ["tourMaskTop", "tourMaskBottom", "tourMaskLeft", "tourMaskRight", "tourSpot", "tourCard"].forEach(id => {
    document.getElementById(id).style.display = "none";
  });
}

function positionTour() {
  if (tourIndex < 0 || tourIndex >= TOUR_STEPS.length) return;
  const step = TOUR_STEPS[tourIndex];
  if (step.needGantt && currentView !== "gantt") {
    currentView = "gantt";
    document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.view === "gantt"));
    renderGantt();
  }

  let target = step.getEl ? step.getEl() : null;
  if (target) {
    try { target.scrollIntoView({ behavior: "instant", block: "nearest", inline: "center" }); } catch (_) {}
  }

  let rect = target ? target.getBoundingClientRect() : null;
  if (!rect || rect.width < 2 || rect.height < 2) {
    rect = document.querySelector("header").getBoundingClientRect();
  }

  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const pad = 6;
  const top = Math.max(0, rect.top - pad);
  const left = Math.max(0, rect.left - pad);
  const right = Math.min(vw, rect.right + pad);
  const bottom = Math.min(vh, rect.bottom + pad);
  const w = Math.max(10, right - left);
  const h = Math.max(10, bottom - top);

  const mTop = document.getElementById("tourMaskTop");
  const mBot = document.getElementById("tourMaskBottom");
  const mLeft = document.getElementById("tourMaskLeft");
  const mRight = document.getElementById("tourMaskRight");

  mTop.style.cssText = `display:block;top:0;left:0;width:${vw}px;height:${top}px;`;
  mBot.style.cssText = `display:block;top:${bottom}px;left:0;width:${vw}px;height:${Math.max(0, vh - bottom)}px;`;
  mLeft.style.cssText = `display:block;top:${top}px;left:0;width:${left}px;height:${h}px;`;
  mRight.style.cssText = `display:block;top:${top}px;left:${right}px;width:${Math.max(0, vw - right)}px;height:${h}px;`;

  const spot = document.getElementById("tourSpot");
  spot.style.cssText = `display:block;top:${top}px;left:${left}px;width:${w}px;height:${h}px;`;
  document.getElementById("tourSpotNum").textContent = String(tourIndex + 1);

  const card = document.getElementById("tourCard");
  document.getElementById("tourTitle").textContent = step.title;
  document.getElementById("tourBody").textContent = step.body;
  document.getElementById("tourTip").textContent = step.tip;
  document.getElementById("tourPrev").disabled = tourIndex === 0;
  document.getElementById("tourNext").textContent = tourIndex === TOUR_STEPS.length - 1 ? "完成教學 ✓" : "下一步";

  const dotsEl = document.getElementById("tourDots");
  dotsEl.innerHTML = TOUR_STEPS.map((_, i) =>
    `<button class="tour-dot ${i === tourIndex ? "active" : ""}" data-step="${i}">${i + 1}</button>`
  ).join("");
  dotsEl.querySelectorAll(".tour-dot").forEach(b => {
    b.addEventListener("click", () => startTour(Number(b.dataset.step)));
  });

  card.style.display = "block";
  const cw = card.offsetWidth || 380;
  const ch = card.offsetHeight || 240;

  let cardTop = bottom + 14;
  if (cardTop + ch > vh - 12) {
    cardTop = Math.max(12, top - ch - 14);
  }
  let cardLeft = left;
  if (cardLeft + cw > vw - 12) {
    cardLeft = Math.max(12, vw - cw - 12);
  }
  card.style.top = cardTop + "px";
  card.style.left = cardLeft + "px";
}

document.getElementById("btnTour").addEventListener("click", () => startTour(0));
document.getElementById("tourClose").addEventListener("click", endTour);
document.getElementById("tourPrev").addEventListener("click", () => { if (tourIndex > 0) startTour(tourIndex - 1); });
document.getElementById("tourNext").addEventListener("click", () => {
  if (tourIndex < TOUR_STEPS.length - 1) startTour(tourIndex + 1);
  else endTour();
});
["tourMaskTop", "tourMaskBottom", "tourMaskLeft", "tourMaskRight"].forEach(id => {
  document.getElementById(id).addEventListener("click", endTour);
});
window.addEventListener("resize", () => { if (tourIndex >= 0) positionTour(); });
document.addEventListener("keydown", e => {
  if (tourIndex < 0) return;
  if (e.key === "Escape") endTour();
  else if (e.key === "ArrowRight") {
    if (tourIndex < TOUR_STEPS.length - 1) startTour(tourIndex + 1);
    else endTour();
  } else if (e.key === "ArrowLeft" && tourIndex > 0) {
    startTour(tourIndex - 1);
  }
});

// ---------- Global Tab & Filter Events ----------
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentView = btn.dataset.view;
    render();
  });
});
document.getElementById("filterAssignee").addEventListener("change", render);
document.getElementById("searchBox").addEventListener("input", render);

fetchState(true);
/* ---------- 深色／淺色主題切換 ---------- */
(function () {
  var headBtn = document.getElementById("themeToggleBtn");
  var fab = document.getElementById("themeFab");
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function current() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }
  function apply(mode) {
    var isDark = mode === "dark";
    if (isDark) { document.documentElement.setAttribute("data-theme", "dark"); }
    else { document.documentElement.removeAttribute("data-theme"); }
    if (headBtn) {
      headBtn.textContent = isDark ? "\u2600\uFE0F 淺色" : "\uD83C\uDF19 主題";
      headBtn.title = isDark ? "目前為深色主題，點擊切換淺色" : "目前為淺色主題，點擊切換深色";
    }
    if (fab) {
      fab.textContent = isDark ? "\u2600\uFE0F" : "\uD83C\uDF19";
      fab.title = isDark ? "切換為淺色主題" : "切換為深色主題";
    }
  }
  function toggle() {
    var next = current() === "dark" ? "light" : "dark";
    try { localStorage.setItem("pm_theme", next); } catch (e) {}
    try {
      if (location.search.indexOf("theme=") !== -1) {
        var u = new URL(location.href);
        u.searchParams.delete("theme");
        history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
      }
    } catch (e) {}
    apply(next);
  }
  apply(current());
  if (headBtn) { headBtn.addEventListener("click", toggle); }
  if (fab) { fab.addEventListener("click", toggle); }
  if (mq && mq.addEventListener) {
    mq.addEventListener("change", function (ev) {
      var saved = null;
      try { saved = localStorage.getItem("pm_theme"); } catch (e) {}
      if (!saved) { apply(ev.matches ? "dark" : "light"); }
    });
  }
})();
setInterval(() => fetchState(false), 2500);
</script>
</body>
</html>
"""


def bind_server(start_port, max_tries=20):
    for p in range(start_port, start_port + max_tries):
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", p), PMHandler)
            return srv, p
        except OSError:
            continue
    raise RuntimeError(
        f"埠號 {start_port} 至 {start_port + max_tries - 1} 皆已被佔用，請指定其他埠號（例如 python pm_server-v11.py 9500）。"
    )


def main():
    disable_windows_quick_edit()
    ensure_data_file()
    if RESET_ADMIN_FLAG:
        names = reset_admin_pins()
        reset_view_password()
        print(f"已重設系統管理員（{', '.join(names)}）PIN 為 1234，觀看密碼為 {DEFAULT_VIEW_PASS}")
    elif RESET_VIEW_PASS_FLAG:
        reset_view_password()
        print(f"已重設觀看密碼為：{DEFAULT_VIEW_PASS}")

    with DATA_LOCK:
        cur = load_data_unlocked()
        roots_summary = [(r["label"], r["path"]) for r in cur.get("sharedRoots", [])]
        vp_enabled = bool(cur.get("viewPassEnabled", True))

    gh_cfg = load_github_config()
    sync_thread = threading.Thread(target=cloud_sync_worker, daemon=True)
    sync_thread.start()

    lan_ip = get_lan_ip()
    host_name = socket.gethostname()
    server, actual_port = bind_server(REQUESTED_PORT)
    print("=" * 72)
    print("  團隊專案管理伺服器 v11（GitHub 私有倉庫雙向同步＋季檢視＋休假日截斷）")
    if actual_port != REQUESTED_PORT:
        print(f"  注意：埠號 {REQUESTED_PORT} 已被佔用，已自動改用 {actual_port}")
    print(f"  本機開啟: http://127.0.0.1:{actual_port}")
    print(f"  區網 IP : http://{lan_ip}:{actual_port}")
    print(f"  電腦名稱: http://{host_name}:{actual_port}  （不怕 IP 變動）")
    print(f"  資料位置: {DATA_FILE}")
    for lbl, rpath in roots_summary:
        print(f"  共用目錄: [{lbl}] {rpath}")
    if gh_cfg["enabled"] and gh_cfg["owner"] and gh_cfg["repo"]:
        print(f"  雲端同步: 已啟用 → GitHub 私有庫 {gh_cfg['owner']}/{gh_cfg['repo']} ({gh_cfg['branch']})")
    else:
        print("  雲端同步: 未啟用（系統管理員可至網頁「系統設定 → ☁️ 雲端同步」設定）")
    print(f"  觀看密碼: {'已啟用（首次預設 ' + DEFAULT_VIEW_PASS + '）' if vp_enabled else '已停用'}")
    print("  系統管理員預設 PIN: 1234（忘記任一密碼可執行 python pm_server-v11.py --reset-admin）")
    print("=" * 72)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n伺服器已安全停止。")
        server.server_close()


if __name__ == "__main__":
    main()
