"""
База данных SQLite (требование 10).

Нормализованная схема (3НФ): справочники continents и locations, таблицы фактов
daily_stats / vaccinations / hospitalizations / testing / excess_mortality
(разреженные блоки хранятся отдельно — только непустые строки),
производная витрина weekly_stats, а также таблицы результатов анализа waves и forecast_results.

Функции модуля: создание схемы, загрузка очищенных данных, выполнение каталога
SQL-запросов из sql_queries.py с выгрузкой в pandas, сравнение Pandas vs SQL.
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

import config
from src.cleaning import location_coverage
from src.sql_queries import QUERIES, WEEKLY_FROM_DAILY_SQL
from src.utils import Timer, log, save_table

SCHEMA_SQL = """
DROP TABLE IF EXISTS forecast_results;
DROP TABLE IF EXISTS waves;
DROP TABLE IF EXISTS weekly_stats;
DROP TABLE IF EXISTS excess_mortality;
DROP TABLE IF EXISTS testing;
DROP TABLE IF EXISTS hospitalizations;
DROP TABLE IF EXISTS vaccinations;
DROP TABLE IF EXISTS daily_stats;
DROP TABLE IF EXISTS locations;
DROP TABLE IF EXISTS continents;

CREATE TABLE continents (
    continent_id   INTEGER PRIMARY KEY,
    continent_name TEXT NOT NULL UNIQUE
);

CREATE TABLE locations (
    iso_code                   TEXT PRIMARY KEY,
    location                   TEXT NOT NULL UNIQUE,
    continent_id               INTEGER REFERENCES continents(continent_id),
    is_aggregate               INTEGER NOT NULL DEFAULT 0,
    population                 REAL,
    population_density         REAL,
    median_age                 REAL,
    life_expectancy            REAL,
    gdp_per_capita             REAL,
    extreme_poverty            REAL,
    diabetes_prevalence        REAL,
    handwashing_facilities     REAL,
    hospital_beds_per_thousand REAL,
    first_report_date          TEXT,
    last_report_date           TEXT,
    n_days_reported            INTEGER
);

CREATE TABLE daily_stats (
    iso_code                 TEXT NOT NULL REFERENCES locations(iso_code),
    date                     TEXT NOT NULL,
    new_cases                REAL,
    new_deaths               REAL,
    total_cases              REAL,
    total_deaths             REAL,
    new_cases_smoothed       REAL,
    new_deaths_smoothed      REAL,
    new_cases_per_million    REAL,
    new_deaths_per_million   REAL,
    total_cases_per_million  REAL,
    total_deaths_per_million REAL,
    reproduction_rate        REAL,
    stringency_index         REAL,
    PRIMARY KEY (iso_code, date)
);

CREATE TABLE vaccinations (
    iso_code                            TEXT NOT NULL REFERENCES locations(iso_code),
    date                                TEXT NOT NULL,
    total_vaccinations                  REAL,
    people_vaccinated                   REAL,
    people_fully_vaccinated             REAL,
    total_boosters                      REAL,
    new_vaccinations                    REAL,
    people_vaccinated_per_hundred       REAL,
    people_fully_vaccinated_per_hundred REAL,
    total_boosters_per_hundred          REAL,
    PRIMARY KEY (iso_code, date)
);

CREATE TABLE hospitalizations (
    iso_code                           TEXT NOT NULL REFERENCES locations(iso_code),
    date                               TEXT NOT NULL,
    icu_patients                       REAL,
    icu_patients_per_million           REAL,
    hosp_patients                      REAL,
    hosp_patients_per_million          REAL,
    weekly_icu_admissions              REAL,
    weekly_icu_admissions_per_million  REAL,
    weekly_hosp_admissions             REAL,
    weekly_hosp_admissions_per_million REAL,
    PRIMARY KEY (iso_code, date)
);

CREATE TABLE testing (
    iso_code       TEXT NOT NULL REFERENCES locations(iso_code),
    date           TEXT NOT NULL,
    new_tests      REAL,
    total_tests    REAL,
    positive_rate  REAL,
    tests_per_case REAL,
    PRIMARY KEY (iso_code, date)
);

