"""
Аналитические SQL-запросы к базе data/covid.db (требование 10: не менее 8 запросов).

Словарь QUERIES: имя запроса -> {"описание", "конструкции", "sql"}.
Все запросы выполняются функцией database.run_queries(), результаты попадают в pandas
и используются в графиках и отчёте.
"""

QUERIES = {
    "top15_deaths_per_million": {
        "описание": "Топ-15 стран (население ≥ 1 млн) по накопленной смертности на миллион жителей",
        "конструкции": ["JOIN 3 таблиц", "коррелированный подзапрос"],
        "sql": """
            SELECT l.location,
                   c.continent_name AS continent,
                   d.date AS last_date,
                   ROUND(d.total_deaths_per_million, 1) AS total_deaths_per_million,
                   ROUND(d.total_cases_per_million, 0)  AS total_cases_per_million
            FROM daily_stats d
            JOIN locations  l ON l.iso_code = d.iso_code
            JOIN continents c ON c.continent_id = l.continent_id
            WHERE l.is_aggregate = 0 AND l.population >= 1000000
              AND d.date = (SELECT MAX(date) FROM daily_stats
                            WHERE iso_code = d.iso_code AND total_deaths_per_million IS NOT NULL)
            ORDER BY d.total_deaths_per_million DESC
            LIMIT 15;
        """,
    },
    "cases_by_continent_year": {
        "описание": "Случаи, смерти и летальность по континентам и годам (только крупные группы)",
        "конструкции": ["JOIN", "GROUP BY", "HAVING", "strftime"],
        "sql": """
            SELECT c.continent_name AS continent,
                   CAST(strftime('%Y', d.date) AS INTEGER) AS year,
                   SUM(d.new_cases)  AS cases,
                   SUM(d.new_deaths) AS deaths,
                   ROUND(100.0 * SUM(d.new_deaths) / NULLIF(SUM(d.new_cases), 0), 2) AS cfr_pct
            FROM daily_stats d
            JOIN locations  l ON l.iso_code = d.iso_code
            JOIN continents c ON c.continent_id = l.continent_id
            WHERE l.is_aggregate = 0
            GROUP BY c.continent_name, year
            HAVING SUM(d.new_cases) > 1000000
            ORDER BY continent, year;
        """,
    },
    "world_monthly_change": {
        "описание": "Месячная динамика случаев в мире и изменение к предыдущему месяцу (CTE + LAG)",
        "конструкции": ["CTE", "оконная функция LAG"],
        "sql": """
            WITH monthly AS (
                SELECT strftime('%Y-%m', date) AS month,
                       SUM(new_cases)  AS cases,
                       SUM(new_deaths) AS deaths
                FROM daily_stats
                WHERE iso_code = 'OWID_WRL'
                GROUP BY month
            )
            SELECT month, cases, deaths,
                   LAG(cases) OVER (ORDER BY month) AS prev_month_cases,
                   ROUND(100.0 * (cases - LAG(cases) OVER (ORDER BY month))
                         / NULLIF(LAG(cases) OVER (ORDER BY month), 0), 1) AS change_pct
            FROM monthly
            ORDER BY month;
        """,
    },
    "rank_within_continent": {
        "описание": "Три страны с наибольшей смертностью на миллион внутри каждого континента (RANK)",
        "конструкции": ["CTE", "JOIN", "оконная функция RANK ... PARTITION BY"],
        "sql": """
            WITH last_dates AS (
                SELECT iso_code, MAX(date) AS last_date
                FROM daily_stats
                WHERE total_deaths_per_million IS NOT NULL
                GROUP BY iso_code
            ),
            deaths AS (
                SELECT l.location, c.continent_name AS continent, d.total_deaths_per_million
                FROM last_dates ld
                JOIN daily_stats d ON d.iso_code = ld.iso_code AND d.date = ld.last_date
                JOIN locations   l ON l.iso_code = d.iso_code
                JOIN continents  c ON c.continent_id = l.continent_id
                WHERE l.is_aggregate = 0
            ),
            ranked AS (
                SELECT continent, location, ROUND(total_deaths_per_million, 1) AS total_deaths_per_million,
                       RANK() OVER (PARTITION BY continent ORDER BY total_deaths_per_million DESC) AS rank_in_continent
                FROM deaths
            )
            SELECT * FROM ranked
            WHERE rank_in_continent <= 3
            ORDER BY continent, rank_in_continent;
        """,
    },
    "rolling_average_kazakhstan": {
        "описание": "Скользящее среднее за 7 дней в SQL против готового столбца OWID (Казахстан, лето 2021)",
        "конструкции": ["CTE", "оконная функция AVG ... ROWS BETWEEN"],
        "sql": """
            -- окно считается по всему ряду страны, а фильтр дат применяется снаружи:
            -- иначе первые 6 строк периода усреднялись бы по неполному окну
            WITH kaz AS (
                SELECT date, new_cases, new_cases_smoothed,
                       AVG(new_cases) OVER (ORDER BY date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS rolling_7d_sql
                FROM daily_stats
                WHERE iso_code = 'KAZ'
            )
            SELECT date, new_cases, ROUND(rolling_7d_sql, 2) AS rolling_7d_sql,
                   ROUND(new_cases_smoothed, 2) AS rolling_7d_owid
            FROM kaz
            WHERE date BETWEEN '2021-07-01' AND '2021-08-31'
            ORDER BY date;
        """,
    },
    "vaccination_vs_deaths_2022": {
        "описание": "Покрытие вакцинацией к концу 2021 г. и смертность на миллион за 2022 г. (страны > 1 млн)",
        "конструкции": ["2 CTE", "JOIN 4 таблиц", "агрегаты"],
        "sql": """
            WITH coverage AS (
                SELECT iso_code, MAX(people_fully_vaccinated_per_hundred) AS fully_vaccinated_pct
                FROM vaccinations
                WHERE date <= '2021-12-31'
                GROUP BY iso_code
            ),
            deaths_2022 AS (
                SELECT iso_code, SUM(new_deaths_per_million) AS deaths_per_million_2022
                FROM daily_stats
                WHERE date BETWEEN '2022-01-01' AND '2022-12-31'
                GROUP BY iso_code
            )
            SELECT l.location, c.continent_name AS continent, l.median_age, l.gdp_per_capita,
                   ROUND(cov.fully_vaccinated_pct, 1) AS fully_vaccinated_pct,
                   ROUND(d.deaths_per_million_2022, 1) AS deaths_per_million_2022
            FROM locations l
            JOIN continents c ON c.continent_id = l.continent_id
            JOIN coverage cov ON cov.iso_code = l.iso_code
            JOIN deaths_2022 d ON d.iso_code = l.iso_code
            WHERE l.is_aggregate = 0 AND l.population > 1000000
            ORDER BY cov.fully_vaccinated_pct DESC;
        """,
    },
    "waves_per_country": {
        "описание": "Число волн по странам (≥ 1 млн жителей) и самая сильная волна каждой (ROW_NUMBER, COUNT OVER)",
        "конструкции": ["CTE", "оконные функции ROW_NUMBER и COUNT ... OVER", "JOIN"],
        "sql": """
            WITH ranked AS (
                SELECT w.*,
                       ROW_NUMBER() OVER (PARTITION BY w.iso_code ORDER BY w.peak_weekly_cases_per_million DESC) AS rn,
                       COUNT(*)     OVER (PARTITION BY w.iso_code) AS n_waves
                FROM waves w
                WHERE w.method = 'peaks'
            )
            SELECT l.location, c.continent_name AS continent, r.n_waves,
                   r.peak_date AS strongest_peak_date, r.peak_weekly_cases_per_million,
                   r.duration_weeks, r.cfr_pct, r.size
            FROM ranked r
            JOIN locations  l ON l.iso_code = r.iso_code
            JOIN continents c ON c.continent_id = l.continent_id
            WHERE r.rn = 1 AND l.population >= 1000000
            ORDER BY r.n_waves DESC, r.peak_weekly_cases_per_million DESC;
        """,
    },
    "waves_by_continent": {
        "описание": "Среднее число волн на страну, длительность и летальность волн по континентам",
        "конструкции": ["JOIN", "GROUP BY", "HAVING", "COUNT DISTINCT"],
        "sql": """
            SELECT c.continent_name AS continent,
                   COUNT(*) AS waves,
                   COUNT(DISTINCT w.iso_code) AS countries,
                   ROUND(1.0 * COUNT(*) / COUNT(DISTINCT w.iso_code), 2) AS waves_per_country,
                   ROUND(AVG(w.duration_weeks), 1) AS avg_duration_weeks,
                   ROUND(AVG(w.cfr_pct), 2) AS avg_cfr_pct
            FROM waves w
            JOIN locations  l ON l.iso_code = w.iso_code
            JOIN continents c ON c.continent_id = l.continent_id
            WHERE w.method = 'peaks' AND l.population >= 1000000
            GROUP BY c.continent_name
            HAVING COUNT(DISTINCT w.iso_code) >= 10
            ORDER BY waves_per_country DESC;
        """,
    },
    "cfr_by_wave_year": {
        "описание": "Летальность волн по году пика: как менялась тяжесть волн по мере вакцинации",
        "конструкции": ["GROUP BY", "strftime", "агрегаты"],
        "sql": """
            SELECT strftime('%Y', peak_date) AS peak_year,
                   COUNT(*) AS waves,
                   ROUND(AVG(peak_weekly_cases_per_million), 0) AS avg_peak_per_million,
                   ROUND(AVG(cfr_pct), 2) AS avg_cfr_pct,
                   ROUND(AVG(duration_weeks), 1) AS avg_duration_weeks
            FROM waves w
            JOIN locations l ON l.iso_code = w.iso_code
            WHERE w.method = 'peaks' AND l.population >= 1000000
            GROUP BY peak_year
            ORDER BY peak_year;
        """,
    },
    "countries_above_world_peak": {
        "описание": "Страны (≥ 1 млн), чей пик недельной заболеваемости втрое выше мирового (скалярный подзапрос)",
        "конструкции": ["JOIN", "GROUP BY", "HAVING со скалярным подзапросом"],
        "sql": """
            SELECT l.location, ROUND(MAX(w.new_cases_per_million), 0) AS peak_weekly_per_million
            FROM weekly_stats w
            JOIN locations l ON l.iso_code = w.iso_code
            WHERE l.is_aggregate = 0 AND l.population >= 1000000
            GROUP BY l.location
            HAVING MAX(w.new_cases_per_million) >
                   3 * (SELECT MAX(new_cases_per_million) FROM weekly_stats WHERE iso_code = 'OWID_WRL')
            ORDER BY peak_weekly_per_million DESC
            LIMIT 15;
        """,
    },
    "excess_vs_reported": {
        "описание": "Избыточная смертность против официальной: во сколько раз избыточные смерти больше учтённых",
        "конструкции": ["CTE", "JOIN 4 таблиц", "NULLIF"],
        "sql": """
            WITH last_excess AS (
                SELECT iso_code, MAX(date) AS date
                FROM excess_mortality
                WHERE excess_mortality_cumulative_absolute IS NOT NULL
                GROUP BY iso_code
            )
            SELECT l.location, e.date,
                   ROUND(e.excess_mortality_cumulative_absolute, 0) AS excess_deaths,
                   ROUND(d.total_deaths, 0) AS reported_deaths,
                   ROUND(e.excess_mortality_cumulative_absolute / NULLIF(d.total_deaths, 0), 2) AS excess_to_reported
            FROM last_excess le
            JOIN excess_mortality e ON e.iso_code = le.iso_code AND e.date = le.date
            JOIN daily_stats d ON d.iso_code = e.iso_code AND d.date = e.date
            JOIN locations l ON l.iso_code = e.iso_code
            WHERE l.is_aggregate = 0 AND d.total_deaths > 1000
            ORDER BY excess_to_reported DESC;
        """,
    },
    "icu_peaks": {
        "описание": "Пиковая загрузка реанимаций (ICU на миллион) по странам с такими данными",
        "конструкции": ["CTE", "оконная функция ROW_NUMBER", "JOIN"],
        "sql": """
            WITH ranked AS (
                SELECT h.iso_code, h.date, h.icu_patients_per_million,
                       ROW_NUMBER() OVER (PARTITION BY h.iso_code ORDER BY h.icu_patients_per_million DESC) AS rn
                FROM hospitalizations h
                WHERE h.icu_patients_per_million IS NOT NULL
            )
            SELECT l.location, r.date AS peak_icu_date, ROUND(r.icu_patients_per_million, 1) AS icu_patients_per_million
            FROM ranked r
            JOIN locations l ON l.iso_code = r.iso_code
            WHERE r.rn = 1
            ORDER BY r.icu_patients_per_million DESC
            LIMIT 15;
        """,
    },
    "weekly_cases_by_continent": {
        "описание": "Недельные случаи по континентам (этот же расчёт делается в pandas для сравнения скорости)",
        "конструкции": ["JOIN", "GROUP BY"],
        "sql": """
            SELECT c.continent_name AS continent, w.week_start, SUM(w.new_cases) AS new_cases
            FROM weekly_stats w
            JOIN locations  l ON l.iso_code = w.iso_code
            JOIN continents c ON c.continent_id = l.continent_id
            WHERE l.is_aggregate = 0
            GROUP BY c.continent_name, w.week_start
            ORDER BY continent, week_start;
        """,
    },
    "forecast_best_models": {
        "описание": "Лучшая модель прогноза для каждой страны и горизонта (RANK по MAE)",
        "конструкции": ["подзапрос", "оконная функция RANK", "JOIN"],
        "sql": """
            SELECT location, horizon_weeks, model, mae, mae_vs_naive
            FROM (
                SELECT l.location, f.horizon_weeks, f.model, f.mae, f.mae_vs_naive,
                       RANK() OVER (PARTITION BY f.iso_code, f.horizon_weeks ORDER BY f.mae) AS rnk
                FROM forecast_results f
                JOIN locations l ON l.iso_code = f.iso_code
            )
            WHERE rnk = 1
            ORDER BY location, horizon_weeks;
        """,
    },
    "vaccination_tiers": {
        "описание": "Страны по уровню вакцинации (CASE WHEN): средняя смертность 2022 г. и возраст в каждой группе",
        "конструкции": ["CASE WHEN", "CTE", "GROUP BY", "LEFT JOIN"],
        "sql": """
            WITH coverage AS (
                SELECT iso_code, MAX(people_fully_vaccinated_per_hundred) AS fully_vaccinated_pct
                FROM vaccinations WHERE date <= '2021-12-31' GROUP BY iso_code
            ),
            deaths_2022 AS (
                SELECT iso_code, SUM(new_deaths_per_million) AS deaths_per_million_2022
                FROM daily_stats WHERE date BETWEEN '2022-01-01' AND '2022-12-31' GROUP BY iso_code
            )
            SELECT CASE
                       WHEN cov.fully_vaccinated_pct IS NULL THEN '0. нет данных'
                       WHEN cov.fully_vaccinated_pct < 20 THEN '1. менее 20 %'
                       WHEN cov.fully_vaccinated_pct < 50 THEN '2. 20–50 %'
                       WHEN cov.fully_vaccinated_pct < 70 THEN '3. 50–70 %'
                       ELSE '4. 70 % и выше'
                   END AS vaccination_tier,
                   COUNT(*) AS countries,
                   ROUND(AVG(d.deaths_per_million_2022), 1) AS avg_deaths_per_million_2022,
                   ROUND(AVG(l.median_age), 1) AS avg_median_age,
                   ROUND(AVG(l.gdp_per_capita), 0) AS avg_gdp_per_capita
            FROM locations l
            LEFT JOIN coverage cov ON cov.iso_code = l.iso_code
            LEFT JOIN deaths_2022 d ON d.iso_code = l.iso_code
            WHERE l.is_aggregate = 0 AND l.population >= 1000000
            GROUP BY vaccination_tier
            ORDER BY vaccination_tier;
        """,
    },
    "countries_without_vaccination_data": {
        "описание": "Страны с данными о случаях, но без единой записи о вакцинации (анти-соединение NOT EXISTS)",
        "конструкции": ["NOT EXISTS", "JOIN"],
        "sql": """
            SELECT l.location, c.continent_name AS continent, l.population, l.n_days_reported
            FROM locations l
            JOIN continents c ON c.continent_id = l.continent_id
            WHERE l.is_aggregate = 0
              AND NOT EXISTS (SELECT 1 FROM vaccinations v WHERE v.iso_code = l.iso_code)
            ORDER BY l.population DESC;
        """,
    },
    "running_total_check": {
        "описание": "Проверка целостности: накопительная сумма new_cases (оконная SUM) против total_cases OWID (Казахстан)",
        "конструкции": ["оконная функция SUM ... OVER (ORDER BY)", "подзапрос"],
        "sql": """
            SELECT date, new_cases, total_cases,
                   running_total,
                   total_cases - running_total AS difference
            FROM (
                SELECT date, new_cases, total_cases,
                       SUM(new_cases) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total
                FROM daily_stats
                WHERE iso_code = 'KAZ'
            )
            WHERE date IN ('2020-12-31', '2021-12-31', '2022-12-31', '2023-12-31', '2024-05-13')
            ORDER BY date;
        """,
    },
    "tests_vs_cfr": {
        "описание": "Тестирование и наблюдаемая летальность: чем больше тестов на 1000 жителей, тем ниже CFR (страны ≥ 1 млн)",
        "конструкции": ["2 CTE", "JOIN 3 таблиц", "коррелированный подзапрос", "CASE WHEN", "GROUP BY"],
        "sql": """
            WITH last_tests AS (
                SELECT t.iso_code, MAX(t.total_tests) * 1000.0 / l.population AS tests_per_thousand
                FROM testing t
                JOIN locations l ON l.iso_code = t.iso_code
                WHERE l.population > 0
                GROUP BY t.iso_code
            ),
            totals AS (
                SELECT d.iso_code, d.total_cases, d.total_deaths
                FROM daily_stats d
                WHERE d.date = (SELECT MAX(date) FROM daily_stats WHERE iso_code = d.iso_code AND total_cases IS NOT NULL)
            )
            SELECT CASE
                       WHEN t.tests_per_thousand < 200 THEN '1. менее 200 тестов на 1000'
                       WHEN t.tests_per_thousand < 1000 THEN '2. 200–1000'
                       WHEN t.tests_per_thousand < 3000 THEN '3. 1000–3000'
                       ELSE '4. более 3000'
                   END AS testing_tier,
                   COUNT(*) AS countries,
                   ROUND(100.0 * SUM(tot.total_deaths) / SUM(tot.total_cases), 2) AS cfr_pct,
                   ROUND(AVG(l.median_age), 1) AS avg_median_age
            FROM last_tests t
            JOIN totals tot ON tot.iso_code = t.iso_code
            JOIN locations l ON l.iso_code = t.iso_code
            WHERE l.is_aggregate = 0 AND l.population >= 1000000
            GROUP BY testing_tier
            ORDER BY testing_tier;
        """,
    },
}

# SQL-запрос для сравнения Pandas vs SQL: недельные суммы по всем локациям прямо из дневной таблицы
WEEKLY_FROM_DAILY_SQL = """
    SELECT iso_code,
           date(date, '-' || ((strftime('%w', date) + 6) % 7) || ' days') AS week_start,
           SUM(new_cases)  AS new_cases,
           SUM(new_deaths) AS new_deaths
    FROM daily_stats
    GROUP BY iso_code, week_start
"""
