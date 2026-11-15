# Архитектура проекта «COVID-19 Global Dynamics» (проект решения, v1)

Тема 10, Cluster 2. Команда: Serikov Alikhan, Mashina Veronika (ADA-2602M).
Цель: проанализировать волны заболеваемости, вакцинацию и смертность по странам (OWID), реализовать
детекцию волн и интерактивный дашборд, выполнив все обязательные задачи кластера 2 и технические требования 1–11.

## 1. Данные
- Основной файл: `data/raw/owid_covid_compact.csv` — актуальная версия набора OWID (629 868 строк × 61 столбец, 185 МБ,
  2020-01-01 … 2026-10-18). Скачивается `scripts/download_data.py` (urllib, try/except, пропуск если файл уже есть).
  Репозиторий GitHub указывает на этот файл как на текущую версию набора (см. docs/data_notes.md).
- Загрузчик переименовывает `country→location`, `code→iso_code`, поэтому старый GitHub-файл `owid-covid-data.csv` тоже читается.
- Базовая частота анализа — неделя (после 2023 г. отчётность недельная, см. data_notes).

## 2. Структура репозитория
```
covid-global-dynamics/
├── main.py                 # консольное меню (while + try/except) и запуск полного пайплайна (--all)
├── config.py               # пути (pathlib), URL, списки стран, параметры волн/прогноза
├── requirements.txt
├── README.md
├── scripts/download_data.py
├── src/
│   ├── utils.py            # таймер, замер памяти, save_json/load_json, лог с datetime
│   ├── data_loader.py      # чтение CSV чанками, оптимизация типов (замер до/после), CSV vs Parquet
│   ├── cleaning.py         # аудит качества + очистка с журналом шагов (list[dict] → JSON)
│   ├── eda.py              # describe, пропуски, выбросы IQR, типы признаков, сводка по странам
│   ├── timeseries.py       # resample, скользящие средние, seasonal_decompose
│   ├── waves.py            # детекция волн: пороговый метод (baseline) и метод пиков (find_peaks)
│   ├── correlation.py      # матрица корреляций страновых показателей, вакцинация vs смертность
│   ├── database.py         # SQLite: схема (3NF), загрузка, 10+ SQL-запросов → pandas, Pandas vs SQL
│   ├── sql_queries.py      # словарь {имя: {"sql": ..., "описание": ...}}
│   ├── forecasting.py      # naive / seasonal naive / moving average / LinearRegression, MAE, время
│   └── visualization.py    # matplotlib/seaborn (PNG) и Plotly (HTML)
├── dashboard/app.py        # Dash-дашборд с фильтрами (год, континент, страна, метрика), данные из SQLite
├── notebooks/COVID19_Global_Dynamics.ipynb  # ноутбук с выводами (Colab-совместимый)
├── data/raw/ (gitignore, кроме кодбука)  data/processed/*.parquet  data/covid.db
├── outputs/figures/*.png|html   outputs/tables/*.csv   outputs/results.json
└── docs/ (требования, заметки, ER-диаграмма, отчёт, презентация)
```

## 3. Пайплайн (пункты меню main.py)
1. **Загрузка данных** — `read_csv(chunksize=100_000)`; по каждому чанку: приведение типов (float64→float32,
   строки→category, date→datetime), замер памяти до/после; сохранение в Parquet; таблица сравнения
   CSV vs Parquet (размер файла, время чтения, память) → `outputs/tables/format_comparison.csv`.
2. **Аудит и EDA** — пропуски по столбцам, дубликаты ключа, отрицательные значения, нули (недельная отчётность),
   диапазон дат по странам, агрегаты OWID; очистка: разделение стран и агрегатов (`is_aggregate`), удаление
   полностью пустых столбцов, заполнение дат; описательная статистика, выбросы IQR (boxplot смертности на млн по континентам),
   типы признаков. Журнал очистки → `outputs/tables/cleaning_log.json`.