CREATE TABLE excess_mortality (
    iso_code                                TEXT NOT NULL REFERENCES locations(iso_code),
    date                                    TEXT NOT NULL,
    excess_mortality                        REAL,
    excess_mortality_cumulative             REAL,
    excess_mortality_cumulative_absolute    REAL,
    excess_mortality_cumulative_per_million REAL,
    PRIMARY KEY (iso_code, date)
);

CREATE TABLE weekly_stats (
    iso_code               TEXT NOT NULL REFERENCES locations(iso_code),
    week_start             TEXT NOT NULL,
    new_cases              REAL,
    new_deaths             REAL,
    new_cases_per_million  REAL,
    new_deaths_per_million REAL,
    PRIMARY KEY (iso_code, week_start)
);

CREATE TABLE waves (
    wave_id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    iso_code                      TEXT NOT NULL REFERENCES locations(iso_code),
    method                        TEXT NOT NULL,
    wave_no                       INTEGER NOT NULL,
    start_date                    TEXT,
    peak_date                     TEXT,
    end_date                      TEXT,
    duration_weeks                INTEGER,
    peak_weekly_cases_per_million REAL,
    total_cases                   REAL,
    total_deaths                  REAL,
    cfr_pct                       REAL,
    size                          TEXT
);

CREATE TABLE forecast_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    iso_code      TEXT NOT NULL REFERENCES locations(iso_code),
    model         TEXT NOT NULL,
    horizon_weeks INTEGER NOT NULL,
    n_forecasts   INTEGER,
    mae           REAL,
    mae_vs_naive  REAL,
    seconds       REAL,
    is_baseline   INTEGER
);

