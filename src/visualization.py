"""
Визуализации (задача кластера 2: 4–5 графиков, из них не менее одного интерактивного).

Статические графики — matplotlib/seaborn (PNG в outputs/figures),
интерактивные — Plotly (HTML в outputs/figures). Все подписи на русском языке.
Цвета берутся из фиксированной палитры, безопасной для дальтоников: цвет закреплён
за сущностью (континентом, моделью), а не за порядком появления на графике.
"""
import matplotlib
matplotlib.use("Agg")                      # рисуем в файл, без окон
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import seaborn as sns
from matplotlib.colors import LinearSegmentedColormap

import config
from src.timeseries import weekly_series
from src.utils import log
from src.waves import prepare_series, relative_threshold

# Категориальная палитра (фиксированный порядок)
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
CONTINENT_COLORS = {
    "Africa": PALETTE[0], "Asia": PALETTE[1], "Europe": PALETTE[2],
    "North America": PALETTE[3], "Oceania": PALETTE[4], "South America": PALETTE[5],
}
MODEL_COLORS = {"naive": PALETTE[0], "seasonal_naive": PALETTE[1],
                "moving_average": PALETTE[2], "linear_regression": PALETTE[3]}
MODEL_NAMES = {"naive": "naive", "seasonal_naive": "seasonal naive",
               "moving_average": "скользящее среднее", "linear_regression": "линейная регрессия"}
# Расходящаяся шкала для корреляций: синий — отрицательная, серый — ноль, красный — положительная
DIVERGING = LinearSegmentedColormap.from_list("diverging", ["#2a78d6", "#f0efec", "#e34948"])
SEQUENTIAL_BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

sns.set_theme(style="whitegrid", font_scale=0.95)
plt.rcParams["axes.titleweight"] = "bold"


SOURCE_NOTE = "Источник: Our World in Data, COVID-19 dataset (CC BY 4.0), срез до 2026-10-18; расчёты авторов"


