"""
COVID-19 Global Dynamics — главный файл проекта.

Запуск:
    python main.py            интерактивное консольное меню (требование 7)
    python main.py --all      выполнить все шаги подряд без вопросов (воспроизводимый прогон)
    python main.py --step 3   выполнить один шаг по номеру

Каждый шаг сохраняет результаты в файлы (Parquet, CSV, JSON, PNG, HTML, SQLite),
поэтому шаги можно запускать по отдельности и в разные дни.
"""
import argparse
import sys
from datetime import datetime

import pandas as pd

import config
from src import cleaning, correlation, data_loader, database, eda, forecasting, timeseries, visualization, waves
from src.utils import Timer, ensure_dirs, load_json, log, save_table, update_results

# Кэш данных внутри одного сеанса, чтобы не перечитывать файлы между пунктами меню
STATE = {}


# ---------- вспомогательные загрузчики (читают кэш или файлы прошлых шагов) ----------

def get_clean_df():
    """Очищенные данные: из кэша или из Parquet (шаг 1)."""
    if "df" not in STATE:
        STATE["df"] = data_loader.load_parquet(config.CLEAN_PARQUET)
    return STATE["df"]


def get_weekly():
    """Недельная таблица по всем локациям."""
    if "weekly" not in STATE:
        STATE["weekly"] = timeseries.make_weekly_table(get_clean_df())
    return STATE["weekly"]


def get_waves():
    """Таблицы волн обоих методов: из кэша, из CSV прошлого запуска или заново."""
    if "waves" not in STATE:
        paths = {method: config.TABLES_DIR / f"waves_{method}.csv" for method in ["threshold", "peaks"]}
        if all(path.exists() for path in paths.values()):
            STATE["waves"] = {method: pd.read_csv(path) for method, path in paths.items()}
        else:
            step_timeseries_and_waves()
    return STATE["waves"]


def get_forecast_metrics():
    """Метрики прогноза: из кэша, из CSV или заново."""
    if "forecast_metrics" not in STATE:
        path = config.TABLES_DIR / "forecast_metrics.csv"
        if path.exists():
            STATE["forecast_metrics"] = pd.read_csv(path)
        else:
            step_forecast()
    return STATE["forecast_metrics"]


# ---------- шаги пайплайна ----------

def step_load_data():
    """Шаг 1. Скачивание, чтение CSV чанками, оптимизация типов, Parquet, аудит и очистка."""
    log("ШАГ 1. Загрузка и подготовка данных")
    ensure_dirs()
    data_loader.download_dataset()
    df_raw, load_stats = data_loader.read_csv_in_chunks()
    data_loader.save_parquet(df_raw, config.RAW_PARQUET)
    format_rows = data_loader.compare_csv_parquet()
    save_table(pd.DataFrame(format_rows), "format_comparison")

    clean_df, audit_report, steps = cleaning.run_audit_and_clean(df_raw)
    data_loader.save_parquet(clean_df, config.CLEAN_PARQUET)
    STATE["df"] = clean_df
    STATE.pop("weekly", None)

    update_results("1_данные", {
        "загрузка": load_stats,
        "сравнение_форматов": format_rows,
        "аудит": {key: audit_report[key] for key in ["строк", "столбцов", "локаций", "период",
                                                      "дубликаты_ключа_location_date", "доля_нулей_new_cases"]},
        "очистка": steps,
        "после_очистки": {"строк": int(len(clean_df)), "локаций": int(clean_df["location"].nunique())},
    })


def step_eda():
    """Шаг 2. Разведочный анализ: типы, описательная статистика, пропуски, выбросы IQR."""
    log("ШАГ 2. EDA")
    df = get_clean_df()
    results, summary = eda.run_eda(df)
    STATE["summary"] = summary
    visualization.plot_deaths_boxplot(summary)
    update_results("2_eda", results)


