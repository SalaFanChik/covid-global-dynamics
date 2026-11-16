# Архитектура проекта «COVID-19 Global Dynamics»

Тема 10, Cluster 2 «Exploratory Analytics, Time Series & Dashboards». Команда ADA-2602M (Astana IT University):
Serikov Alikhan, Mashina Veronika. Документ описывает код таким, какой он лежит в репозитории; все числа взяты из
`outputs/results.json` и `outputs/tables/` последнего полного прогона (срез данных OWID до 2026-10-18,
md5 `0af7a57f919d603b77986a0f3af7679c`). Инструкция по запуску и результаты — в [README.md](../README.md).

## 1. Поток данных

```
compact.csv (185 МБ, 629 868 строк)          scripts/download_data.py или шаг 1
   │  read_csv(chunksize=100 000), 7 чанков  src/data_loader.py
   │  float64→float32, текст→category        326 → 185 МБ
   ├─► data/processed/covid_raw_optimized.parquet (23 МБ)
   │  аудит + очистка с журналом             src/cleaning.py
   ├─► data/processed/covid_clean.parquet (21 МБ, 598 223 строки, 241 локация)
   │
   ├─ шаг 2  EDA ───────────────────────────  src/eda.py            → eda_*.csv, country_summary.csv, boxplot
   ├─ шаг 3  недельная таблица (85 555 строк) src/timeseries.py     → декомпозиция, скользящие средние
   │         волны двумя методами             src/waves.py          → waves_threshold.csv, waves_peaks.csv
   ├─ шаг 4  прогноз rolling origin           src/forecasting.py    → forecast_*.csv
   ├─ шаг 5  SQLite data/covid.db (10 таблиц) src/database.py       → 18 запросов src/sql_queries.py → sql_*.csv
   ├─ шаг 6  корреляции + Plotly              src/correlation.py, src/visualization.py → heatmap, 3 HTML
   └─ дашборд Dash читает только covid.db     dashboard/app.py
```

Каждый шаг пишет свои файлы (CSV/JSON/TXT в `outputs/tables`, PNG/HTML в `outputs/figures`) и свой раздел
`outputs/results.json`, поэтому шаги можно запускать по отдельности и в разные дни. Базовая частота анализа —
неделя: с 2023 г. OWID/ВОЗ публикуют случаи раз в неделю, и дневные значения на 65,5 % состоят из нулей.

## 2. Точка входа `main.py`

| Элемент | Назначение |
|---|---|
| `STATE` (dict) | кэш очищенных данных, недельной таблицы, волн и метрик прогноза внутри одного сеанса |
| `get_clean_df`, `get_weekly`, `get_waves`, `get_forecast_metrics` | берут объект из кэша, иначе из файлов прошлого шага, иначе считают заново |
| `step_load_data` | шаг 1: скачивание (при `ConnectionError` — снимок `covid_clean.parquet`), чтение чанками, Parquet, сравнение форматов, аудит, очистка |
| `step_eda` | шаг 2: `eda.run_eda` + boxplot смертности по континентам |
| `step_timeseries_and_waves` | шаг 3: скользящее среднее (Казахстан), декомпозиция США (period 7) и World (period 52), волны двумя методами, сравнение, чувствительность, проверка покрытия |
| `step_forecast` | шаг 4: 4 модели × 2 горизонта на 11 рядах, сводка, MAE по годам, график |
| `step_database` | шаг 5: сборка базы, 18 запросов, Pandas vs SQL, два графика по результатам запросов |
| `step_correlation_and_interactive` | шаг 6: матрица Спирмена, вакцинация vs смертность (данные из SQL), 3 интерактивных графика |
| `step_dashboard` | пункт 8: запуск `dashboard.app.run_dashboard` |
| `run_all` | пункт 7 / `--all`: шаги 1–6 подряд с замером времени каждого, затем `environment_info` (версии библиотек, md5 файла) |
| `PIPELINE_STEPS`, `MENU` (dict) | порядок шагов и пункты меню 0–9 |
| `show_results`, `run_menu`, `main` | печать `results.json`; цикл `while True` с проверкой ввода и `try/except`; аргументы `--all`, `--step N` (argparse) |

Пункты меню: 1 данные, 2 EDA, 3 ряды и волны, 4 прогноз, 5 база и SQL, 6 корреляции и интерактив, 7 все шаги,
8 дашборд, 9 показать `results.json`, 0 выход. Полный прогон — 19,0 с (10,8 / 0,3 / 1,3 / 0,9 / 5,0 / 0,7 с).

## 3. Модули `src/` и их функции

