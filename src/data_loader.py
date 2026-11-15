"""
Загрузка данных OWID (требование 11: работа с большим файлом с учётом памяти).

Что делает модуль:
1. скачивает CSV-файл (если его ещё нет);
2. читает большой CSV по частям (чанками), а не целиком;
3. сразу уменьшает память: float64 -> float32, текст -> category, дата -> datetime;
4. сохраняет данные в Parquet и сравнивает CSV и Parquet
   по размеру файла, времени чтения и памяти (требование 9).
"""
import urllib.request
from pathlib import Path

import pandas as pd
import pyarrow  # noqa: F401  (импортируем заранее, чтобы время импорта не попало в замеры чтения)

import config
from src.utils import Timer, file_size_mb, log, memory_mb

# В новой версии файла (каталог OWID) столбцы называются country/code,
# в старой версии из GitHub — location/iso_code. Приводим к одному виду.
RENAME_COLUMNS = {"country": "location", "code": "iso_code"}

# Текстовые столбцы с небольшим числом уникальных значений -> тип category
CATEGORY_COLUMNS = ["iso_code", "location", "continent"]

# Столбцы с очень большими числами (до 10 млрд): оставляем float64,
# потому что float32 хранит только ~7 значащих цифр и потерял бы точность
KEEP_FLOAT64 = [
    "population", "total_cases", "total_deaths",
    "total_vaccinations", "people_vaccinated", "people_fully_vaccinated",
    "total_boosters", "new_vaccinations", "total_tests", "new_tests",
    "excess_mortality_cumulative_absolute",
]


def download_dataset(url=config.DATA_URL, path=config.RAW_CSV):
    """Скачивает файл данных, если его ещё нет. Возвращает путь к файлу."""
    path = Path(path)
    if path.exists():
        log(f"Файл уже скачан: {path.name} ({file_size_mb(path)} МБ)")
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"Скачиваю {url} ...")
    try:
        urllib.request.urlretrieve(url, path)
    except Exception as error:  # нет интернета, неверная ссылка и т. п.
        if path.exists():
            path.unlink()  # удаляем недокачанный файл
        raise ConnectionError(f"Не удалось скачать данные: {error}") from error

    log(f"Скачано: {path.name} ({file_size_mb(path)} МБ)")
    return path


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
    with Timer() as timer:
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

    stats = {
        "файл": path.name,
        "размер_файла_мб": file_size_mb(path),
        "строк": int(len(df)),
        "столбцов": int(df.shape[1]),
        "чанков": len(chunks),
        "размер_чанка": chunk_size,
        "время_чтения_с": timer.seconds,
        "память_до_оптимизации_мб": round(memory_before, 1),
        "память_после_оптимизации_мб": memory_mb(df),
    }
    stats["экономия_памяти_%"] = round(
        100 * (1 - stats["память_после_оптимизации_мб"] / stats["память_до_оптимизации_мб"]), 1
    )
    log(f"Загружено {stats['строк']:,} строк за {stats['время_чтения_с']} с; "
        f"память {stats['память_до_оптимизации_мб']} -> {stats['память_после_оптимизации_мб']} МБ")
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


def compare_csv_parquet(csv_path=config.RAW_CSV, parquet_path=config.RAW_PARQUET):
    """
    Сравнивает два формата хранения одних и тех же данных (требование 9).

    Критерии: размер файла на диске, время полного чтения, память DataFrame.
    Каждый формат читается два раза, берётся лучшее время — так первое «холодное»
    чтение (кэш диска, инициализация библиотек) не искажает сравнение.
    Возвращает список словарей — по одной строке на формат.
    """
    readers = {
        "CSV (float64, строки)": (csv_path, lambda: pd.read_csv(csv_path, low_memory=False)),
        "Parquet (float32, category)": (parquet_path, lambda: pd.read_parquet(parquet_path, engine="pyarrow")),
    }
    rows = []
    for name, (path, read) in readers.items():
        times = []
        for _ in range(2):
            with Timer() as timer:
                df = read()
            times.append(timer.seconds)
        rows.append({
            "формат": name,
            "размер_файла_мб": file_size_mb(path),
            "время_чтения_с": min(times),
            "память_мб": memory_mb(df),
        })
        del df
        log(f"  {name}: {rows[-1]['размер_файла_мб']} МБ на диске, "
            f"{rows[-1]['время_чтения_с']} с чтения, {rows[-1]['память_мб']} МБ в памяти")
    return rows