def step_timeseries_and_waves():
    """Шаг 3. Временные ряды (resample, скользящие средние, декомпозиция) и детекция волн."""
    log("ШАГ 3. Временные ряды и волны")
    df = get_clean_df()
    weekly = get_weekly()

    # скользящее среднее: наш расчёт против готового столбца OWID (Казахстан)
    daily_kz = timeseries.country_series(df, "Kazakhstan", "new_cases")
    rolling = pd.DataFrame({
        "new_cases": daily_kz,
        "rolling_7d_наш": timeseries.rolling_mean(daily_kz, 7),
        "new_cases_smoothed_owid": timeseries.country_series(df, "Kazakhstan", "new_cases_smoothed"),
    }).loc["2021-07-01":"2021-08-31"]
    save_table(rolling.reset_index(), "rolling_mean_kazakhstan")

    # сезонная декомпозиция мирового недельного ряда
    world = timeseries.weekly_series(weekly, "World", "new_cases_per_million")
    decomposition = timeseries.decompose_series(world)
    strength = timeseries.seasonality_strength(decomposition)
    visualization.plot_decomposition(decomposition, "Сезонная декомпозиция недельных случаев на млн (World)")

    # волны двумя методами
    waves_threshold, seconds_threshold = waves.detect_waves_for_all(weekly, "threshold")
    waves_peaks, seconds_peaks = waves.detect_waves_for_all(weekly, "peaks")
    comparison, agreement = waves.compare_methods(waves_threshold, waves_peaks, seconds_threshold, seconds_peaks)
    save_table(waves_threshold, "waves_threshold")
    save_table(waves_peaks, "waves_peaks")
    save_table(comparison, "wave_methods_comparison")
    STATE["waves"] = {"threshold": waves_threshold, "peaks": waves_peaks}

    visualization.plot_waves_small_multiples(weekly, waves_peaks,
                                             ["Kazakhstan", "Germany", "United States", "South Africa"])
    visualization.plot_wave_methods(weekly, waves_threshold, waves_peaks, "Kazakhstan")

    kazakhstan_waves = waves_peaks[waves_peaks["location"] == "Kazakhstan"]
    update_results("3_ряды_и_волны", {
        "сила_сезонности_world": strength,
        "сравнение_методов": comparison.to_dict(orient="records"),
        "совпадение_пиков_%": agreement,
        "волны_казахстан": kazakhstan_waves[["wave_no", "start_date", "peak_date", "end_date",
                                              "peak_weekly_cases_per_million", "cfr_pct", "size"]].to_dict(orient="records"),
    })


def step_forecast():
    """Шаг 4. Прогноз: naive / seasonal naive / скользящее среднее / линейная регрессия, MAE."""
    log("ШАГ 4. Прогноз и сравнение с baseline")
    weekly = get_weekly()
    metrics, forecasts = forecasting.evaluate_locations(weekly, config.FOCUS_COUNTRIES + ["World"])
    summary = forecasting.summarize_models(metrics)
    lines = forecasting.conclusion(summary)
    for line in lines:
        log("  " + line)
    save_table(metrics, "forecast_metrics")
    save_table(summary, "forecast_summary")
    save_table(forecasts, "forecast_predictions")
    STATE["forecast_metrics"] = metrics
    visualization.plot_forecast(forecasts, metrics, "Kazakhstan")
    update_results("4_прогноз", {"период_оценки": [config.FORECAST_START, config.FORECAST_END],
                                 "сводка": summary.to_dict(orient="records"), "выводы": lines})


def step_database():
    """Шаг 5. SQLite: схема, загрузка, 14 SQL-запросов, Pandas vs SQL, графики по результатам запросов."""
    log("ШАГ 5. База данных и SQL")
    df = get_clean_df()
    all_waves = pd.concat(get_waves().values(), ignore_index=True)
    stats = database.build_database(df, all_waves, get_forecast_metrics())

    connection = database.connect()
    try:
        results, summary = database.run_queries(connection)
        comparison, check = database.compare_pandas_vs_sql(df, connection)
    finally:
        connection.close()
    save_table(summary, "sql_queries_summary")
    save_table(comparison, "pandas_vs_sql")
    STATE["sql_results"] = results

    visualization.plot_top_deaths(results["top15_deaths_per_million"])
    visualization.plot_cfr_by_wave_year(results["cfr_by_wave_year"])
    update_results("5_база_и_sql", {"база": stats, "запросов_выполнено": int(len(summary)),
                                    "pandas_vs_sql": comparison.to_dict(orient="records"), "проверка": check})


def step_correlation_and_interactive():
    """Шаг 6. Корреляции (heatmap) и интерактивные графики Plotly."""
    log("ШАГ 6. Корреляции и интерактивные графики")
    df = get_clean_df()
    weekly = get_weekly()
    summary = STATE.get("summary")
    if summary is None:
        summary = eda.country_summary(df)

    vaccination_table = None
    if config.DB_PATH.exists():
        connection = database.connect()
        try:
            vaccination_table = pd.read_sql_query(
                database.QUERIES["vaccination_vs_deaths_2022"]["sql"], connection)
        finally:
            connection.close()
    else:
        log("  база ещё не создана — анализ «вакцинация vs смертность» пропущен (выполните шаг 5)")

    results, matrix = correlation.run_correlation(summary, vaccination_table)
    visualization.plot_correlation_heatmap(matrix)
    visualization.plotly_weekly_lines(weekly, config.FOCUS_COUNTRIES)
    visualization.plotly_choropleth(timeseries.make_monthly_table(df))
    if vaccination_table is not None:
        visualization.plotly_vaccination_scatter(vaccination_table)
    update_results("6_корреляции", results)