| Модуль | Функции | Что делает |
|---|---|---|
| `utils.py` | `log`, `Timer`, `MemoryTimer`, `memory_mb`, `file_size_mb`, `ensure_dirs`, `save_json`, `load_json`, `save_table`, `update_results` | сообщения со временем, секундомер и пиковая память (`tracemalloc`), запись CSV/JSON, накопление `results.json` |
| `data_loader.py` | `download_dataset`, `file_md5`, `optimize_types`, `check_columns`, `read_csv_in_chunks`, `save_parquet`, `load_parquet`, `read_csv_typed`, `read_csv_chunked`, `stream_country_totals`, `compare_read_methods` | скачивание во временный файл с проверкой размера; чтение чанками с приведением типов; Parquet; 5 способов чтения по времени, итоговой и пиковой памяти |
| `cleaning.py` | `audit_quality`, `location_coverage`, `total_vs_new_mismatch`, `clean_data`, `write_audit_txt`, `run_audit_and_clean` | аудит (пропуски, дубликаты, отрицательные, нули, покрытие отчётностью, согласованность total/new); очистка в 7 шагов с журналом — список словарей → `cleaning_log.json`, отчёт `data_audit.txt` |
| `eda.py` | `feature_types`, `describe_numeric`, `missing_report`, `iqr_bounds`, `detect_outliers_iqr`, `country_summary`, `run_eda` | типы признаков, describe по 10 показателям, пропуски, выбросы IQR внутри континентов, сводка «одна строка на страну» (233 страны) |
| `timeseries.py` | `country_series`, `resample_series`, `rolling_mean`, `make_weekly_table`, `make_monthly_table`, `weekly_series`, `decompose_series`, `weekday_effect`, `seasonality_strength` | недельная (пн–вс) и месячная агрегация без превращения пропусков в нули, скользящие средние, `seasonal_decompose`, эффект дня недели, сила сезонности |
| `waves.py` | `smooth_weekly`, `prepare_series`, `classify_wave`, `describe_wave`, `relative_threshold`, `detect_waves_threshold`, `detect_waves_peaks`, `detect_waves_for_all`, `check_wave_coverage`, `match_peaks`, `compare_methods`, `reference_recall`, `sensitivity_table` | baseline «порог» (медиана ненулевых недель за окно анализа, ≥ 4 недели, разрывы ≤ 2 недель) и «пики» (`find_peaks` на log1p: prominence ≥ ln 2, distance ≥ 8 недель, пик ≥ 50 на млн, границы по впадинам); CFR волны со сдвигом смертей на 2 недели; сравнение методов и доля найденных эталонных пиков |
| `forecasting.py` | `naive_forecast`, `seasonal_naive_forecast`, `moving_average_forecast`, `linear_regression_forecast`, `rolling_origin_evaluation`, `evaluate_locations`, `summarize_models`, `mae_by_year`, `conclusion` | словарь `MODELS`; оценка «скользящим началом»: обучение только до момента прогноза, горизонты 1 и 4 недели, MAE и отношение к naive, число побед |
| `correlation.py` | `prepare_country_table`, `correlation_matrix`, `strongest_correlations`, `partial_correlation`, `vaccination_vs_mortality`, `run_correlation` | Спирмен по 158 странам ≥ 1 млн; частная корреляция при контроле медианного возраста; связь внутри возрастных групп |
| `database.py` | `connect`, `create_schema`, `load_reference_tables`, `load_fact_tables`, `build_weekly_table_sql`, `load_results_tables`, `build_database`, `run_queries`, `compare_pandas_vs_sql` | схема `SCHEMA_SQL`, загрузка через `to_sql` (chunksize 50 000), витрина `weekly_stats` одним SQL из `daily_stats`, выполнение каталога запросов с выгрузкой в pandas и CSV, Pandas vs SQL с проверкой равенства |
| `sql_queries.py` | словарь `QUERIES` (18 запросов: имя → описание, конструкции, SQL), `WEEKLY_FROM_DAILY_SQL` | каталог запросов; текст запросов не дублируется в других модулях |
| `visualization.py` | `save_figure`, `save_html`, `shade_waves`, `plot_waves_small_multiples`, `plot_wave_methods`, `plot_deaths_boxplot`, `plot_correlation_heatmap`, `plot_decomposition`, `plot_weekday_effect`, `plot_forecast`, `plot_top_deaths`, `plot_cfr_by_wave_year`, `plotly_weekly_lines`, `plotly_choropleth`, `plotly_vaccination_scatter` | 10 PNG (matplotlib/seaborn, 150 dpi, подпись источника) и 3 интерактивных HTML (Plotly); фиксированная палитра по континентам и моделям |

`dashboard/app.py`: `query`, `load_locations`, `placeholders`, `weekly_for`, `waves_for`, `totals_by_country` (параметризованный SQL),
`empty_figure`, `figure_weekly`, `figure_map`, `figure_top`, `figure_vaccination`, `build_app` (макет + callback-функции
`update_country_options`, `update_figures`), `run_dashboard`.

