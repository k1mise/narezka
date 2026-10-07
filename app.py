"""Запуск GUI:  python app.py"""
from __future__ import annotations

import datetime as dt
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from narez import banner as bn
from narez import schedule
from narez.cleanup import discard_clip
from narez.config import load_config
from narez.db import DB, PLATFORMS
from narez.history import import_videos, parse_video_ids
from narez.meta import normalize_tags
from narez.pipeline import run
from narez.publishers import get_publisher
from narez.service import publish_clips

POS_LABELS = {"center": "по центру", "top": "сверху", "bottom": "снизу"}


def open_file(p: str):
    if sys.platform == "win32":
        os.startfile(p)
    else:
        subprocess.Popen(["xdg-open", p])


def parse_iso(s: str):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def pub_status(p) -> str:
    priv, at = p["privacy"] or "", parse_iso(p["publish_at"] or "")
    if priv == "deleted":
        return "Удалено"
    if priv == "public":
        return "Опубликовано"
    if priv == "unlisted":
        return "По ссылке"
    if at and at > dt.datetime.now(dt.timezone.utc):
        return f"Запланировано на {at.astimezone():%d.%m %H:%M}"
    return "Закрытое" if priv == "private" else "Загружено"


def num(v) -> str:
    return "—" if v is None else str(v)


def ask_text(parent, title: str, prompt: str) -> str | None:
    """Небольшое окно с многострочным полем (simpledialog умеет только одну строку)."""
    win = tk.Toplevel(parent)
    win.title(title); win.geometry("640x340"); win.transient(parent); win.grab_set()
    ttk.Label(win, text=prompt, wraplength=600, justify="left").pack(anchor="w", padx=10, pady=(10, 4))
    t = tk.Text(win, height=10, wrap="word"); t.pack(fill="both", expand=True, padx=10)
    res: dict = {"v": None}

    def ok():
        res["v"] = t.get("1.0", "end").strip(); win.destroy()
    row = ttk.Frame(win); row.pack(fill="x", padx=10, pady=10)
    ttk.Button(row, text="Добавить", command=ok).pack(side="right", padx=2)
    ttk.Button(row, text="Отмена", command=win.destroy).pack(side="right", padx=2)
    t.focus_set(); parent.wait_window(win)
    return res["v"]