def step_dashboard():
    """Шаг 8. Запуск дашборда Dash (остановка — Ctrl+C)."""
    log(f"Запускаю дашборд: http://{config.DASH_HOST}:{config.DASH_PORT}")
    from dashboard.app import run_dashboard
    run_dashboard()


def run_all():
    """Шаг 7. Все шаги анализа подряд с замером времени каждого."""
    timings = {}
    for number, (name, step) in enumerate(PIPELINE_STEPS.items(), start=1):
        with Timer() as timer:
            step()
        timings[f"{number}. {name}"] = timer.seconds
        log(f"--- шаг {number} выполнен за {timer.seconds} с ---")
    update_results("время_шагов_с", timings)
    log(f"Готово. Итоги в {config.RESULTS_JSON}, таблицы в {config.TABLES_DIR}, графики в {config.FIGURES_DIR}")
    return timings


# Порядок шагов полного пайплайна (словарь: название -> функция)
PIPELINE_STEPS = {
    "Загрузка и подготовка данных": step_load_data,
    "EDA": step_eda,
    "Временные ряды и волны": step_timeseries_and_waves,
    "Прогноз": step_forecast,
    "База данных и SQL": step_database,
    "Корреляции и интерактивные графики": step_correlation_and_interactive,
}

MENU = {
    "1": ("Скачать и подготовить данные (чанки, типы, Parquet, аудит, очистка)", step_load_data),
    "2": ("EDA: статистика, пропуски, выбросы", step_eda),
    "3": ("Временные ряды и детекция волн", step_timeseries_and_waves),
    "4": ("Прогноз и сравнение с baseline (MAE)", step_forecast),
    "5": ("База данных SQLite и SQL-запросы", step_database),
    "6": ("Корреляции и интерактивные графики", step_correlation_and_interactive),
    "7": ("Выполнить все шаги подряд", run_all),
    "8": ("Запустить дашборд Dash", step_dashboard),
    "9": ("Показать сохранённые результаты (results.json)", None),
    "0": ("Выход", None),
}


def show_results():
    """Печатает краткое содержимое results.json."""
    results = load_json(config.RESULTS_JSON)
    if not results:
        print("Результатов ещё нет — выполните шаги анализа.")
        return
    print(f"Файл {config.RESULTS_JSON} обновлён: {results.get('обновлено')}")
    for section, content in results.items():
        if section == "обновлено":
            continue
        keys = list(content.keys()) if isinstance(content, dict) else f"{len(content)} записей"
        print(f"  {section}: {keys}")


def run_menu():
    """Интерактивное меню: цикл while, проверка ввода, обработка ошибок."""
    print("=" * 70)
    print("COVID-19 Global Dynamics — анализ волн, вакцинации и смертности (OWID)")
    print("=" * 70)
    while True:
        print()
        for key, (title, _) in MENU.items():
            print(f"  {key} — {title}")
        choice = input("Выберите пункт: ").strip()

        if choice == "0":
            print("До свидания!")
            break
        elif choice == "9":
            show_results()
        elif choice in MENU:
            title, step = MENU[choice]
            try:
                with Timer() as timer:
                    step()
                log(f"«{title}» выполнено за {timer.seconds} с")
            except FileNotFoundError as error:
                print(f"Нет нужного файла: {error}")
            except (ConnectionError, ValueError) as error:
                print(f"Ошибка: {error}")
            except KeyboardInterrupt:
                print("\nШаг прерван пользователем.")
        else:
            print(f"Неверный ввод «{choice}». Введите число от 0 до {len(MENU) - 1}.")


def main():
    parser = argparse.ArgumentParser(description="COVID-19 Global Dynamics")
    parser.add_argument("--all", action="store_true", help="выполнить все шаги без меню")
    parser.add_argument("--step", type=int, help="выполнить один шаг по номеру меню (1–6)")
    args = parser.parse_args()

    started = datetime.now()
    if args.all:
        run_all()
    elif args.step is not None:
        if str(args.step) not in MENU or MENU[str(args.step)][1] is None:
            print(f"Нет шага с номером {args.step}")
            sys.exit(1)
        MENU[str(args.step)][1]()
    else:
        run_menu()
    log(f"Время работы: {datetime.now() - started}")


if __name__ == "__main__":
    main()