3. **Временные ряды и волны** — недельные ряды (`resample('W').sum()`), скользящие средние (3 и 7 недель / 7 дней),
   `seasonal_decompose` (World, недельный ряд, period=52). Детекция волн двумя методами на недельных случаях на млн:
   - baseline «порог»: сглаженный ряд ≥ T (абсолютный порог), минимум 4 недели, разрывы ≤ 2 недель объединяются;
   - «пики»: `scipy.signal.find_peaks` на log1p(ряда) с prominence ≥ ln 2 (рост и спад ≥ 2 раза), distance ≥ 8 недель,
     минимальная высота пика; границы волны — минимумы между пиками / падение ниже 10 % пика.
   Для каждой волны: start, peak, end, длительность, пиковое значение, сумма случаев и смертей, CFR.
   Сравнение методов: число волн по странам, совпадение пиков (±4 недели), время работы → таблица + вывод.
4. **Корреляции** — страновая сводка (смертность на млн, случаи на млн, CFR, покрытие вакцинацией, ВВП, медианный
   возраст, койки, диабет, ожидаемая продолжительность жизни); `corr()` + heatmap; scatter вакцинация vs смертность 2022.
5. **SQLite и SQL** — создание схемы, загрузка очищенных данных, выполнение 10+ запросов из `sql_queries.py`
   (JOIN 2–3 таблиц, GROUP BY/HAVING, подзапросы, CTE, оконные функции RANK/LAG/ROW_NUMBER/скользящее AVG),
   результаты в pandas и CSV; **Pandas vs SQL**: одинаковая агрегация (недельные случаи по континентам) — время и проверка равенства.
6. **Прогноз** — недельные случаи на млн для списка стран + World; оценка «скользящим началом» (rolling origin)
   на периоде 2021-01 … 2023-06: обучение строго до момента прогноза, горизонт 1 и 4 недели;
   модели: naive (baseline), seasonal naive (52 недели), moving average (4 недели), LinearRegression на лагах 1–4;
   MAE и время по моделям/странам → таблица и график; результаты в SQLite (`forecast_results`).
7. **Всё сразу** — последовательный запуск 1–6, итоговый `outputs/results.json` (все метрики с датой запуска).
8. **Дашборд** — запуск `dashboard/app.py` (Dash, порт 8050).
0. Выход. Неверный ввод → ValueError перехватывается, отсутствие файла → FileNotFoundError с подсказкой.

## 4. Схема базы данных (SQLite, 3NF) — `data/covid.db`
- `continents(continent_id PK, continent_name UNIQUE)`
- `locations(iso_code PK, location UNIQUE, continent_id FK NULL, is_aggregate, population, population_density, median_age,
  life_expectancy, gdp_per_capita, extreme_poverty, diabetes_prevalence, handwashing_facilities, hospital_beds_per_thousand)`
- `daily_stats(iso_code FK, date, new_cases, new_deaths, total_cases, total_deaths, new_cases_smoothed, new_deaths_smoothed,
  new_cases_per_million, new_deaths_per_million, total_cases_per_million, total_deaths_per_million, reproduction_rate,
  stringency_index; PK(iso_code, date))` — ~630 тыс. строк
- `weekly_stats(iso_code FK, week_start, new_cases, new_deaths, new_cases_per_million, new_deaths_per_million; PK)` —
  производная витрина для дашборда (создаётся в SQL из daily_stats, сверяется с pandas)
- `vaccinations(iso_code FK, date, total_vaccinations, people_vaccinated, people_fully_vaccinated, total_boosters,
  new_vaccinations, people_vaccinated_per_hundred, people_fully_vaccinated_per_hundred, total_boosters_per_hundred; PK)` — только непустые строки
