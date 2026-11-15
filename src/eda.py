"""
Разведочный анализ данных (EDA, задача кластера 2):
типы признаков, описательная статистика, пропуски, выбросы по правилу IQR,
а также сводная таблица по странам, которая дальше используется
для корреляций, boxplot и SQL-запросов.
"""
import pandas as pd

import config
from src.utils import log, save_table

# Показатели, по которым считаем описательную статистику
KEY_METRICS = [
    "new_cases", "new_deaths", "new_cases_per_million", "new_deaths_per_million",
    "total_cases_per_million", "total_deaths_per_million", "reproduction_rate",
    "stringency_index", "people_fully_vaccinated_per_hundred", "positive_rate",
]

# Постоянные характеристики стран (одинаковы во всех строках страны)
STATIC_COLUMNS = [
    "population", "population_density", "median_age", "life_expectancy", "gdp_per_capita",
    "extreme_poverty", "diabetes_prevalence", "handwashing_facilities", "hospital_beds_per_thousand",
]


def feature_types(df):
    """Группирует столбцы по типу признака: числовые, категориальные, дата, логические."""
    types = {"числовые": [], "категориальные": [], "дата": [], "логические": []}
    for column in df.columns:
        dtype = df[column].dtype
        if isinstance(dtype, pd.CategoricalDtype) or pd.api.types.is_string_dtype(dtype):
            types["категориальные"].append(column)
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            types["дата"].append(column)
        elif pd.api.types.is_bool_dtype(dtype):
            types["логические"].append(column)
        elif pd.api.types.is_numeric_dtype(dtype):
            types["числовые"].append(column)
    return types


def describe_numeric(df, columns=KEY_METRICS):
    """Описательная статистика по ключевым показателям (только страны, без агрегатов)."""
    countries = df[~df["is_aggregate"]]
    present = [column for column in columns if column in countries.columns]
    table = countries[present].describe().T
    table["доля_пропусков"] = countries[present].isna().mean().round(3)
    return table.round(3).reset_index().rename(columns={"index": "показатель"})


def missing_report(df):
    """Таблица пропусков по столбцам: число и доля, по убыванию."""
    missing = df.isna().sum()
    table = pd.DataFrame({
        "столбец": missing.index,
        "пропусков": missing.values,
        "доля": (missing.values / len(df)).round(4),
    })
    return table.sort_values("пропусков", ascending=False).reset_index(drop=True)


def iqr_bounds(series):
    """Границы выбросов по правилу IQR: [Q1 - 1.5*IQR, Q3 + 1.5*IQR]."""
    q1 = series.quantile(0.25)
    q3 = series.quantile(0.75)
    iqr = q3 - q1
    return q1 - 1.5 * iqr, q3 + 1.5 * iqr


def detect_outliers_iqr(table, column, group=None):
    """
    Находит выбросы в столбце column по правилу IQR.
    Если задан group (например, 'continent'), границы считаются внутри каждой группы.
    Возвращает (таблица выбросов, сводка по группам).
    """
    outliers = []
    summary = []
    groups = table.groupby(group, observed=True) if group else [("все", table)]
    for name, part in groups:
        values = part[column].dropna()
        if len(values) < 4:            # слишком мало наблюдений для квартилей
            continue
        lower, upper = iqr_bounds(values)
        mask = (part[column] < lower) | (part[column] > upper)
        outliers.append(part[mask])
        summary.append({"группа": name, "наблюдений": len(values), "нижняя_граница": round(lower, 1),
                        "верхняя_граница": round(upper, 1), "выбросов": int(mask.sum())})
    outliers_table = pd.concat(outliers) if outliers else table.iloc[0:0]
    return outliers_table, pd.DataFrame(summary)


def country_summary(df):
    """
    Одна строка на страну: итоговые показатели пандемии + постоянные характеристики.
    Итоги берём на последнюю дату (total_* — накопленные), вакцинацию — максимум за период.
    """
    countries = df[~df["is_aggregate"]].sort_values(["iso_code", "date"])
    last = countries.groupby("iso_code", observed=True).last()      # последняя строка каждой страны
    maxima = countries.groupby("iso_code", observed=True)[
        ["people_fully_vaccinated_per_hundred", "total_boosters_per_hundred", "stringency_index",
         "total_tests_per_thousand"]].max()
    # last() берёт последнее НЕпустое значение в группе — удобно для редких показателей
    last_excess = countries.groupby("iso_code", observed=True)["excess_mortality_cumulative_per_million"].last()

    summary = pd.DataFrame({
        "location": last["location"],
        "continent": last["continent"],
        "total_cases": last["total_cases"],
        "total_deaths": last["total_deaths"],
        "total_cases_per_million": last["total_cases_per_million"],
        "total_deaths_per_million": last["total_deaths_per_million"],
        "people_fully_vaccinated_per_hundred": maxima["people_fully_vaccinated_per_hundred"],
        "total_boosters_per_hundred": maxima["total_boosters_per_hundred"],
        "max_stringency_index": maxima["stringency_index"],
        "total_tests_per_thousand": maxima["total_tests_per_thousand"],
        "excess_mortality_cumulative_per_million": last_excess,
    })
    for column in STATIC_COLUMNS:
        summary[column] = last[column]
    # летальность (case fatality rate): смерти / случаи
    summary["cfr_pct"] = (100 * summary["total_deaths"] / summary["total_cases"]).round(3)
    summary = summary.reset_index()
    for column in ["location", "continent"]:
        summary[column] = summary[column].astype(str)   # category -> обычные строки
    return summary


def run_eda(df):
    """Полный шаг EDA: таблицы сохраняются в outputs/tables, ключевые числа возвращаются словарём."""
    types = feature_types(df)
    describe = describe_numeric(df)
    missing = missing_report(df)
    summary = country_summary(df)
    outliers, outlier_summary = detect_outliers_iqr(summary, "total_deaths_per_million", group="continent")

    save_table(describe, "eda_describe")
    save_table(missing, "eda_missing")
    save_table(summary, "country_summary")
    save_table(outliers[["location", "continent", "total_deaths_per_million"]], "eda_outliers_deaths")
    save_table(outlier_summary, "eda_outliers_summary")

    results = {
        "типы_признаков": {name: len(columns) for name, columns in types.items()},
        "стран_в_сводке": int(len(summary)),
        "столбцов_с_пропусками": int((missing["пропусков"] > 0).sum()),
        "выбросов_смертность_на_млн": int(len(outliers)),
        "страны_выбросы": outliers.sort_values("total_deaths_per_million", ascending=False)["location"].head(10).tolist(),
    }
    log(f"EDA: {results['стран_в_сводке']} стран, выбросов по смертности {results['выбросов_смертность_на_млн']} "
        f"({', '.join(results['страны_выбросы'][:5])}...)")
    return results, summary
