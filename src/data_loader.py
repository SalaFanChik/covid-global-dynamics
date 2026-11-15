"""
Загрузка данных OWID (требование 11: работа с большим файлом с учётом памяти).

Что делает модуль:
1. скачивает CSV-файл (во временный файл, с таймаутом и проверкой размера);
2. читает большой CSV по частям (чанками), приводя типы каждого куска;
3. уменьшает память: float64 -> float32, текст -> category, дата -> datetime;
4. сохраняет данные в Parquet и честно сравнивает четыре способа чтения
   по времени, итоговой и пиковой памяти (требование 9).
"""
import hashlib
import shutil
import urllib.request
from pathlib import Path

import pandas as pd

try:
    import pyarrow  # noqa: F401  (импортируем заранее, чтобы время импорта не попало в замеры чтения)
except ImportError as error:  # библиотеки нет — подсказываем, что делать
    raise ImportError("Нет библиотеки pyarrow: выполните pip install -r requirements.txt") from error

import config
from src.utils import MemoryTimer, Timer, file_size_mb, log, memory_mb

# В новой версии файла (каталог OWID) столбцы называются country/code,
# в старой версии из GitHub — location/iso_code. Приводим к именам кодбука.
RENAME_COLUMNS = {"country": "location", "code": "iso_code"}

# Текстовые столбцы с небольшим числом уникальных значений -> тип category
CATEGORY_COLUMNS = ["iso_code", "location", "continent"]

# Столбцы с очень большими числами (до 10 млрд) и счётчики, которые мы суммируем,
# оставляем float64: float32 хранит только ~7 значащих цифр и потерял бы точность
KEEP_FLOAT64 = [
    "population", "new_cases", "new_deaths", "total_cases", "total_deaths",
    "total_vaccinations", "people_vaccinated", "people_fully_vaccinated",
    "total_boosters", "new_vaccinations", "total_tests", "new_tests",
    "excess_mortality_cumulative_absolute",
]

# Столбцы, без которых проект не работает (проверка набора столбцов при загрузке)
REQUIRED_COLUMNS = ["location", "iso_code", "continent", "date", "population", "new_cases", "new_deaths"]


def download_dataset(url=config.DATA_URL, path=config.RAW_CSV, timeout=60):
    """
    Скачивает файл данных, если его ещё нет. Возвращает путь к файлу.

    Скачивание идёт во временный файл .part и только после проверки размера
    файл переименовывается — недокачанный файл не будет принят за полный.
    """
    path = Path(path)
    if path.exists():
        log(f"Файл уже скачан: {path.name} ({file_size_mb(path)} МБ)")
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".csv.part")
    log(f"Скачиваю {url} ...")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, open(temporary, "wb") as file:
            expected = int(response.headers.get("Content-Length", 0))
            shutil.copyfileobj(response, file, length=1024 * 1024)      # копируем по 1 МБ
        actual = temporary.stat().st_size
        if expected and actual != expected:
            raise ConnectionError(f"скачано {actual} байт из {expected}")
        temporary.rename(path)
    except Exception as error:  # нет интернета, таймаут, неверная ссылка, обрыв связи
        if temporary.exists():
            temporary.unlink()
        raise ConnectionError(f"Не удалось скачать данные: {error}") from error

    log(f"Скачано: {path.name} ({file_size_mb(path)} МБ)")
    return path


def file_md5(path, block_size=1024 * 1024):
    """Контрольная сумма файла — чтобы в отчёте было видно, на каком именно срезе данных он сделан."""
    digest = hashlib.md5()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def optimize_types(chunk):
    """Уменьшает память одного куска данных, подбирая типы столбцов под содержимое."""
    chunk = chunk.rename(columns=RENAME_COLUMNS)

    # 1) дата как настоящий тип даты (а не строка)
    chunk["date"] = pd.to_datetime(chunk["date"], format="%Y-%m-%d")

    # 2) текстовые столбцы -> category
    for column in CATEGORY_COLUMNS:
        if column in chunk.columns:
            chunk[column] = chunk[column].astype("category")

    # 3) числовые столбцы -> float32, кроме списка KEEP_FLOAT64
    for column in chunk.columns:
        if chunk[column].dtype == "float64" and column not in KEEP_FLOAT64:
            chunk[column] = chunk[column].astype("float32")

    return chunk


def check_columns(df):
    """Проверяет, что все нужные столбцы на месте (иначе понятная ошибка)."""
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"В файле данных нет столбцов {missing} — это не набор OWID COVID-19?")


