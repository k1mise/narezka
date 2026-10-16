"""Диагностика истории: что лежит в базе и что отдаёт API. Ничего не меняет и не печатает ключи/токены.
Запуск (из папки narez, программа может быть запущена):  python check_history.py"""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import re  # noqa: E402

# путь к базе берём из config.toml без сторонних библиотек (запускается любым Python, в том числе вне виртуального окружения)
m = re.search(r'^\s*db\s*=\s*"([^"]+)"', (ROOT / "config.toml").read_text(encoding="utf-8"), re.M)
DB_PATH = Path(m.group(1) if m else "narez.db")
DB_PATH = DB_PATH if DB_PATH.is_absolute() else ROOT / DB_PATH
cfg = {"paths": {"db": DB_PATH}}
db = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
q = lambda sql, *a: db.execute(sql, a).fetchall()

print("База:", cfg["paths"]["db"])
print("\n== Группы и аккаунты ==")
for g in q("SELECT * FROM groups ORDER BY id"):
    accs = q("SELECT id, platform, label FROM accounts WHERE group_id=?", g["id"])
    print(f"группа {g['id']} «{g['name']}»: " + ", ".join(f"{a['platform']}#{a['id']} «{a['label']}»" for a in accs))

print("\n== Записей в истории (publications) ==")
rows = q("SELECT p.*, g.name AS gname, a.label AS alabel FROM publications p "
         "LEFT JOIN groups g ON g.id=p.group_id LEFT JOIN accounts a ON a.id=p.account_id ORDER BY p.id")
print("всего:", len(rows))
for r in q("SELECT platform, group_id, account_id, COUNT(*) n, SUM(clip_id IS NULL) imported FROM publications GROUP BY 1,2,3"):
    print(f"  {r['platform']:<9} группа={r['group_id']} аккаунт={r['account_id']}: {r['n']} шт. (из них добавлено с канала/вручную: {r['imported']})")

print("\n== Последние 15 записей ==")
for r in rows[-15:][::-1]:
    print(f"  #{r['id']} {r['platform']} группа={r['group_id']}({r['gname']}) акк={r['account_id']}({r['alabel']}) clip={r['clip_id']} "
          f"id={r['video_id']} {r['uploaded_at']} privacy={r['privacy']!r} «{(r['title'] or '')[:40]}»")

print("\n== Клипы по статусам ==")
for r in q("SELECT status, COUNT(*) n FROM clips GROUP BY 1"):
    print(f"  {r['status']}: {r['n']}")
lost = q("SELECT c.id, c.title FROM clips c WHERE c.status='published' AND NOT EXISTS (SELECT 1 FROM publications p WHERE p.clip_id=c.id)")
print(f"\nКлипов со статусом «published», у которых НЕТ записи в истории: {len(lost)}")
for r in lost[:15]:
    print(f"  клип #{r['id']} «{(r['title'] or '')[:50]}»")

print("\n== Что отдаёт сервер (/api/history) ==")
try:
    with urllib.request.urlopen("http://localhost:8000/api/history", timeout=5) as resp:
        data = json.load(resp)
    print("записей в ответе:", len(data), "| в базе:", len(rows))
except Exception as e:  # noqa: BLE001
    print("сервер не отвечает на localhost:8000 (запусти программу, чтобы сравнить):", e)