class ClipEditor(tk.Toplevel):
    """Правка названия, описания, хэштегов и баннера клипа перед публикацией."""

    def __init__(self, app: "App", clip_id: int):
        super().__init__(app)
        self.app, self.cfg, self.clip_id = app, app.cfg, clip_id
        clip = app.db.clip(clip_id)
        spec = json.loads(clip["banner"]) if clip["banner"] else {}
        self.margin = int(spec.get("margin", self.cfg["banner"].get("margin", 160)))
        self.title("Редактирование клипа")
        self.geometry("700x720")
        self.transient(app)
        self.q: queue.Queue = queue.Queue()

        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        self.v_title = tk.StringVar(value=clip["title"] or "")
        ttk.Label(f, text="Название").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_title).grid(row=0, column=1, columnspan=3, sticky="we", pady=2)

        ttk.Label(f, text="Описание").grid(row=1, column=0, sticky="nw")
        self.t_desc = tk.Text(f, height=7, wrap="word")
        self.t_desc.grid(row=1, column=1, columnspan=3, sticky="we", pady=2)
        self.t_desc.insert("1.0", clip["reason"] or "")

        self.v_tags = tk.StringVar(value=clip["tags"] or "")
        ttk.Label(f, text="Хэштеги").grid(row=2, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_tags).grid(row=2, column=1, columnspan=3, sticky="we", pady=2)

        ttk.Separator(f).grid(row=3, column=0, columnspan=4, sticky="we", pady=8)
        ttk.Label(f, text="Баннер (картинка PNG/JPG или видео, в том числе со звуком)").grid(
            row=4, column=0, columnspan=4, sticky="w")
        self.v_path = tk.StringVar(value=spec.get("path", ""))
        ttk.Label(f, text="Файл").grid(row=5, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_path).grid(row=5, column=1, sticky="we", pady=2)
        ttk.Button(f, text="Выбрать…", command=self._pick).grid(row=5, column=2, padx=4)
        ttk.Button(f, text="Убрать", command=self._clear).grid(row=5, column=3)
        self.info = ttk.Label(f, text="", wraplength=620, foreground="#555")
        self.info.grid(row=6, column=0, columnspan=4, sticky="w", pady=2)

        self.v_start = tk.StringVar(value=str(spec.get("start", self.cfg["banner"].get("start_at", 3.0))))
        self.v_dur = tk.StringVar(value=str(spec.get("duration", 0)))
        self.v_width = tk.StringVar(value=str(spec.get("width_percent", self.cfg["banner"].get("video_width_percent", 100))))
        self.v_duck = tk.StringVar(value=str(spec.get("duck_percent", self.cfg["banner"].get("duck_percent", 15))))
        self.v_pos = tk.StringVar(value=POS_LABELS.get(spec.get("position", self.cfg["banner"].get("position", "center")), "по центру"))
        grid = ttk.Frame(f)
        grid.grid(row=7, column=0, columnspan=4, sticky="we", pady=4)
        for i, (lbl, var) in enumerate([("Появляется на секунде", self.v_start),
                                        ("Длительность, с (0 = видео целиком)", self.v_dur),
                                        ("Ширина, % кадра", self.v_width),
                                        ("Звук основного видео, % (пока идёт баннер со звуком)", self.v_duck)]):
            ttk.Label(grid, text=lbl).grid(row=i, column=0, sticky="w")
            ttk.Entry(grid, textvariable=var, width=8).grid(row=i, column=1, padx=6, pady=1)
        ttk.Label(grid, text="Положение").grid(row=4, column=0, sticky="w")
        ttk.Combobox(grid, textvariable=self.v_pos, values=list(POS_LABELS.values()), state="readonly",
                     width=12).grid(row=4, column=1, padx=6, pady=1)
        ttk.Label(f, text="Видео-баннер автоматически растягивается, чтобы занимать не меньше "
                          f"{self.cfg['banner'].get('min_area_percent', 25)}% экрана.",
                  foreground="#555", wraplength=620).grid(row=8, column=0, columnspan=4, sticky="w")

        ttk.Separator(f).grid(row=9, column=0, columnspan=4, sticky="we", pady=8)
        self.v_time = tk.StringVar(value=schedule.format_local(clip["publish_at"]))
        ttk.Label(f, text="Время публикации").grid(row=10, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_time, width=20).grid(row=10, column=1, sticky="w", pady=2)
        ttk.Label(f, text="формат 07.10.2026 18:30, время вашего ПК. Пусто — по обычным правилам (первый клип сразу, "
                          "следующие с задержкой из config.toml).", foreground="#555", wraplength=620
                  ).grid(row=11, column=0, columnspan=4, sticky="w")

        btns = ttk.Frame(f)
        btns.grid(row=12, column=0, columnspan=4, sticky="we", pady=(10, 4))
        self.b_plain = ttk.Button(btns, text="Открыть клип в плеере", command=self._open_plain)
        self.b_prev = ttk.Button(btns, text="Предпросмотр с баннером", command=self._preview)
        self.b_plain.pack(side="left", padx=2)
        self.b_prev.pack(side="left", padx=2)
        ttk.Button(btns, text="Сохранить и закрыть", command=self._save_close).pack(side="right", padx=2)
        ttk.Button(btns, text="Убрать клип из очереди", command=self._discard).pack(side="right", padx=2)
        pub = ttk.Frame(f)
        pub.grid(row=13, column=0, columnspan=4, sticky="we", pady=4)
        ttk.Button(pub, text="Опубликовать сейчас", command=self._publish_now).pack(side="left", padx=2)
        ttk.Button(pub, text="Запланировать на указанное время", command=self._publish_scheduled).pack(side="left", padx=2)
        self.status = ttk.Label(f, text="", foreground="#555")
        self.status.grid(row=14, column=0, columnspan=4, sticky="w")

        self._describe()
        self.after(200, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # --- баннер ---
    def _spec(self) -> dict | None:
        path = self.v_path.get().strip()
        if not path:
            return None

        def f(v, d):
            try:
                return float(v.get().replace(",", "."))
            except ValueError:
                return d
        pos = next((k for k, v in POS_LABELS.items() if v == self.v_pos.get()), "center")
        return {"path": path, "start": max(f(self.v_start, 3.0), 0.0), "duration": max(f(self.v_dur, 0.0), 0.0),
                "width_percent": min(max(f(self.v_width, 100.0), 10.0), 100.0), "position": pos,
                "margin": self.margin, "duck_percent": int(min(max(f(self.v_duck, 15.0), 0.0), 100.0))}

    def _describe(self):
        spec = self._spec()
        if not spec:
            self.info.config(text="Баннер не выбран.")
            return
        try:
            self.info.config(text=bn.describe(spec, self.cfg))
        except bn.BannerError as e:
            self.info.config(text=f"⚠ {e}")

    def _pick(self):
        p = filedialog.askopenfilename(parent=self, title="Баннер", filetypes=[
            ("Картинки и видео", "*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.mkv *.webm *.m4v"), ("Все файлы", "*.*")])
        if not p:
            return
        d = bn.default_spec(self.cfg, p)  # для нового файла подставляем разумные значения
        self.v_path.set(p)
        self.v_start.set(str(d["start"]))
        self.v_dur.set(str(d["duration"]))
        self.v_width.set(str(d["width_percent"]))
        self.v_duck.set(str(d["duck_percent"]))
        self.v_pos.set(POS_LABELS[d["position"]])
        self._describe()

    def _clear(self):
        self.v_path.set("")
        self._describe()

    # --- сохранение ---
    def _save(self, publish_at: str | None = None) -> bool:
        """Сохраняет правки. publish_at: None — взять время из поля, '' — очистить, иначе — готовая ISO-строка.
        False — если время в поле введено неверно (правки при этом не сохраняются)."""
        if publish_at is None:
            try:
                d = schedule.parse_local(self.v_time.get())
            except ValueError as e:
                messagebox.showerror("Время публикации", str(e), parent=self)
                return False
            publish_at = schedule.to_iso(d) if d else ""
        spec = self._spec()
        self.app.db.update_clip(
            self.clip_id, title=self.v_title.get().strip(), reason=self.t_desc.get("1.0", "end").strip(),
            tags=normalize_tags(self.v_tags.get()), publish_at=publish_at,
            banner=json.dumps(spec, ensure_ascii=False) if spec else "")
        return True

    def _close(self):
        self.app._reload_clips()
        self.destroy()

    def _save_close(self):
        if self._save():
            self._close()

    def _publish_now(self):
        """Правки сохраняются, клип сразу уходит во все аккаунты группы (время из поля игнорируется и очищается)."""
        self.v_time.set("")
        if self._save(""):
            self._go()

    def _publish_scheduled(self):
        """Правки сохраняются, клип загружается сейчас и выходит на YouTube в указанное время."""
        try:
            d = schedule.parse_local(self.v_time.get())
        except ValueError as e:
            return messagebox.showerror("Время публикации", str(e), parent=self)
        if not d:
            return messagebox.showinfo("Время публикации", "Укажи время публикации или нажми «Опубликовать сейчас».", parent=self)
        if d < dt.datetime.now(dt.timezone.utc) + schedule.MIN_AHEAD:
            return messagebox.showerror("Время публикации", "Время должно быть минимум на 5 минут позже текущего.", parent=self)
        if self._save(schedule.to_iso(d)):
            self._go()

    def _go(self):
        cid, app = self.clip_id, self.app
        self._close()
        app._publish([cid])

    def _discard(self):
        if messagebox.askyesno("Убрать клип", "Клип будет удалён с диска и не опубликуется. Продолжить?", parent=self):
            discard_clip(self.app.db, self.cfg, self.clip_id)
            self._close()

    # --- просмотр ---
    def _open_plain(self):
        p = self.app.db.clip(self.clip_id)["path"]
        if p and Path(p).exists():
            open_file(p)
        else:
            messagebox.showinfo("narez", "Файл клипа не найден.", parent=self)

    def _preview(self):
        self._save()
        self.b_prev.state(["disabled"])
        self.status.config(text="Накладываю баннер…")
        cfg, dbp, cid = self.cfg, self.cfg["paths"]["db"], self.clip_id

        def work():
            try:
                db = DB(dbp)
                self.q.put(("open", str(bn.ensure_final(db, db.clip(cid), cfg))))
            except Exception as e:
                self.q.put(("err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                k, v = self.q.get_nowait()
                self.b_prev.state(["!disabled"])
                if k == "open":
                    self.status.config(text="Готово.")
                    open_file(v)
                else:
                    self.status.config(text="")
                    messagebox.showerror("Баннер", v, parent=self)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(200, self._poll)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("narez — нарезка шортсов")
        self.geometry("1000x680")
        self.cfg = load_config()
        self.db = DB(self.cfg["paths"]["db"])
        self.q: queue.Queue = queue.Queue()
        self.clips: list[dict] = []
        self.busy = False
        self.publishing = False
        self._imgs: dict[int, tk.PhotoImage] = {}
        self._ui()
        self._reload_groups()
        self._reload_history()
        self.after(150, self._poll)

    def _ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        main, hist = ttk.Frame(nb), ttk.Frame(nb)
        nb.add(main, text="Нарезка и публикация")
        nb.add(hist, text="История")

        left = ttk.Frame(main, padding=8); left.pack(side="left", fill="y")
        ttk.Label(left, text="Группы аккаунтов").pack(anchor="w")
        self.gl = tk.Listbox(left, width=24, height=12, exportselection=False)
        self.gl.pack(fill="y"); self.gl.bind("<<ListboxSelect>>", lambda e: self._on_group())
        ttk.Button(left, text="+ Группа", command=self._add_group).pack(fill="x", pady=2)
        ttk.Label(left, text="Аккаунты группы").pack(anchor="w", pady=(10, 0))
        self.al = tk.Listbox(left, width=24, height=6); self.al.pack(fill="x")
        ttk.Button(left, text="Привязать аккаунт", command=self._set_account).pack(fill="x", pady=2)
        ttk.Button(left, text="Войти в YouTube", command=self._yt_login).pack(fill="x", pady=2)

        right = ttk.Frame(main, padding=8); right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="Ссылка на видео (YouTube и др.)").pack(anchor="w")
        row = ttk.Frame(right); row.pack(fill="x")
        self.url = ttk.Entry(row); self.url.pack(side="left", fill="x", expand=True)
        self.btn = ttk.Button(row, text="Нарезать", command=self._start); self.btn.pack(side="left", padx=4)
        self.log = tk.Text(right, height=11, state="disabled"); self.log.pack(fill="x", pady=6)
        ttk.Label(right, text="Клипы, ждущие публикации (двойной клик — редактировать)").pack(anchor="w")
        self.cl = tk.Listbox(right, height=8, exportselection=False); self.cl.pack(fill="both", expand=True)
        self.cl.bind("<Double-Button-1>", lambda e: self._edit())
        row2 = ttk.Frame(right); row2.pack(fill="x", pady=4)
        ttk.Button(row2, text="Редактировать…", command=self._edit).pack(side="left", padx=2)
        ttk.Button(row2, text="Открыть в плеере", command=self._open).pack(side="left", padx=2)
        self.pub_btn = ttk.Button(right, text="Опубликовать выбранный клип во все аккаунты группы",
                                  command=self._publish_selected)
        self.pub_btn.pack(fill="x", pady=2)
        self.pub_all_btn = ttk.Button(right, text="Опубликовать все клипы из списка", command=self._publish)
        self.pub_all_btn.pack(fill="x", pady=2)

        # --- история ---
        top = ttk.Frame(hist, padding=(8, 8, 8, 0)); top.pack(fill="x")
        ttk.Button(top, text="Обновить статистику", command=self._refresh_stats).pack(side="left")
        ttk.Button(top, text="Открыть в браузере", command=self._open_pub).pack(side="left", padx=6)
        ttk.Button(top, text="Добавить ранее опубликованные…", command=self._import).pack(side="left", padx=6)
        self.hist_info = ttk.Label(top, text="Клик по превью или двойной клик по строке — открыть видео.",
                                   foreground="#555")
        self.hist_info.pack(side="left", padx=8)
        ttk.Style().configure("Hist.Treeview", rowheight=70)
        cols = ("title", "account", "status", "when", "views", "likes", "comments")
        self.hv = ttk.Treeview(hist, columns=cols, show="tree headings", style="Hist.Treeview")
        self.hv.heading("#0", text="Превью"); self.hv.column("#0", width=90, stretch=False)
        for c, t, w in [("title", "Название", 300), ("account", "Группа / аккаунт", 150), ("status", "Статус", 170),
                        ("when", "Загружено", 110), ("views", "Просм.", 60), ("likes", "Лайки", 60),
                        ("comments", "Комм.", 60)]:
            self.hv.heading(c, text=t); self.hv.column(c, width=w, anchor="w" if c in ("title", "account", "status") else "e")
        self.hv.pack(fill="both", expand=True, padx=8, pady=8)
        self.hv.bind("<Double-Button-1>", self._hist_dclick)
        self.hv.bind("<ButtonRelease-1>", self._hist_click)
        self.pub_urls: dict[str, str] = {}

    # --- группы ---
    def _reload_groups(self):
        self.groups = self.db.groups()
        self.gl.delete(0, "end")
        for g in self.groups:
            self.gl.insert("end", g["name"])
        if self.groups and not self.gl.curselection():
            self.gl.selection_set(0); self._on_group()

    def _gid(self):
        s = self.gl.curselection()
        return self.groups[s[0]]["id"] if s else None

    def _add_group(self):
        n = simpledialog.askstring("Новая группа", "Название (например, «Собаки»):")
        if n:
            self.db.add_group(n.strip()); self._reload_groups()

    def _on_group(self):
        self._show_accounts()
        self._reload_clips()

    def _show_accounts(self):
        self.al.delete(0, "end")
        if self._gid():
            for a in self.db.accounts(self._gid()):
                self.al.insert("end", f"{a['platform']}: {a['label']}")

    def _set_account(self):
        if not self._gid():
            return messagebox.showinfo("narez", "Сначала создай группу")
        p = simpledialog.askstring("Платформа", f"Платформа ({', '.join(PLATFORMS)}):")
        if p not in PLATFORMS:
            return
        l = simpledialog.askstring("Аккаунт", "Название/логин аккаунта:")
        if l:
            self.db.set_account(self._gid(), p, l); self._show_accounts()

    # --- клипы ---
    def _reload_clips(self):
        sel = self.cl.curselection()
        self.cl.delete(0, "end")
        self.clips = [dict(c) for c in self.db.pending_clips(self._gid())] if self._gid() else []
        for c in self.clips:
            mark = ("  · баннер" if c["banner"] else "") + (
                f"  · выйдет {schedule.format_local(c['publish_at'])}" if c["publish_at"] else "")
            self.cl.insert("end", f"{c['title']}  ({c['end'] - c['start']:.0f} с){mark}")
        if sel and sel[0] < len(self.clips):
            self.cl.selection_set(sel[0])

    def _selected(self):
        s = self.cl.curselection()
        return self.clips[s[0]] if s else None

    def _edit(self):
        c = self._selected()
        if c:
            ClipEditor(self, c["id"])

    def _open(self):
        c = self._selected()
        if c and c["path"] and Path(c["path"]).exists():
            open_file(c["path"])

    # --- обработка ---
    def _print(self, msg):
        self.q.put(("log", msg))

    def _start(self):
        url = self.url.get().strip()
        if not url or self.busy:
            return
        if not self._gid():
            return messagebox.showinfo("narez", "Сначала создай и выбери группу")
        self.busy = True; self.btn.state(["disabled"])
        gid = self._gid()

        def work():
            try:
                run(url, self.cfg, DB(self.cfg["paths"]["db"]), gid, self._print)
            except Exception as e:
                self.q.put(("log", "ОШИБКА: " + re.sub(r"\x1b\[[0-9;]*m", "", str(e))))  # без цветовых кодов yt-dlp
            self.q.put(("done", None))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                k, v = self.q.get_nowait()
                if k == "log":
                    self.log.config(state="normal"); self.log.insert("end", v + "\n")
                    self.log.see("end"); self.log.config(state="disabled")
                elif k == "done":
                    self.busy = False; self.btn.state(["!disabled"]); self._reload_clips()
                elif k == "pubdone":
                    self.publishing = False
                    for b in (self.pub_btn, self.pub_all_btn):
                        b.state(["!disabled"])
                    self._reload_clips(); self._reload_history()
                elif k == "history":
                    self._reload_history()
                elif k == "hinfo":
                    self.hist_info.config(text=v)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _yt_login(self):
        accs = [a for a in self.db.accounts(self._gid()) if a["platform"] == "youtube"] if self._gid() else []
        if not accs:
            return messagebox.showinfo("narez", "Сначала привяжи аккаунт платформы youtube к группе")
        aid = accs[0]["id"]

        def work():
            try:
                get_publisher("youtube", self.cfg).authorize(aid)
                self._print("YouTube: вход выполнен.")
            except Exception as e:
                self._print(f"ОШИБКА входа в YouTube: {e}")
        threading.Thread(target=work, daemon=True).start()

    # --- публикация ---
    def _publish_selected(self):
        c = self._selected()
        if not c:
            return messagebox.showinfo("narez", "Сначала выбери клип в списке.")
        self._publish([c["id"]])

    def _publish(self, only: list[int] | None = None):
        """Публикует клипы во все аккаунты группы: только перечисленные в only или все из списка."""
        gid = self._gid()
        accs = [dict(a) for a in self.db.accounts(gid)] if gid else []
        if not self.clips or not accs:
            return messagebox.showinfo("narez", "Нужны готовые клипы и привязанные аккаунты группы")
        if self.publishing:
            return
        clip_ids = list(only) if only else [c["id"] for c in self.clips]
        what = (f"клип «{self.db.clip(clip_ids[0])['title']}»" if len(clip_ids) == 1 else f"{len(clip_ids)} клип(ов)")
        if len(clip_ids) == 1:
            at = schedule.from_iso(self.db.clip(clip_ids[0])["publish_at"])
            if at and at > dt.datetime.now(dt.timezone.utc):
                what += f" (выйдет {schedule.format_local(self.db.clip(clip_ids[0])['publish_at'])})"
        if not messagebox.askyesno("Публикация", f"Опубликовать {what} в {len(accs)} аккаунт(ов)?\n"
                                   "Исходники и готовые файлы после успешной загрузки удалятся с диска."):
            return
        self.publishing = True
        for b in (self.pub_btn, self.pub_all_btn):
            b.state(["disabled"])
        cfg = self.cfg

        def work():
            try:
                publish_clips(DB(cfg["paths"]["db"]), cfg, gid, clip_ids, self._print, delete_sources=not self.busy)
            except Exception as e:
                self._print(f"ОШИБКА публикации: {e}")
            self.q.put(("pubdone", None))
        threading.Thread(target=work, daemon=True).start()

    # --- история ---
    def _reload_history(self):
        self.hv.delete(*self.hv.get_children())
        self.pub_urls.clear()
        for p in self.db.publications():
            img = None
            if p["thumb"] and Path(p["thumb"]).exists():
                try:
                    img = tk.PhotoImage(file=p["thumb"]).subsample(2)
                    self._imgs[p["id"]] = img
                except tk.TclError:
                    img = None
            up = parse_iso((p["uploaded_at"] or "").replace(" ", "T") + "+00:00")
            vals = (p["title"], f"{p['group_name'] or ''} / {p['account_label'] or p['platform']}", pub_status(p),
                    f"{up.astimezone():%d.%m %H:%M}" if up else "", num(p["views"]), num(p["likes"]), num(p["comments"]))
            iid = str(p["id"])
            self.hv.insert("", "end", iid=iid, image=img or "", values=vals)
            self.pub_urls[iid] = p["url"]

    def _hist_click(self, e):
        """Один клик по превью (первая колонка) открывает видео."""
        row = self.hv.identify_row(e.y)
        if row and self.hv.identify_column(e.x) == "#0":
            webbrowser.open(self.pub_urls.get(row, ""))

    def _hist_dclick(self, e):
        """Двойной клик по любой другой ячейке строки открывает видео (по пустому месту — ничего)."""
        row = self.hv.identify_row(e.y)
        if row and self.hv.identify_column(e.x) != "#0":
            webbrowser.open(self.pub_urls.get(row, ""))

    def _open_pub(self):
        s = self.hv.selection()
        if s:
            webbrowser.open(self.pub_urls.get(s[0], ""))

    def _import(self):
        """Видео, загруженные до появления истории, добавляются по ссылкам."""
        gid = self._gid()
        accs = [a for a in self.db.accounts(gid) if a["platform"] == "youtube"] if gid else []
        if not accs:
            return messagebox.showinfo("narez", "Выбери на вкладке «Нарезка» группу с привязанным аккаунтом YouTube — "
                                                "видео добавятся к этому аккаунту.")
        text = ask_text(self, "Добавить в историю",
                        f"Вставь ссылки на свои видео (каждую с новой строки). Они добавятся к аккаунту "
                        f"«{accs[0]['label']}» группы «{next(g['name'] for g in self.groups if g['id'] == gid)}».")
        ids = parse_video_ids(text or "")
        if not ids:
            return
        cfg, acc = self.cfg, accs[0]

        def work():
            from narez.publishers.youtube import ScopeError
            try:
                n = import_videos(DB(cfg["paths"]["db"]), cfg, get_publisher("youtube", cfg), acc["id"], gid, ids,
                                  self._print)
                self.q.put(("hinfo", f"Добавлено видео: {n}."))
            except ScopeError as e:
                self.q.put(("hinfo", str(e)))
            except Exception as e:
                self.q.put(("hinfo", f"Ошибка: {e}"))
            self.q.put(("history", None))
        threading.Thread(target=work, daemon=True).start()

    def _refresh_stats(self):
        cfg = self.cfg

        def work():
            from narez.publishers.youtube import ScopeError
            db = DB(cfg["paths"]["db"])
            by_acc: dict[int, list] = {}
            for p in db.publications("youtube"):
                by_acc.setdefault(p["account_id"], []).append(p)
            if not by_acc:
                self.q.put(("hinfo", "Пока нет опубликованных видео."))
                return
            self.q.put(("hinfo", "Обновляю…"))
            try:
                for acc, items in by_acc.items():
                    stats = get_publisher("youtube", cfg).fetch_stats(acc, [p["video_id"] for p in items])
                    for p in items:
                        s = stats[p["video_id"]]
                        db.update_stats(p["id"], s["views"], s["likes"], s["comments"], s["privacy"],
                                        s["publish_at"] or None)
                self.q.put(("hinfo", f"Статистика обновлена {dt.datetime.now():%H:%M}."))
            except ScopeError as e:
                self.q.put(("hinfo", str(e)))
            except Exception as e:
                self.q.put(("hinfo", f"Ошибка: {e}"))
            self.q.put(("history", None))
        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