def read_csv_in_chunks(path=config.RAW_CSV, chunk_size=config.CHUNK_SIZE):
    """
    Читает CSV частями по chunk_size строк и собирает один оптимизированный DataFrame.

    Возвращает кортеж (DataFrame, словарь со статистикой загрузки).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Нет файла данных {path}. Сначала выполните пункт меню «Скачать данные»."
        )

    chunks = []             # список обработанных кусков
    memory_before = 0.0     # память «как есть» (сумма по кускам) — для сравнения до/после
    with MemoryTimer() as timer:
        reader = pd.read_csv(path, chunksize=chunk_size, low_memory=False)
        for number, chunk in enumerate(reader, start=1):
            memory_before += memory_mb(chunk)
            chunks.append(optimize_types(chunk))
            log(f"  чанк {number}: {len(chunk):,} строк")
        df = pd.concat(chunks, ignore_index=True)
        # у каждого куска были свои наборы категорий, поэтому после concat
        # столбцы снова стали текстовыми — переводим их в category ещё раз
        for column in CATEGORY_COLUMNS:
            if column in df.columns:
                df[column] = df[column].astype("category")
    check_columns(df)

    stats = {
        "файл": path.name,
        "md5": file_md5(path),
        "размер_файла_мб": file_size_mb(path),
        "строк": int(len(df)),
        "столбцов": int(df.shape[1]),
        "чанков": len(chunks),
        "размер_чанка": chunk_size,
        "время_чтения_с": timer.seconds,
        "пиковая_память_мб": timer.peak_mb,
        "память_до_оптимизации_мб": round(memory_before, 1),
        "память_после_оптимизации_мб": memory_mb(df),
    }
    stats["экономия_памяти_%"] = round(
        100 * (1 - stats["память_после_оптимизации_мб"] / stats["память_до_оптимизации_мб"]), 1
    )
    log(f"Загружено {stats['строк']:,} строк за {stats['время_чтения_с']} с; "
        f"память {stats['память_до_оптимизации_мб']} -> {stats['память_после_оптимизации_мб']} МБ, "
        f"пик {stats['пиковая_память_мб']} МБ")
    return df, stats


def save_parquet(df, path):
    """Сохраняет DataFrame в Parquet (сжатие snappy) и возвращает путь."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    log(f"Сохранено: {path.name} ({file_size_mb(path)} МБ)")
    return path


def load_parquet(path=config.CLEAN_PARQUET):
    """Читает Parquet-файл. Если его нет — понятная ошибка с подсказкой."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Нет файла {path}. Сначала выполните пункт меню «Загрузить и подготовить данные»."
        )
    return pd.read_parquet(path, engine="pyarrow")


def read_csv_typed(path):
    """Чтение CSV целиком, но сразу с оптимизацией типов (без чанков) — для сравнения способов."""
    return optimize_types(pd.read_csv(path, low_memory=False))


def read_csv_chunked(path):
    """Чтение CSV чанками с оптимизацией типов (как в основном пайплайне, без логов)."""
    chunks = [optimize_types(chunk) for chunk in pd.read_csv(path, chunksize=config.CHUNK_SIZE, low_memory=False)]
    df = pd.concat(chunks, ignore_index=True)
    for column in CATEGORY_COLUMNS:
        df[column] = df[column].astype("category")
    return df


def stream_country_totals(path):
    """
    Потоковая обработка: читаем CSV чанками и храним только итоги по странам (случаи и смерти),
    а не все 630 тысяч строк. Так работают с файлами, которые не помещаются в память.
    """
    totals = {}                                   # словарь: страна -> [случаи, смерти]
    for chunk in pd.read_csv(path, chunksize=config.CHUNK_SIZE, usecols=["country", "new_cases", "new_deaths"]):
        sums = chunk.groupby("country")[["new_cases", "new_deaths"]].sum()
        for country, row in sums.iterrows():
            current = totals.setdefault(country, [0.0, 0.0])
            current[0] += float(row["new_cases"])
            current[1] += float(row["new_deaths"])
    return pd.DataFrame.from_dict(totals, orient="index", columns=["new_cases", "new_deaths"])


def compare_read_methods(csv_path=config.RAW_CSV, parquet_path=config.RAW_PARQUET):
    """
    Сравнивает пять способов работы с одними и теми же данными (требование 9):
    CSV «как есть» (float64, строки), CSV с подбором типов, CSV чанками с подбором типов,
    CSV чанками с потоковой агрегацией (без хранения всех строк) и Parquet.

    Критерии: размер файла, время (лучшее из 2 запусков), итоговая память DataFrame
    и пиковая память Python-объектов во время чтения (tracemalloc).
    Для Parquet пик не измеряется: память выделяет библиотека Arrow, которую tracemalloc не видит.
    """
    methods = {
        "CSV как есть (float64, строки)": (csv_path, lambda: pd.read_csv(csv_path, low_memory=False), ""),
        "CSV + оптимизация типов": (csv_path, lambda: read_csv_typed(csv_path), ""),
        "CSV чанками + оптимизация типов": (csv_path, lambda: read_csv_chunked(csv_path),
                                           "все чанки остаются в памяти (нужны для анализа)"),
        "CSV чанками, потоковые итоги по странам": (csv_path, lambda: stream_country_totals(csv_path),
                                                   "в памяти только один чанк и таблица итогов"),
        "Parquet (float32, category)": (parquet_path, lambda: pd.read_parquet(parquet_path, engine="pyarrow"),
                                        "пик не измеряется tracemalloc (память выделяет Arrow)"),
    }
    rows = []
    for name, (path, read, note) in methods.items():
        seconds, peaks = [], []
        for _ in range(2):                       # два запуска: первый «прогревает» кэш диска
            with MemoryTimer() as timer:
                df = read()
            seconds.append(timer.seconds)
            peaks.append(timer.peak_mb)
        rows.append({
            "способ": name,
            "размер_файла_мб": file_size_mb(path),
            "время_с": min(seconds),
            "итоговая_память_мб": memory_mb(df),
            "пиковая_память_мб": None if name.startswith("Parquet") else max(peaks),
            "примечание": note,
        })
        del df
        log(f"  {name}: файл {rows[-1]['размер_файла_мб']} МБ, время {rows[-1]['время_с']} с, "
            f"память {rows[-1]['итоговая_память_мб']} МБ, пик {rows[-1]['пиковая_память_мб']} МБ")
    return rows
