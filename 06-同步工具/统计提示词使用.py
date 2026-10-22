# -*- coding: utf-8 -*-
"""扫描 ZCode 会话数据库，统计提示词体系文档被 Read 工具读取的记录。

用途：定期体检哪些提示词文档在实际会话中被使用，找出长期零读取的精简候选。
数据源：C:\\Users\\23973\\.zcode\\cli\\db\\sqlite 的 part 表（全部会话的工具调用记录）。

口径说明：
1. 只统计 Read 工具调用；工作流骨架注入的子 Agent 角色提示词（03-协作提示词\\03-子Agent详细提示词）
   不经过主会话 Read，因此零读取不代表废弃，需结合 07-工作流体系 判断。
2. 只覆盖 ZCode 会话；用户在其他工具（Claude/Codex）粘贴快捷短语触发的读取不在统计内。
3. 文件改名前的读取记录挂在旧路径名下；同名不同目录的读取会在"旧路径提示"中标注。

用法：python 统计提示词使用.py
"""
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DB_PATH = r"C:\Users\23973\.zcode\cli\db\db.sqlite"
REPO_ROOT = r"D:\1poj\学习\智能体提示词体系"


def norm(path: str) -> str:
    """统一分隔符为反斜杠并转小写，便于前缀匹配。"""
    return path.replace("/", "\\").lower()


def to_date(value):
    """把时间字段转成日期字符串，兼容 epoch 毫秒/秒与 ISO 字符串。"""
    if value is None:
        return "?"
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return "?"
    return str(value)[:10]


def main():
    root_norm = norm(REPO_ROOT)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 先用 LIKE 粗筛包含目录名的记录，再解析 JSON 精确过滤，避免全表逐条解析过慢
    cur.execute(
        "SELECT session_id, data, time_created FROM part WHERE data LIKE ?",
        ("%" + "智能体提示词体系" + "%",),
    )

    # rel_path -> {"count": n, "sessions": set, "last": date}
    reads = defaultdict(lambda: {"count": 0, "sessions": set(), "last": ""})
    scanned = 0

    for session_id, data_raw, time_created in cur:
        try:
            data = json.loads(data_raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or data.get("tool") != "Read":
            continue
        state = data.get("state")
        if not isinstance(state, dict):
            continue
        file_path = (state.get("input") or {}).get("file_path")
        if not isinstance(file_path, str):
            continue
        p = norm(file_path)
        if not p.startswith(root_norm + "\\"):
            continue
        rel = file_path[len(REPO_ROOT):].lstrip("\\").replace("\\", "/")
        rec = reads[rel]
        rec["count"] += 1
        rec["sessions"].add(session_id)
        d = to_date(time_created)
        if d and d != "?" and d > (rec["last"] if rec["last"] != "?" else ""):
            rec["last"] = d
        scanned += 1

    total_sessions = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
    conn.close()

    print(f"会话总数: {total_sessions}")
    print(f"命中提示词体系的 Read 调用: {scanned} 次, 涉及 {len(reads)} 个不同路径\n")

    print("=" * 90)
    print("一、被读取过的文档（按读取次数降序）")
    print("=" * 90)
    print(f"{'相对路径':<62}{'次数':>4} {'会话数':>4}  最近读取")
    for rel, rec in sorted(reads.items(), key=lambda kv: -kv[1]["count"]):
        print(f"{rel:<62}{rec['count']:>4} {len(rec['sessions']):>4}  {rec['last']}")

    # 对照当前仓库：列出仍存在但从未被读取的 md 文件
    repo = Path(REPO_ROOT)
    current_files = sorted(
        p.relative_to(repo).as_posix()
        for p in repo.rglob("*.md")
    )
    read_set = {norm(r) for r in reads}
    basename_read = {Path(r).name.lower() for r in reads}

    never = []
    renamed_note = []
    for rel in current_files:
        if norm(rel) in read_set:
            continue
        # 同名文件曾在旧路径下被读过（目录调整/改名），单独标注避免误判为从未使用
        if Path(rel).name.lower() in basename_read:
            renamed_note.append(rel)
        else:
            never.append(rel)

    print()
    print("=" * 90)
    print("二、当前仓库存在但无读取记录的 .md（精简候选，需人工确认）")
    print("=" * 90)
    print(f"共 {len(never)} 个从未被读取:\n")
    for rel in never:
        print(f"  {rel}")
    if renamed_note:
        print(f"\n另有 {len(renamed_note)} 个文件本体未被读到，但同名文件曾在其他（旧）路径被读取（可能只是目录调整）:")
        for rel in renamed_note:
            print(f"  {rel}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
