"""Общая логика публикации — её используют и окно tkinter (app.py), и веб-сервер."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import banner as bn
from . import schedule
from .cleanup import finish_group
from .meta import api_tags, compose_description
from .publishers import get_publisher
from .publishers.base import NATIVE_SCHEDULE, REAL_PLATFORMS

SIGNUP_MSG = ("у этого Google-аккаунта нет канала YouTube (youtubeSignupRequired). Открой youtube.com под этим аккаунтом и создай канал "
              "(аватар → «Создать канал»), затем в разделе «Аккаунты» нажми «Войти» заново и на экране Google выбери именно этот канал. "
              "Клипы остались в очереди.")
TOKEN_MSG = ("доступ к аккаунту истёк или отозван (invalid_grant). Зайди в «Аккаунты» и войди в этот аккаунт заново — "
             "в Google Cloud в режиме «Тестирование» вход действует всего 7 дней.")
LIMIT_MSG = ("дневной лимит загрузок канала исчерпан. Остальные клипы остались в очереди — опубликуй их позже "
             "(лимит снимается примерно через сутки; подтверждение канала по телефону на youtube.com/verify поднимает его).")


def send_one(db, cfg: dict, a: dict, clip, when, log=print) -> str:
    """Заливает один клип в один аккаунт и записывает публикацию в историю. Возвращает ссылку."""
    real = a["platform"] in REAL_PLATFORMS
    pub = get_publisher(a["platform"], cfg)
    path = bn.ensure_final(db, clip, cfg, log) if real else Path(clip["path"])
    url = pub.publish(str(path), clip["title"], compose_description(clip["reason"], clip["tags"]),
                      {"account_id": a["id"]}, when, api_tags(clip["tags"]))
    if real:
        pid = db.add_publication(clip["id"], a["id"], db.clip_group(clip), a["platform"], url,
                                 clip["title"], clip["thumb"], schedule.to_iso(when) if when else "")
        if a["platform"] == "youtube":  # сразу известны статус и нулевая статистика; реальные цифры подтянет обновление статистики
            db.update_stats(pid, 0, 0, 0, "private" if when else cfg["youtube"].get("privacy", "private"),
                            schedule.to_iso(when) if when else None)
        else:  # статистики по ним пока нет — отметим, что видео уже выложено
            tt = cfg.get("tiktok", {})
            if a["platform"] == "tiktok" and str(tt.get("mode", "inbox")).lower() != "direct":
                state = "draft"  # лежит в черновиках TikTok: публикует сам пользователь
            elif a["platform"] == "tiktok" and tt.get("privacy", "PUBLIC_TO_EVERYONE") != "PUBLIC_TO_EVERYONE":
                state = "private"
            else:
                state = "public"
            db.update_stats(pid, None, None, None, state)
    return url


def run_due(db, cfg: dict, log=print) -> int:
    """Выкладывает клипы из очереди, время которых пришло (Instagram/TikTok). Возвращает число выложенных."""
    import datetime as _dt
    n = 0
    touched: set[int] = set()
    for row in db.deferred_due(schedule.to_iso(_dt.datetime.now(_dt.timezone.utc))):
        a, clip = db.account(row["account_id"]), db.clip(row["clip_id"])
        if not a or not clip or clip["status"] != "ready":
            db.set_deferred(row["id"], "cancel", "клип или аккаунт удалены")
            continue
        db.set_deferred(row["id"], "run")
        try:
            url = send_one(db, cfg, dict(a), clip, None, log)
            db.set_deferred(row["id"], "done")
            log(f"{a['platform']}: {url}")
            n += 1
            touched.add(db.clip_group(clip))
        except Exception as e:
            db.set_deferred(row["id"], "error", str(e))
            log(f"ОШИБКА {a['platform']}: {e}")
    for gid in touched:
        if gid:
            finish_group(db, cfg, gid, log)
    return n


def publish_clips(db, cfg: dict, group_id: int, clip_ids: list[int], log=print, delete_sources: bool = True,
                  account_ids: list[int] | None = None) -> dict:
    """Публикует клипы во все аккаунты группы. Уже загруженные на аккаунт клипы пропускаются (можно повторять после сбоя).
    Своё время клипа (publish_at) главнее общей задержки. Возвращает {'published': n, 'errors': n, 'limit': bool}."""
    if account_ids:   # выбранные аккаунты (любых групп) вместо всех аккаунтов группы клипа
        accs = [dict(a) for a in (db.account(i) for i in account_ids) if a]
    else:
        accs = [dict(a) for a in db.accounts(group_id)]
    delay = cfg["youtube"].get("delay_minutes", 0)
    now = dt.datetime.now(dt.timezone.utc)
    res = {"published": 0, "errors": 0, "limit": False}
    for a in accs:
        pub, real = get_publisher(a["platform"], cfg), a["platform"] in REAL_PLATFORMS
        todo = [i for i in clip_ids if not real or (a["id"] not in db.clip_published_accounts(i)
                                                    and not db.deferred_waiting(i, a["id"]))]
        k = 0
        for cid in todo:
            clip = db.clip(cid)  # свежие данные: учитываем правки из редактора
            when, k = schedule.pick_when(clip["publish_at"], now, delay, k)
            if real and when and a["platform"] not in NATIVE_SCHEDULE:
                # у Instagram/TikTok нет отложенной публикации: клип ждёт в очереди, narez выложит его сам
                db.add_deferred(cid, a["id"], schedule.to_iso(when))
                res["scheduled"] = res.get("scheduled", 0) + 1
                log(f"{a['platform']}: клип «{clip['title']}» выложу в {when.astimezone():%d.%m %H:%M} (программа должна быть запущена).")
                continue
            log(f"{a['platform']} · {a['label']}: клип {todo.index(cid) + 1} из {len(todo)} «{clip['title']}»"
                + (f" — на {when.astimezone():%d.%m %H:%M}" if when else " — сразу"))
            try:
                url = send_one(db, cfg, a, clip, when, log)
                res["published"] += 1
                log(f"{a['platform']}: {url}")
            except Exception as e:
                if "uploadLimitExceeded" in str(e):
                    log(f"{a['platform']}: {LIMIT_MSG}")
                    res["limit"] = True
                    break
                if "youtubeSignupRequired" in str(e) or "invalid_grant" in str(e):
                    # одна и та же причина для всех клипов этого аккаунта — не долбим API, остальные аккаунты продолжают
                    log(f"ОШИБКА {a['platform']} · {a['label']}: " + (SIGNUP_MSG if "youtubeSignupRequired" in str(e) else TOKEN_MSG))
                    res["errors"] += 1
                    break
                res["errors"] += 1
                log(f"ОШИБКА {a['platform']}: {e}")
    finish_group(db, cfg, group_id, log, delete_sources=delete_sources,
                 only=clip_ids if account_ids else None,
                 real_ids={a["id"] for a in accs if a["platform"] in REAL_PLATFORMS} if account_ids else None)
    res["left"] = sum(1 for i in clip_ids if (c := db.clip(i)) and c["status"] == "ready")
    log(f"Итог: загружено сразу — {res['published']}, ошибок — {res['errors']}, в очереди на выход позже — {res.get('scheduled', 0)}, "
        f"осталось в «Готово к публикации» — {res['left']}.")
    return res


def pub_status(p) -> str:
    """Статус видео в истории (по данным последнего обновления статистики и времени публикации)."""
    priv, at = p["privacy"] or "", schedule.from_iso(p["publish_at"] or "")
    if priv == "deleted":
        return "Удалено"
    if priv == "draft":
        return "В черновиках TikTok"
    if priv == "public":
        return "Опубликовано"
    if priv == "unlisted":
        return "По ссылке"
    if at and at > dt.datetime.now(dt.timezone.utc):
        return f"Запланировано на {at.astimezone():%d.%m %H:%M}"
    return "Закрытое" if priv == "private" else "Загружено"


def refresh_stats(db, cfg: dict, log=print) -> int:
    """Обновляет просмотры/лайки/комментарии и статус всех видео истории (videos.list: 1 ед. квоты на 50 видео).
    Возвращает число обновлённых видео. Если нужен повторный вход в YouTube — бросает ScopeError."""
    from .history import sync_channels
    sync_channels(db, cfg, get_publisher("youtube", cfg), log)   # сначала досыпаем в историю то, чего в ней нет, но что есть на канале
    by_acc: dict[int, list] = {}
    for p in db.publications("youtube"):
        by_acc.setdefault(p["account_id"], []).append(p)
    n = 0
    pub = get_publisher("youtube", cfg)
    for acc, items in by_acc.items():
        stats = pub.fetch_stats(acc, [p["video_id"] for p in items])
        for p in items:
            s = stats[p["video_id"]]
            db.update_stats(p["id"], s["views"], s["likes"], s["comments"], s["privacy"], s["publish_at"] or None)
            n += 1
    log(f"Статистика обновлена: {n} видео.")
    return n
