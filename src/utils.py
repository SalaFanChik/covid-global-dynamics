"""
Вспомогательные функции, которые используются во всех модулях:
вывод сообщений с временем, замер времени и памяти, чтение/запись JSON и CSV.
"""
import json
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

import config


def log(message):
    """Печатает сообщение с текущим временем, чтобы было видно ход выполнения."""
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


class Timer:
    """
    Простой секундомер для блока кода.

    Использование:
        with Timer() as timer:
            ...  # измеряемый код
        print(timer.seconds)
    """

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.seconds = round(time.perf_counter() - self.start, 3)


class MemoryTimer(Timer):
    """
    Секундомер + пиковая память Python-объектов (tracemalloc из стандартной библиотеки).

    Использование:
        with MemoryTimer() as m:
            df = pd.read_csv(...)
        print(m.seconds, m.peak_mb)
    """

    def __enter__(self):
        tracemalloc.start()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        super().__exit__(exc_type, exc_value, traceback)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.peak_mb = round(peak / 1_000_000, 1)


# Везде в проекте мегабайт = 10^6 байт (как в проводнике и в документации OWID)
def memory_mb(df):
    """Сколько мегабайт занимает DataFrame в памяти (с учётом строк)."""
    return round(float(df.memory_usage(deep=True).sum()) / 1_000_000, 1)


def file_size_mb(path):
    """Размер файла на диске в мегабайтах."""
    return round(Path(path).stat().st_size / 1_000_000, 1)


def ensure_dirs():
    """Создаёт все рабочие папки проекта, если их ещё нет."""
    for folder in [config.RAW_DIR, config.PROCESSED_DIR, config.FIGURES_DIR, config.TABLES_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def save_json(data, path):
    """Сохраняет словарь или список в JSON-файл (кириллица без экранирования)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, default=str)
    return path


def load_json(path):
    """Читает JSON-файл в словарь. Если файла нет — возвращает пустой словарь."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def save_table(df, name):
    """Сохраняет таблицу результатов в outputs/tables/<name>.csv и возвращает путь."""
    config.TABLES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.TABLES_DIR / f"{name}.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def update_results(section, data):
    """
    Дописывает блок результатов в общий файл outputs/results.json.
    Каждый пункт меню сохраняет свои метрики под своим именем раздела.
    """
    results = load_json(config.RESULTS_JSON)
    results[section] = data
    results["обновлено"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(results, config.RESULTS_JSON)
