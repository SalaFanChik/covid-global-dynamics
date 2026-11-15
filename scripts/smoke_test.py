"""
Быстрая проверка проекта после полного прогона (python main.py --all):
все ли файлы результатов на месте, целы ли база и её внешние ключи, не пересекаются ли волны.

Запуск из корня проекта:  python scripts/smoke_test.py
Без pytest — обычные assert, чтобы проверку мог прочитать и запустить любой участник команды.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

EXPECTED_TABLES = ["format_comparison", "data_audit", "cleaning_log", "country_summary", "waves_peaks",
                   "waves_threshold", "wave_methods_comparison", "wave_sensitivity", "forecast_metrics",
                   "forecast_summary", "sql_queries_summary", "pandas_vs_sql", "correlation_matrix"]
EXPECTED_FIGURES = ["waves_small_multiples.png", "wave_methods_comparison.png", "deaths_boxplot_by_continent.png",
                    "correlation_heatmap.png", "seasonal_decomposition_world.png", "seasonal_decomposition_us_daily.png",
                    "forecast_comparison.png", "top15_deaths_per_million.png", "cfr_by_wave_year.png",
                    "interactive_weekly_cases.html", "interactive_map_monthly_cases.html",
                    "interactive_vaccination_vs_deaths.html"]
EXPECTED_DB_TABLES = {"continents", "locations", "daily_stats", "weekly_stats", "vaccinations",
                      "hospitalizations", "testing", "excess_mortality", "waves", "forecast_results"}


def check_files():
    """Файлы данных, таблиц и графиков существуют и не пустые."""
    assert config.CLEAN_PARQUET.exists(), "нет очищенного Parquet"
    for name in EXPECTED_TABLES:
        path = config.TABLES_DIR / f"{name}.csv"
        if name in ("data_audit", "cleaning_log"):
            path = config.TABLES_DIR / f"{name}.json"
        assert path.exists() and path.stat().st_size > 0, f"нет файла {path.name}"
    for name in EXPECTED_FIGURES:
        path = config.FIGURES_DIR / name
        assert path.exists() and path.stat().st_size > 0, f"нет графика {name}"
    print(f"✓ файлы: {len(EXPECTED_TABLES)} таблиц и {len(EXPECTED_FIGURES)} графиков на месте")


def check_database():
    """Схема, целостность и внешние ключи SQLite."""
    assert config.DB_PATH.exists(), "нет базы covid.db"
    connection = sqlite3.connect(config.DB_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = EXPECTED_DB_TABLES - tables
    assert not missing, f"в базе нет таблиц {missing}"
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "integrity_check не пройден"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == [], "нарушены внешние ключи"
    rows = connection.execute("SELECT COUNT(*) FROM daily_stats").fetchone()[0]
    assert rows > 500_000, f"в daily_stats слишком мало строк: {rows}"
    waves = connection.execute("SELECT COUNT(*) FROM waves WHERE method = 'peaks'").fetchone()[0]
    assert waves > 100, f"слишком мало волн: {waves}"
    connection.close()
    print(f"✓ база: {len(tables)} таблиц, daily_stats {rows:,} строк, волн {waves}, внешние ключи целы")


def check_results():
    """results.json содержит все разделы, а волны не пересекаются."""
    results = json.loads(config.RESULTS_JSON.read_text(encoding="utf-8"))
    for section in ["1_данные", "2_eda", "3_ряды_и_волны", "4_прогноз", "5_база_и_sql", "6_корреляции"]:
        assert section in results, f"в results.json нет раздела {section}"
    share = results["3_ряды_и_волны"]["доля_случаев_в_волнах_макс"]
    assert share <= 1.001, f"волны пересекаются: доля случаев в волнах {share}"
    assert results["5_база_и_sql"]["проверка"]["результаты_совпадают"], "Pandas и SQL дали разные результаты"
    assert results["5_база_и_sql"]["запросов_выполнено"] >= 8, "выполнено меньше 8 SQL-запросов"
    print(f"✓ результаты: {results['5_база_и_sql']['запросов_выполнено']} SQL-запросов, "
          f"доля случаев в волнах ≤ {share}, Pandas = SQL")


if __name__ == "__main__":
    try:
        check_files()
        check_database()
        check_results()
    except AssertionError as error:
        print(f"✗ ПРОВЕРКА НЕ ПРОЙДЕНА: {error}")
        sys.exit(1)
    print("Все проверки пройдены.")