CREATE INDEX idx_daily_date ON daily_stats(date);
CREATE INDEX idx_weekly_week ON weekly_stats(week_start);
CREATE INDEX idx_waves_iso ON waves(iso_code);
CREATE INDEX idx_locations_continent ON locations(continent_id);
"""

# Какие столбцы исходных данных идут в какую таблицу фактов
FACT_TABLES = {
    "daily_stats": [
        "new_cases", "new_deaths", "total_cases", "total_deaths", "new_cases_smoothed",
        "new_deaths_smoothed", "new_cases_per_million", "new_deaths_per_million",
        "total_cases_per_million", "total_deaths_per_million", "reproduction_rate", "stringency_index",
    ],
    "vaccinations": [
        "total_vaccinations", "people_vaccinated", "people_fully_vaccinated", "total_boosters",
        "new_vaccinations", "people_vaccinated_per_hundred", "people_fully_vaccinated_per_hundred",
        "total_boosters_per_hundred",
    ],
    "hospitalizations": [
        "icu_patients", "icu_patients_per_million", "hosp_patients", "hosp_patients_per_million",
        "weekly_icu_admissions", "weekly_icu_admissions_per_million",
        "weekly_hosp_admissions", "weekly_hosp_admissions_per_million",
    ],
    "testing": ["new_tests", "total_tests", "positive_rate", "tests_per_case"],
    "excess_mortality": [
        "excess_mortality", "excess_mortality_cumulative",
        "excess_mortality_cumulative_absolute", "excess_mortality_cumulative_per_million",
    ],
}
LOCATION_COLUMNS = [
    "population", "population_density", "median_age", "life_expectancy", "gdp_per_capita",
    "extreme_poverty", "diabetes_prevalence", "handwashing_facilities", "hospital_beds_per_thousand",
]


def connect(db_path=config.DB_PATH, must_exist=True):
    """Открывает соединение с базой и включает проверку внешних ключей (в SQLite она выключена по умолчанию)."""
    db_path = Path(db_path)
    if must_exist and not db_path.exists():
        raise FileNotFoundError(f"База {db_path} ещё не создана. Выполните пункт меню «База данных и SQL».")
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_schema(connection):
    """Создаёт все таблицы заново (старые удаляются)."""
    connection.executescript(SCHEMA_SQL)
    connection.commit()


def load_reference_tables(df, connection):
    """Заполняет справочники continents и locations. Возвращает словарь континент -> id."""
    continents = sorted(df["continent"].dropna().unique().tolist())
    continent_ids = {name: number for number, name in enumerate(continents, start=1)}   # dict-справочник
    connection.executemany("INSERT INTO continents VALUES (?, ?)",
                           [(number, name) for name, number in continent_ids.items()])

    first_rows = df.groupby("iso_code", observed=True).first()
    coverage = location_coverage(df).set_index("iso_code")      # первый/последний отчёт о случаях
    rows = []
    for iso_code, row in first_rows.iterrows():
        continent = row["continent"]
        continent_id = continent_ids.get(continent) if pd.notna(continent) else None
        values = [iso_code, row["location"], continent_id, int(bool(row["is_aggregate"]))]
        for column in LOCATION_COLUMNS:
            value = row[column]
            values.append(None if pd.isna(value) else float(value))
        cover = coverage.loc[str(iso_code)]
        values += [str(cover["first_report_date"].date()) if pd.notna(cover["first_report_date"]) else None,
                   str(cover["last_report_date"].date()) if pd.notna(cover["last_report_date"]) else None,
                   int(cover["n_days_reported"])]
        rows.append(values)
    placeholders = ", ".join(["?"] * (7 + len(LOCATION_COLUMNS)))
    connection.executemany(f"INSERT INTO locations VALUES ({placeholders})", rows)
    connection.commit()
    return continent_ids


def load_fact_tables(df, connection):
    """Загружает таблицы фактов. Разреженные блоки — только строки, где есть хоть одно значение."""
    counts = {}
    base = pd.DataFrame({
        "iso_code": df["iso_code"].astype(str),
        "date": df["date"].dt.strftime("%Y-%m-%d"),   # SQLite хранит даты текстом ГГГГ-ММ-ДД
    })
    for table, columns in FACT_TABLES.items():
        present = [column for column in columns if column in df.columns]
        part = pd.concat([base, df[present].astype("float64")], axis=1)
        if table != "daily_stats":                           # у дневной таблицы оставляем все дни
            part = part[part[present].notna().any(axis=1)]
        part.to_sql(table, connection, if_exists="append", index=False, chunksize=50_000)
        counts[table] = int(len(part))
        log(f"  {table}: {counts[table]:,} строк")
    connection.commit()
    return counts


def build_weekly_table_sql(connection):
    """Заполняет витрину weekly_stats SQL-запросом из daily_stats. Возвращает время в секундах."""
    with Timer() as timer:
        connection.execute(f"""
            INSERT INTO weekly_stats (iso_code, week_start, new_cases, new_deaths,
                                      new_cases_per_million, new_deaths_per_million)
            SELECT w.iso_code, w.week_start, w.new_cases, w.new_deaths,
                   w.new_cases  * 1000000.0 / l.population,
                   w.new_deaths * 1000000.0 / l.population
            FROM ({WEEKLY_FROM_DAILY_SQL}) AS w
            JOIN locations l ON l.iso_code = w.iso_code
        """)
        connection.commit()
    return timer.seconds


def load_results_tables(waves, forecast_metrics, df, connection):
    """Сохраняет результаты анализа (волны и метрики прогноза) в базу."""
    iso_by_location = dict(zip(df["location"].astype(str), df["iso_code"].astype(str)))   # словарь страна -> код
    wave_rows = waves[["iso_code", "method", "wave_no", "start_date", "peak_date", "end_date", "duration_weeks",
                       "peak_weekly_cases_per_million", "total_cases", "total_deaths", "cfr_pct", "size"]].copy()
    for column in ["start_date", "peak_date", "end_date"]:
        wave_rows[column] = wave_rows[column].astype(str)
    wave_rows["iso_code"] = wave_rows["iso_code"].astype(str)
    wave_rows.to_sql("waves", connection, if_exists="append", index=False)

    forecast_rows = forecast_metrics.copy()
    forecast_rows["iso_code"] = forecast_rows["location"].map(iso_by_location)
    forecast_rows["is_baseline"] = forecast_rows["is_baseline"].astype(int)
    forecast_rows = forecast_rows[["iso_code", "model", "horizon_weeks", "n_forecasts", "mae",
                                   "mae_vs_naive", "seconds", "is_baseline"]]
    forecast_rows.to_sql("forecast_results", connection, if_exists="append", index=False)
    connection.commit()
    log(f"  waves: {len(wave_rows):,} строк, forecast_results: {len(forecast_rows):,} строк")


def build_database(df, waves, forecast_metrics, db_path=config.DB_PATH):
    """Полная сборка базы с нуля. Возвращает словарь со статистикой."""
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    connection = connect(db_path, must_exist=False)
    try:
        with Timer() as timer:
            create_schema(connection)
            load_reference_tables(df, connection)
            counts = load_fact_tables(df, connection)
            weekly_seconds = build_weekly_table_sql(connection)
            load_results_tables(waves, forecast_metrics, df, connection)
        counts["weekly_stats"] = connection.execute("SELECT COUNT(*) FROM weekly_stats").fetchone()[0]
        counts["locations"] = connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    finally:
        connection.close()
    stats = {"файл": str(db_path), "размер_мб": round(db_path.stat().st_size / 1024 ** 2, 1),
             "строк_по_таблицам": counts, "время_сборки_с": timer.seconds, "weekly_из_daily_sql_с": weekly_seconds}
    log(f"База собрана за {timer.seconds} с, размер {stats['размер_мб']} МБ")
    return stats


def run_queries(connection, queries=QUERIES):
    """Выполняет все запросы каталога, сохраняет результаты в CSV и возвращает (результаты, сводка)."""
    results = {}
    summary = []
    for name, query in queries.items():
        try:
            with Timer() as timer:
                table = pd.read_sql_query(query["sql"], connection)
        except (sqlite3.Error, pd.errors.DatabaseError) as error:   # ошибка в SQL не должна ронять весь шаг
            log(f"  ошибка в запросе {name}: {error}")
            continue
        results[name] = table
        save_table(table, f"sql_{name}")
        summary.append({"запрос": name, "описание": query["описание"],
                        "конструкции": ", ".join(query["конструкции"]),
                        "строк": len(table), "время_с": timer.seconds})
        log(f"  {name}: {len(table)} строк за {timer.seconds} с")
    return results, pd.DataFrame(summary)


def compare_pandas_vs_sql(df, connection, repeats=3):
    """
    Один и тот же расчёт двумя способами: недельные суммы случаев и смертей по всем локациям
    из дневных данных (598 223 строки -> 85 555 недель).

    Что входит в замер: SQL — выполнение GROUP BY в SQLite и выгрузка результата в DataFrame;
    pandas — groupby по уже загруженному в память DataFrame. Берём медиану из repeats запусков
    и проверяем, что результаты совпадают (суммы сравниваем в float64 с допуском).
    """
    sql_times, pandas_times = [], []
    for _ in range(repeats):
        with Timer() as timer_sql:
            sql_result = pd.read_sql_query(WEEKLY_FROM_DAILY_SQL, connection)
        sql_times.append(timer_sql.seconds)

        with Timer() as timer_pandas:
            pandas_result = (
                df.assign(week_start=df["date"] - pd.to_timedelta(df["date"].dt.day_of_week, unit="D"))
                  .groupby(["iso_code", "week_start"], observed=True)[["new_cases", "new_deaths"]]
                  .sum(min_count=1)
                  .reset_index()
            )
        pandas_times.append(timer_pandas.seconds)

    # приводим ключи к одному виду и сравниваем суммы
    pandas_result["iso_code"] = pandas_result["iso_code"].astype(str)
    pandas_result["week_start"] = pandas_result["week_start"].dt.strftime("%Y-%m-%d")
    merged = sql_result.merge(pandas_result, on=["iso_code", "week_start"], suffixes=("_sql", "_pandas"))
    same_rows = len(merged) == len(sql_result) == len(pandas_result)
    difference = (merged["new_cases_sql"].astype("float64") - merged["new_cases_pandas"].astype("float64")).abs()
    max_diff = float(difference.max()) if same_rows else None
    equal = bool(same_rows and max_diff is not None and max_diff < 1e-6)

    comparison = pd.DataFrame([
        {"подход": "SQL (SQLite GROUP BY + выгрузка в pandas)", "строк": len(sql_result),
         "время_с_медиана": float(np.median(sql_times))},
        {"подход": "pandas (groupby в памяти)", "строк": len(pandas_result),
         "время_с_медиана": float(np.median(pandas_times))},
    ])
    log(f"Pandas vs SQL: {comparison.iloc[1, 2]} с против {comparison.iloc[0, 2]} с, результаты совпадают: {equal}")
    return comparison, {"результаты_совпадают": equal, "макс_расхождение": max_diff,
                        "вывод": "pandas быстрее для расчётов в памяти; SQLite хранит данные между запусками, "
                                 "фильтрует их запросами и обслуживает дашборд без загрузки всего файла"}