def save_figure(fig, name):
    """Сохраняет фигуру matplotlib в outputs/figures/<name>.png (с подписью источника внизу)."""
    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.FIGURES_DIR / f"{name}.png"
    fig.text(0.01, 0.005, SOURCE_NOTE, fontsize=7, color="#52514e", ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log(f"  график сохранён: {path.name}")
    return path


def save_html(fig, name):
    """Сохраняет интерактивную фигуру Plotly в outputs/figures/<name>.html."""
    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.FIGURES_DIR / f"{name}.html"
    fig.write_html(path, include_plotlyjs="cdn")
    log(f"  интерактивный график сохранён: {path.name}")
    return path


# ---------- 1. Волны ----------

def shade_waves(ax, waves, color, label):
    """Закрашивает периоды волн на оси и отмечает пики."""
    for number, wave in enumerate(waves.itertuples()):
        ax.axvspan(pd.Timestamp(wave.start_date), pd.Timestamp(wave.end_date), color=color, alpha=0.15,
                   label=label if number == 0 else None)
        ax.axvline(pd.Timestamp(wave.peak_date), color=color, alpha=0.6, linewidth=1, linestyle="--")


WAVES_XLIM = (pd.Timestamp("2020-01-01"), pd.Timestamp("2024-01-01"))   # после 2023 г. ряды почти нулевые


def plot_waves_small_multiples(weekly, waves, locations, name="waves_small_multiples"):
    """Недельные случаи на миллион с закрашенными волнами для нескольких стран (2×2)."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 8), sharex=True)
    for ax, location in zip(axes.ravel(), locations):
        series = weekly_series(weekly, location)
        ax.plot(series.index, series.values, color=PALETTE[0], linewidth=1.8, label="случаев на млн в неделю")
        country_waves = waves[waves["location"] == location]
        shade_waves(ax, country_waves, PALETTE[1], "волна (метод пиков)")
        ax.set_title(f"{location}: {len(country_waves)} волн")
        ax.set_ylabel("случаев на млн / нед.")
        ax.set_xlim(WAVES_XLIM)
    axes[0, 0].legend(loc="upper left", fontsize=8)
    fig.suptitle("Волны заболеваемости COVID-19 (недельные случаи на миллион жителей)", fontsize=13)
    return save_figure(fig, name)


def plot_wave_methods(weekly, waves_threshold, waves_peaks, location, name="wave_methods_comparison"):
    """Сравнение двух методов детекции волн на одной стране: порог (baseline) и пики."""
    series = weekly_series(weekly, location)
    threshold = relative_threshold(prepare_series(series))      # медиана ненулевых недель страны
    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    pairs = [(axes[0], waves_threshold, "порог (baseline)", PALETTE[6]),
             (axes[1], waves_peaks, "пики find_peaks", PALETTE[1])]
    for ax, waves, title, color in pairs:
        ax.plot(series.index, series.values, color=PALETTE[0], linewidth=1.8)
        country_waves = waves[waves["location"] == location]
        shade_waves(ax, country_waves, color, f"волна: {title}")
        if threshold is not None and title.startswith("порог"):
            ax.axhline(threshold, color="gray", linewidth=1, linestyle=":",
                       label=f"порог = медиана ненулевых недель: {threshold:.0f} на млн")
        ax.set_title(f"{location} — метод «{title}»: {len(country_waves)} волн")
        ax.set_ylabel("случаев на млн / нед.")
        ax.set_xlim(WAVES_XLIM)
        ax.legend(loc="upper left", fontsize=8)
    return save_figure(fig, name)


# ---------- 2. Выбросы ----------

def plot_deaths_boxplot(summary, name="deaths_boxplot_by_continent"):
    """Boxplot смертности на миллион по континентам; подписаны самые сильные выбросы."""
    table = summary.dropna(subset=["total_deaths_per_million", "continent"])
    order = sorted(table["continent"].unique())
    fig, ax = plt.subplots(figsize=(12, 6))
    sns.boxplot(data=table, x="continent", y="total_deaths_per_million", order=order, hue="continent",
                palette=CONTINENT_COLORS, legend=False, width=0.55, fliersize=0, ax=ax, boxprops={"alpha": 0.5})
    sns.stripplot(data=table, x="continent", y="total_deaths_per_million", order=order, hue="continent",
                  palette=CONTINENT_COLORS, legend=False, size=4, alpha=0.8, ax=ax)
    # подписываем 2 верхних значения в каждом континенте
    for position, continent in enumerate(order):
        top = table[table["continent"] == continent].nlargest(2, "total_deaths_per_million")
        for row in top.itertuples():
            ax.annotate(row.location, (position, row.total_deaths_per_million), fontsize=8,
                        xytext=(6, 0), textcoords="offset points")
    ax.set_title("Накопленная смертность от COVID-19 на миллион жителей по континентам (выбросы по правилу IQR)")
    ax.set_xlabel("")
    ax.set_ylabel("смертей на млн жителей")
    return save_figure(fig, name)


# ---------- 3. Корреляции ----------

def plot_correlation_heatmap(matrix, name="correlation_heatmap"):
    """Тепловая карта матрицы корреляций (Спирмен) между страновыми показателями."""
    fig, ax = plt.subplots(figsize=(12, 9))
    mask = np.triu(np.ones_like(matrix, dtype=bool), k=1)     # показываем только нижний треугольник
    sns.heatmap(matrix, mask=mask, annot=True, fmt=".2f", cmap=DIVERGING, vmin=-1, vmax=1, center=0,
                square=True, linewidths=0.5, cbar_kws={"shrink": 0.7, "label": "коэффициент Спирмена"},
                annot_kws={"size": 8}, ax=ax)
    ax.set_title("Корреляции страновых показателей (страны с населением ≥ 1 млн)")
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    return save_figure(fig, name)


# ---------- 4. Декомпозиция ----------

def plot_decomposition(result, title, period_label="период 52 недели", name="seasonal_decomposition_world"):
    """Четыре панели: исходный ряд, тренд, сезонность, остаток."""
    parts = [("исходный ряд", result.observed), ("тренд", result.trend),
             (f"сезонность ({period_label})", result.seasonal), ("остаток", result.resid)]
    fig, axes = plt.subplots(4, 1, figsize=(13, 9), sharex=True)
    for ax, (label, series) in zip(axes, parts):
        ax.plot(series.index, series.values, color=PALETTE[0], linewidth=1.5)
        ax.set_ylabel(label, fontsize=9)
    axes[0].set_title(title)
    return save_figure(fig, name)


def plot_weekday_effect(effect, location, name="weekday_effect"):
    """Столбцы: на сколько процентов регистрация случаев в каждый день недели отличается от среднего."""
    days = list(effect.keys())
    values = list(effect.values())
    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = [PALETTE[0] if value >= 0 else PALETTE[1] for value in values]
    ax.bar(days, values, color=colors, width=0.6)
    ax.axhline(0, color="black", linewidth=0.8)
    for position, value in enumerate(values):
        ax.text(position, value + (1.5 if value >= 0 else -4), f"{value:+.0f} %", ha="center", fontsize=9)
    ax.set_ylabel("отклонение от среднего уровня, %")
    ax.set_title(f"{location}: эффект дня недели в регистрации случаев (сезонная компонента, период 7 дней)")
    return save_figure(fig, name)


# ---------- 5. Прогноз ----------

def plot_forecast(forecasts, metrics, location, horizon=1, name="forecast_comparison"):
    """Слева: факт и прогнозы моделей на horizon недель вперёд; справа: MAE моделей по горизонтам."""
    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(15, 5.5), gridspec_kw={"width_ratios": [2, 1]})
    part = forecasts[(forecasts["location"] == location) & (forecasts["horizon_weeks"] == horizon)]
    actual = part[part["model"] == "naive"].set_index("target_week")["actual"]
    ax_left.plot(actual.index, actual.values, color="black", linewidth=2.2, label="факт")
    for model, color in MODEL_COLORS.items():
        rows = part[part["model"] == model].set_index("target_week")["forecast"]
        ax_left.plot(rows.index, rows.values, color=color, linewidth=1.3, alpha=0.9, label=MODEL_NAMES[model])
    ax_left.set_title(f"{location}: прогноз на {horizon} нед. вперёд (rolling origin)")
    ax_left.set_ylabel("случаев на млн / нед.")
    ax_left.legend(fontsize=8)

    table = metrics[metrics["location"] == location].pivot(index="model", columns="horizon_weeks", values="mae")
    table = table.reindex(list(MODEL_COLORS))
    positions = np.arange(len(table))
    width = 0.38
    for shift, horizon_weeks in zip([-width / 2, width / 2], table.columns):
        ax_right.bar(positions + shift, table[horizon_weeks], width, label=f"горизонт {horizon_weeks} нед.",
                     color=[MODEL_COLORS[m] for m in table.index], alpha=0.95 if shift < 0 else 0.5)
    ax_right.set_xticks(positions)
    ax_right.set_xticklabels([MODEL_NAMES[m] for m in table.index], rotation=20, ha="right", fontsize=8)
    ax_right.set_ylabel("MAE, случаев на млн / нед.")
    ax_right.set_title("Ошибка MAE по моделям")
    ax_right.legend(fontsize=8)
    return save_figure(fig, name)


# ---------- 6. Результаты SQL ----------

def plot_top_deaths(sql_table, name="top15_deaths_per_million"):
    """Горизонтальные столбцы: топ-15 стран по смертности на миллион (результат SQL-запроса)."""
    table = sql_table.sort_values("total_deaths_per_million")
    fig, ax = plt.subplots(figsize=(10, 6.5))
    ax.barh(table["location"], table["total_deaths_per_million"],
            color=[CONTINENT_COLORS.get(c, "gray") for c in table["continent"]])
    for position, value in enumerate(table["total_deaths_per_million"]):
        ax.text(value + 40, position, f"{value:,.0f}", va="center", fontsize=8)
    handles = [plt.Rectangle((0, 0), 1, 1, color=color) for continent, color in CONTINENT_COLORS.items()
               if continent in set(table["continent"])]
    labels = [continent for continent in CONTINENT_COLORS if continent in set(table["continent"])]
    ax.legend(handles, labels, loc="lower right", fontsize=8, title="континент")
    ax.set_xlabel("смертей на млн жителей (накопленно)")
    ax.set_title("Топ-15 стран по смертности от COVID-19 на миллион жителей")
    return save_figure(fig, name)


def plot_cfr_by_wave_year(sql_table, name="cfr_by_wave_year"):
    """Средняя летальность волн по году пика: эффект вакцинации и смены штаммов."""
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(sql_table["peak_year"].astype(str), sql_table["avg_cfr_pct"], color=PALETTE[0], width=0.6)
    for position, row in enumerate(sql_table.itertuples()):
        ax.text(position, row.avg_cfr_pct + 0.03, f"{row.avg_cfr_pct:.2f} %\n({row.waves} волн)",
                ha="center", fontsize=8)
    ax.set_ylabel("средняя летальность волны (CFR), %")
    ax.set_xlabel("год пика волны")
    ax.set_title("Летальность волн снижалась от года к году (результат SQL-запроса cfr_by_wave_year)")
    return save_figure(fig, name)


# ---------- 7. Интерактивные графики Plotly ----------

def plotly_weekly_lines(weekly, locations, name="interactive_weekly_cases"):
    """Интерактивный линейный график недельных случаев/смертей на млн с переключателем метрики."""
    fig = go.Figure()
    metrics = [("new_cases_per_million", "случаев на млн / нед."), ("new_deaths_per_million", "смертей на млн / нед.")]
    for metric_number, (column, label) in enumerate(metrics):
        for location_number, location in enumerate(locations):
            series = weekly_series(weekly, location, column)
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, name=location, mode="lines",
                line={"color": PALETTE[location_number % len(PALETTE)], "width": 1.8},
                visible=(metric_number == 0),
                hovertemplate=f"{location}<br>%{{x|%d.%m.%Y}}<br>{label}: %{{y:,.1f}}<extra></extra>"))
    n = len(locations)
    buttons = []
    for metric_number, (column, label) in enumerate(metrics):
        visible = [i // n == metric_number for i in range(2 * n)]
        buttons.append({"label": label, "method": "update",
                        "args": [{"visible": visible}, {"yaxis": {"title": label}}]})
    fig.update_layout(
        title="COVID-19: недельная динамика по странам (наведите курсор, выделите период, выключите страну в легенде)",
        xaxis_title="неделя", yaxis_title=metrics[0][1], template="plotly_white", hovermode="x unified",
        updatemenus=[{"buttons": buttons, "direction": "down", "x": 0.0, "xanchor": "left", "y": 1.12, "yanchor": "top"}],
        legend={"orientation": "h", "y": -0.2})
    return save_html(fig, name)


def plotly_choropleth(monthly, name="interactive_map_monthly_cases"):
    """Анимированная карта: месячные случаи на миллион по странам, кадр — месяц."""
    table = monthly[~monthly["is_aggregate"]].copy()
    table["month"] = table["month"].dt.strftime("%Y-%m")
    table["iso_code"] = table["iso_code"].astype(str)
    table["location"] = table["location"].astype(str)
    upper = float(table["new_cases_per_million"].quantile(0.99))   # обрезаем шкалу, иначе всё бледное
    fig = px.choropleth(
        table, locations="iso_code", color="new_cases_per_million", hover_name="location",
        animation_frame="month", color_continuous_scale=SEQUENTIAL_BLUES, range_color=(0, upper),
        labels={"new_cases_per_million": "случаев на млн за месяц", "month": "месяц"},
        title="COVID-19: новые случаи на миллион жителей по месяцам (анимация)")
    fig.update_layout(template="plotly_white", margin={"l": 10, "r": 10, "t": 60, "b": 10})
    return save_html(fig, name)


def plotly_vaccination_scatter(vac_table, name="interactive_vaccination_vs_deaths"):
    """Интерактивная диаграмма рассеяния: вакцинация к концу 2021 г. против смертности в 2022 г."""
    table = vac_table.dropna(subset=["fully_vaccinated_pct", "deaths_per_million_2022"]).copy()
    table = table[table["deaths_per_million_2022"] > 0]
    fig = px.scatter(
        table, x="fully_vaccinated_pct", y="deaths_per_million_2022", color="continent",
        hover_name="location", hover_data={"median_age": ":.1f", "gdp_per_capita": ":,.0f"},
        color_discrete_map=CONTINENT_COLORS, log_y=True,
        labels={"fully_vaccinated_pct": "полностью вакцинировано к 31.12.2021, %",
                "deaths_per_million_2022": "смертей на млн за 2022 г. (лог. шкала)", "continent": "континент",
                "median_age": "медианный возраст", "gdp_per_capita": "ВВП на душу"},
        title="Вакцинация и смертность в 2022 г.: страны с населением более 1 млн")
    fig.update_traces(marker={"size": 9, "opacity": 0.8, "line": {"width": 1, "color": "white"}})
    fig.update_layout(template="plotly_white")
    return save_html(fig, name)