Скрипты: `scripts/download_data.py` (вызывает `download_dataset`), `scripts/make_erd.py` (`read_schema` через
`PRAGMA table_info` / `foreign_key_list`, `compute_layout`, `draw_table`, `connect_boxes`, `make_erd` → `docs/erd.png`),
`scripts/smoke_test.py` (`check_files`, `check_database`, `check_results`).

## 4. Параметры (`config.py`)

| Параметр | Значение | Где используется |
|---|---|---|
| `DATA_URL`, `RAW_CSV`, `CLEAN_PARQUET`, `DB_PATH`, `RESULTS_JSON` | пути через `pathlib` относительно корня проекта | все модули |
| `CHUNK_SIZE` | 100 000 строк | чтение CSV чанками |
| `FOCUS_COUNTRIES` | Kazakhstan, Russia, Germany, United States, Brazil, India, Italy, Japan, South Africa, United Kingdom | прогноз (+ World), интерактивный график |
| `ANALYSIS_START` … `ANALYSIS_END` | 2020-03-01 … 2023-06-30 | окно, по которому baseline-метод берёт медиану ненулевых недель страны (`waves.relative_threshold`) |
| `MIN_POPULATION_FOR_STATS` | 1 000 000 | сравнение методов волн; тот же порог — в корреляциях и SQL-запросах |
| `WAVE_SMOOTH_WEEKS`, `WAVE_MIN_WEEKS`, `WAVE_MAX_GAP_WEEKS` | 3, 4, 2 | сглаживание и baseline-метод |
| `WAVE_MIN_PROMINENCE`, `WAVE_MIN_DISTANCE_WEEKS`, `WAVE_MIN_PEAK_PER_MILLION`, `WAVE_BOUNDARY_SHARE` | ln 2 = 0,693; 8; 50; 0,10 | метод пиков |
| `WAVE_DEATH_LAG_WEEKS`, `WAVE_MATCH_TOLERANCE_WEEKS` | 2; 4 | смерти волны; сопоставление пиков между методами и с эталоном |
| `REFERENCE_PEAKS` | месяцы пиков Kazakhstan, South Africa, Germany, United States, India (ВОЗ/OWID) | метрика качества детекции |
| `FORECAST_START` … `FORECAST_END` | 2021-01-01 … 2023-04-30 (121 точка прогноза на ряд) | rolling origin |
| `FORECAST_HORIZONS`, `MA_WINDOW`, `N_LAGS`, `SEASONAL_PERIOD_WEEKS` | [1, 4]; 4; 4; 52 | модели прогноза, seasonal naive, декомпозиция World |
| `DASH_HOST`, `DASH_PORT` | 127.0.0.1, 8050 | дашборд |

## 5. Схема базы данных (`data/covid.db`, SQLite, 3НФ)

Справочники → факты → результаты; у всех таблиц фактов составной ключ `(iso_code, date)` или `(iso_code, week_start)`,
`iso_code` — внешний ключ на `locations`. `PRAGMA foreign_keys = ON` включается при каждом соединении,
`foreign_key_check` пуст. Разреженные блоки (вакцинация, госпитализации, тесты, избыточная смертность) хранятся
отдельными таблицами и содержат только непустые строки.

| Таблица | Ключ | Строк | Содержимое |
|---|---|---|---|
| `continents` | `continent_id` | 6 | названия континентов |
| `locations` | `iso_code` | 241 | страна/агрегат, `continent_id` FK, `is_aggregate`, население и 8 постоянных признаков, даты первого/последнего отчёта, `n_days_reported` |
| `daily_stats` | `(iso_code, date)` | 598 223 | случаи, смерти, накопленные значения, на миллион, `reproduction_rate`, `stringency_index` |
| `weekly_stats` | `(iso_code, week_start)` | 85 555 | недельные суммы и значения на миллион; строится SQL-запросом из `daily_stats` за 0,35 с, сверена с pandas |
| `vaccinations` | `(iso_code, date)` | 76 748 | прививки, полностью вакцинированные, бустеры (абсолютные и на 100 чел.) |
| `hospitalizations` | `(iso_code, date)` | 41 543 | пациенты в стационарах и реанимации, недельные поступления |
| `testing` | `(iso_code, date)` | 104 052 | тесты, доля положительных, тестов на случай |
| `excess_mortality` | `(iso_code, date)` | 13 748 | избыточная смертность (%, накопленная, абсолютная, на миллион) |
| `waves` | `wave_id` (autoincrement), `iso_code` FK | 2 245 | волны обоих методов: даты начала/пика/конца, длительность, пик на млн, случаи, смерти, CFR, размер |
| `forecast_results` | `id` (autoincrement), `iso_code` FK | 88 | MAE моделей по странам и горизонтам, отношение к naive, время |