- `hospitalizations(iso_code FK, date, icu_patients, hosp_patients, weekly_icu_admissions, weekly_hosp_admissions + per_million; PK)`
- `testing(iso_code FK, date, new_tests, total_tests, positive_rate, tests_per_case; PK)`
- `excess_mortality(iso_code FK, date, excess_mortality, excess_mortality_cumulative, excess_mortality_cumulative_absolute,
  excess_mortality_cumulative_per_million; PK)`
- `waves(wave_id PK, iso_code FK, method, wave_no, start_date, peak_date, end_date, duration_days,
  peak_weekly_cases_per_million, total_cases, total_deaths, cfr_pct)`
- `forecast_results(id PK, iso_code FK, model, horizon_weeks, mae, fit_seconds, is_baseline)`
ER-диаграмма: `docs/erd.png` (mermaid erDiagram).

## 5. Визуализации (≥ 5, из них 2 интерактивные)
1. Недельные случаи на млн с закрашенными волнами (Казахстан + 3 страны, matplotlib).
2. Boxplot смертности на млн по континентам с выбросами IQR (seaborn).
3. Heatmap корреляций страновых показателей (seaborn).
4. Сезонная декомпозиция мирового недельного ряда (statsmodels).
5. Факт vs прогноз 4 моделей + столбцы MAE (matplotlib).
6. Plotly: интерактивный линейный график (выбор страны/метрики) → HTML.
7. Plotly: анимированная хороплет-карта месячных случаев на млн → HTML (и в дашборде).
8. Топ-15 стран по смертности на млн (из SQL-запроса).

## 6. Дашборд (Dash)
Фильтры: диапазон лет (RangeSlider), континент (Dropdown), страны (multi Dropdown, зависит от континента),
метрика (RadioItems: случаи/смерти на млн, вакцинация). Графики: недельный ряд с волнами (из таблицы waves),
карта-хороплет по выбранному году, топ-10 стран по метрике, scatter «полностью вакцинированные % vs смерти на млн».
Все данные читаются из SQLite SQL-запросами с параметрами.

## 7. Сравнения для требования 9 (метрики, время, память)
| Сравнение | Baseline | Альтернативы | Критерии |
|-----------|----------|--------------|----------|
| Формат хранения | CSV | Parquet | размер файла, время чтения, память |
| Типы данных | float64/object | float32/category | память до/после |
| Агрегация | Pandas groupby | SQL GROUP BY | время, равенство результатов |
| Детекция волн | пороговое правило | find_peaks | число волн, совпадение пиков, время |
| Прогноз | naive, seasonal naive | moving average, LinearRegression | MAE (горизонт 1 и 4 нед.), время |

## 8. Распределение работы (предложение, уточнить в команде)
- **Serikov Alikhan**: архитектура и меню, загрузка/оптимизация данных и Parquet, схема SQLite + ER-диаграмма + SQL-запросы,
  детекция волн, README.
- **Mashina Veronika**: аудит данных и EDA, временные ряды и декомпозиция, корреляционный анализ, визуализации,
  сравнение прогнозов, дашборд Dash, отчёт и презентация.
Каждый участник должен уметь объяснить любую часть кода (критерий 8 оценивается индивидуально).

## 9. Соответствие требованиям (чек-лист)
Тр.1 функции (>30) · Тр.2 list/dict (журналы, словарь запросов, списки стран) · Тр.3 if/elif/else (меню, классификация волн)
· Тр.4 циклы (чанки, страны, rolling origin) · Тр.5 файлы (CSV/Parquet/JSON/TXT отчёт аудита) · Тр.6 try/except (ввод, файл, URL, SQL)
· Тр.7 меню + Dash · Тр.8 модули + os/pathlib/datetime/json/sqlite3 · Тр.9 таблица выше · Тр.10 SQLite, ERD, 10+ запросов
· Тр.11 629 868 строк / 185 МБ, chunksize, типы, Parquet · Cluster 2: EDA, ряды, корреляции, 8 графиков (2 Plotly), SQLite, Dash, прогноз vs baseline.
