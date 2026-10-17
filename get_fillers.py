"""Скачивает бесплатные видео-вставки (Mixkit, лицензия «Mixkit Free License»: можно использовать в коммерческих проектах без указания автора)
в папку assets/fillers. Запуск из папки narez:  python get_fillers.py
Работает на любом Python 3 без сторонних библиотек. Уже скачанные файлы пропускаются. Свои ролики можно положить в ту же папку
или загрузить во вкладке «Вставки»."""
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parent / "assets" / "fillers"
# (id на mixkit.co, короткое имя). Страница ролика: https://mixkit.co/free-stock-video/<…>-<id>/
VIDEOS = [
    (45041, "steam-cleaning-furniture"), (45039, "vacuum-sofa"), (48757, "handheld-vacuum-couch"), (45040, "steam-cleaner-closeup"),
    (48835, "vacuum-carpet-bedroom"), (49064, "car-interior-vacuum"), (49063, "car-trunk-vacuum"), (48836, "vacuum-carpet-toys"),
    (10340, "handheld-vacuum-carpet-pov"), (36522, "car-carpet-vacuum"), (45674, "ink-exploding"),
    (52208, "cereal-stream"), (33490, "sand-hands"), (28889, "hourglass"),
]
RES = ("1080", "720", "360")   # пробуем от лучшего качества к худшему


def fetch(vid: int, name: str) -> str:
    out = DEST / f"mixkit-{vid}-{name}.mp4"
    if out.exists() and out.stat().st_size > 50_000:
        return "уже есть"
    for res in RES:
        url = f"https://assets.mixkit.co/videos/{vid}/{vid}-{res}.mp4"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(out.with_suffix(".part"), "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            out.with_suffix(".part").replace(out)
            return f"скачано ({res}p, {out.stat().st_size // 1024 // 1024} МБ)"
        except (urllib.error.URLError, TimeoutError, OSError):
            out.with_suffix(".part").unlink(missing_ok=True)
    return "НЕ УДАЛОСЬ"


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    bad = 0
    for vid, name in VIDEOS:
        res = fetch(vid, name)
        bad += res == "НЕ УДАЛОСЬ"
        print(f"{vid} {name}: {res}", flush=True)
    print(f"\nГотово. Папка: {DEST}. Не скачалось: {bad}.")
    if bad:
        print("Если не скачивается — открой страницу ролика на mixkit.co, скачай вручную и положи в эту папку или загрузи во вкладке «Вставки».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