Индексы: `idx_daily_date(daily_stats.date)`, `idx_weekly_week(weekly_stats.week_start)`, `idx_waves_iso(waves.iso_code)`,
`idx_locations_continent(locations.continent_id)`. Служебная таблица `sqlite_sequence` появляется из-за autoincrement.
База собирается за 2,75 с, размер 91,9 МБ. ER-диаграмма `docs/erd.png` (и `docs/erd.mmd`) строится скриптом
`scripts/make_erd.py` из реальной схемы.

## 6. Каталог SQL-запросов (`src/sql_queries.py`, 18 запросов)

| Запрос | Конструкции |
|---|---|
| `top15_deaths_per_million` | JOIN 3 таблиц, коррелированный подзапрос |
| `cases_by_continent_year` | JOIN, GROUP BY, HAVING, strftime |
| `world_monthly_change` | CTE, оконная функция LAG |
| `rank_within_continent` | CTE, JOIN, RANK … PARTITION BY |
| `rolling_average_kazakhstan` | AVG … OVER ROWS BETWEEN |
| `vaccination_vs_deaths_2022` | 2 CTE, JOIN 4 таблиц, агрегаты |
| `waves_per_country` | CTE, ROW_NUMBER и COUNT … OVER, JOIN |
| `waves_by_continent` | JOIN, GROUP BY, HAVING, COUNT DISTINCT |
| `cfr_by_wave_year` | GROUP BY, strftime, агрегаты |
| `countries_above_world_peak` | JOIN, GROUP BY, HAVING со скалярным подзапросом |
| `excess_vs_reported` | CTE, JOIN 4 таблиц, NULLIF |
| `icu_peaks` | CTE, ROW_NUMBER, JOIN |
| `weekly_cases_by_continent` | JOIN, GROUP BY (тот же расчёт делается в pandas) |
| `forecast_best_models` | подзапрос, RANK, JOIN |
| `vaccination_tiers` | CASE WHEN, CTE, GROUP BY, LEFT JOIN |
| `countries_without_vaccination_data` | NOT EXISTS, JOIN |
| `running_total_check` | SUM … OVER (ORDER BY), подзапрос |
| `tests_vs_cfr` | 2 CTE, JOIN 3 таблиц, коррелированный подзапрос, CASE WHEN, GROUP BY |

`database.run_queries` выполняет их по очереди (ошибка в одном запросе не роняет шаг), сохраняет `sql_<имя>.csv` и сводку
`sql_queries_summary.csv` (описание, конструкции, число строк, время). Результаты используются дальше: `top15_deaths_per_million`
и `cfr_by_wave_year` — в графиках, `vaccination_vs_deaths_2022` — в корреляциях, интерактивном графике и дашборде.

## 7. Дашборд (`dashboard/app.py`, Dash 4.5)

Фильтры: годы (`RangeSlider` 2020–2026), континент (`Dropdown`), страны (`Dropdown multi`, список зависит от континента),
показатель (`RadioItems`: случаи / смерти на млн). Графики: недельные ряды с закрашенными волнами первой выбранной страны,
карта-хороплет суммы за период, топ-10 стран, вакцинация к концу 2021 г. против смертности 2022 г., таблица волн.
Все данные — параметризованные SQL-запросы к `weekly_stats`, `locations`, `continents`, `waves` и запрос
`vaccination_vs_deaths_2022` из каталога; CSV и Parquet дашборд не читает. Ошибки: нет базы — `FileNotFoundError`
с подсказкой, ошибка SQLite — сообщение внутри графиков, занятый порт — сообщение вместо трассировки.

## 8. Обработка ошибок и стандартная библиотека

`ConnectionError` при скачивании (переход на снимок данных), `FileNotFoundError` при отсутствии Parquet/базы,
`ValueError` при неизвестной локации или методе, `sqlite3.Error` в запросах и дашборде, `OSError` при занятом порте,
`EOFError`/`KeyboardInterrupt` в меню, `ImportError` без pyarrow, `AssertionError` в smoke-тесте.
Стандартные модули: `pathlib`, `datetime`, `json`, `sqlite3`, `time`, `tracemalloc`, `hashlib`, `urllib`, `shutil`,
`argparse`, `platform`, `math`, `sys`.

## 9. Распределение работы

| Участник | Модули и материалы | Проверял(а) |
|---|---|---|
| Serikov Alikhan | `config.py`, `src/data_loader.py`, `src/cleaning.py`, `src/database.py` + `src/sql_queries.py` + `scripts/make_erd.py`, `src/waves.py`, `dashboard/app.py`, `main.py`, `README.md` | Mashina Veronika |
| Mashina Veronika | `src/eda.py`, `src/timeseries.py`, `src/correlation.py`, `src/forecasting.py`, `src/visualization.py`, ноутбук, письменный отчёт, презентация | Serikov Alikhan |

Это рабочее разделение; каждый участник отвечает на вопросы по любой части кода.
