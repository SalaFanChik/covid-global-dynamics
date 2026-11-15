"""
Корреляционный анализ (задача кластера 2): связь итоговых показателей пандемии
со структурой населения, экономикой, системой здравоохранения и вакцинацией.

Единица анализа — страна (одна строка на страну из eda.country_summary),
агрегаты OWID исключены, микрогосударства отфильтрованы по населению.
"""
import numpy as np
import pandas as pd

import config
from src.utils import log, save_table

# Показатели для матрицы корреляций (понятные названия для графика)
CORRELATION_COLUMNS = {
    "total_cases_per_million": "случаев на млн",
    "total_deaths_per_million": "смертей на млн",
    "cfr_pct": "летальность, %",
    "people_fully_vaccinated_per_hundred": "полностью вакцинировано, %",
    "total_boosters_per_hundred": "бустеров на 100 чел.",
    "median_age": "медианный возраст",
    "life_expectancy": "ожидаемая продолжительность жизни",
    "gdp_per_capita": "ВВП на душу",
    "hospital_beds_per_thousand": "коек на 1000",
    "diabetes_prevalence": "диабет, %",
    "population_density": "плотность населения",
    "extreme_poverty": "крайняя бедность, %",
    "max_stringency_index": "макс. строгость мер",
    "total_tests_per_thousand": "тестов на 1000 (полнота учёта)",
    "excess_mortality_cumulative_per_million": "избыточная смертность на млн",
}


def prepare_country_table(summary, min_population=1_000_000):
    """Оставляет страны с населением не меньше min_population и нужные столбцы."""
    table = summary[summary["population"] >= min_population].copy()
    columns = [column for column in CORRELATION_COLUMNS if column in table.columns]
    return table[["location", "continent"] + columns]


def correlation_matrix(table, method="spearman"):
    """
    Матрица корреляций между показателями. По умолчанию Спирмен (ранговая):
    он устойчив к выбросам и не требует линейной связи — распределения здесь сильно скошены.
    """
    numeric = table.drop(columns=["location", "continent"])
    matrix = numeric.corr(method=method)
    return matrix.rename(index=CORRELATION_COLUMNS, columns=CORRELATION_COLUMNS).round(2)


def strongest_correlations(matrix, target="смертей на млн", top=6):
    """Самые сильные связи выбранного показателя с остальными (по модулю коэффициента)."""
    column = matrix[target].drop(target)
    ordered = column.reindex(column.abs().sort_values(ascending=False).index)
    return ordered.head(top)


def partial_correlation(x, y, control):
    """
    Частная корреляция Спирмена x и y «при прочих равных» по control:
    из x и y убираем линейную зависимость от control (остатки) и коррелируем остатки.
    """
    table = pd.DataFrame({"x": x, "y": y, "c": control}).dropna()
    if len(table) < 10:
        return None
    residual_x = table["x"] - np.polyval(np.polyfit(table["c"], table["x"], 1), table["c"])
    residual_y = table["y"] - np.polyval(np.polyfit(table["c"], table["y"], 1), table["c"])
    return round(float(residual_x.corr(residual_y, method="spearman")), 3)


def vaccination_vs_mortality(sql_vaccination_table):
    """
    Готовит данные «вакцинация к концу 2021 г. против смертности в 2022 г.» из SQL-запроса
    vaccination_vs_deaths_2022 и считает корреляции: общую, частную (с поправкой на медианный
    возраст) и внутри возрастных групп. Это межстрановая (экологическая) связь, а не причинность.
    """
    table = sql_vaccination_table.dropna(subset=["fully_vaccinated_pct", "deaths_per_million_2022"]).copy()
    table["fully_vaccinated_pct"] = table["fully_vaccinated_pct"].clip(upper=100)   # Гибралтар: 110 % (приезжие)
    overall = round(float(table["fully_vaccinated_pct"].corr(table["deaths_per_million_2022"], method="spearman")), 3)
    partial = partial_correlation(table["fully_vaccinated_pct"], table["deaths_per_million_2022"], table["median_age"])

    # возраст — главный смешивающий фактор: богатые «старые» страны и привиты лучше, и теряют больше пожилых.
    # Поэтому смотрим связь внутри возрастных групп (if / elif / else).
    def age_group(age):
        if pd.isna(age):
            return "нет данных"
        elif age < 25:
            return "молодые (<25)"
        elif age < 35:
            return "средние (25–35)"
        else:
            return "старые (35+)"

    table["age_group"] = table["median_age"].apply(age_group)
    by_group = (table[table["age_group"] != "нет данных"]
                .groupby("age_group")
                .apply(lambda part: pd.Series({
                    "стран": int(len(part)),
                    "корреляция_спирмена": round(part["fully_vaccinated_pct"].corr(
                        part["deaths_per_million_2022"], method="spearman"), 3)}), include_groups=False)
                .reset_index())
    by_group["стран"] = by_group["стран"].astype(int)
    return table, overall, partial, by_group


def run_correlation(summary, sql_vaccination_table=None):
    """Полный шаг: матрица корреляций, сильнейшие связи, вакцинация vs смертность. Сохраняет таблицы."""
    table = prepare_country_table(summary)
    matrix = correlation_matrix(table)
    save_table(matrix.reset_index().rename(columns={"index": "показатель"}), "correlation_matrix")
    strongest = strongest_correlations(matrix)
    results = {
        "стран_в_анализе": int(len(table)),
        "метод": "Spearman",
        "сильнейшие_связи_со_смертностью_на_млн": strongest.to_dict(),
    }
    log("Корреляции со смертностью на млн: " +
        ", ".join(f"{name}: {value}" for name, value in strongest.items()))

    if sql_vaccination_table is not None and len(sql_vaccination_table):
        vac_table, overall, partial, by_group = vaccination_vs_mortality(sql_vaccination_table)
        save_table(by_group, "vaccination_vs_deaths_by_age_group")
        results["вакцинация_vs_смертность_2022_общая"] = overall
        results["вакцинация_vs_смертность_2022_частная_при_контроле_возраста"] = partial
        results["вакцинация_vs_смертность_2022_по_возрасту"] = by_group.to_dict(orient="records")
        log(f"Вакцинация vs смертность 2022: общая {overall}, частная (возраст) {partial}; по группам: "
            + "; ".join(f"{row.age_group}: {row.корреляция_спирмена}" for row in by_group.itertuples()))
    # избыточная смертность — более честная мера потерь, чем официальная статистика
    excess = table.dropna(subset=["people_fully_vaccinated_per_hundred", "excess_mortality_cumulative_per_million"])
    if len(excess) >= 10:
        results["вакцинация_vs_избыточная_смертность"] = {
            "стран": int(len(excess)),
            "спирмен": round(float(excess["people_fully_vaccinated_per_hundred"].corr(
                excess["excess_mortality_cumulative_per_million"], method="spearman")), 3)}
    return results, matrix
