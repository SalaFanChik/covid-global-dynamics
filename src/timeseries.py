"""
Временные ряды (задача кластера 2): недельная агрегация (resample),
скользящие средние и сезонная декомпозиция.

Почему неделя: с 2023 г. ВОЗ публикует случаи раз в неделю, поэтому дневные значения
содержат много структурных нулей. Недельная сумма одинаково корректна для всех лет.
"""
import pandas as pd
from statsmodels.tsa.seasonal import seasonal_decompose

import config
from src.utils import log

# Столбцы-константы по локации (одинаковы во всех строках страны)
LOCATION_INFO = ["location", "continent", "is_aggregate", "population"]


def country_series(df, location, column="new_cases"):
    """Дневной ряд одного показателя для одной локации (индекс — дата)."""
    rows = df[df["location"] == location]
    if rows.empty:
        raise ValueError(f"Локация «{location}» не найдена в данных")
    return rows.set_index("date")[column].sort_index()


def resample_series(series, freq="W-SUN", how="sum"):
    """
    Переводит дневной ряд в другую частоту: 'W-SUN' — недели (метка — воскресенье),
    'MS' — месяцы. how='sum' для счётчиков, 'mean' для долей и индексов.
    """
    if how == "sum":
        return series.resample(freq).sum()
    return series.resample(freq).mean()


def rolling_mean(series, window=7, center=False):
    """Скользящее среднее с окном window точек (сглаживает шум отчётности)."""
    return series.rolling(window=window, center=center, min_periods=1).mean()


def make_weekly_table(df):
    """
    Недельная таблица для всех локаций: сумма новых случаев и смертей за неделю (пн–вс),
    плюс показатели на миллион жителей. Это основной ряд для волн, прогноза и дашборда.

    Столбцы: iso_code, week_start (понедельник), new_cases, new_deaths, days,
             location, continent, is_aggregate, population,
             new_cases_per_million, new_deaths_per_million.
    """
    grouper = pd.Grouper(key="date", freq="W-SUN")      # неделя заканчивается в воскресенье
    weekly = (
        df.groupby(["iso_code", grouper], observed=True)
          .agg(new_cases=("new_cases", "sum"),
               new_deaths=("new_deaths", "sum"),
               days=("date", "size"))                     # сколько дней попало в неделю
          .reset_index()
    )
    weekly = weekly[weekly["days"] > 0].copy()           # пустые недели (их добавляет Grouper) убираем
    weekly["week_start"] = weekly["date"] - pd.Timedelta(days=6)   # понедельник той же недели
    weekly = weekly.drop(columns="date")

    # справочная информация по локации
    info = df.groupby("iso_code", observed=True)[LOCATION_INFO].first().reset_index()
    weekly = weekly.merge(info, on="iso_code", how="left")

    # показатели на миллион жителей (население постоянно внутри страны)
    weekly["new_cases_per_million"] = weekly["new_cases"] / weekly["population"] * 1_000_000
    weekly["new_deaths_per_million"] = weekly["new_deaths"] / weekly["population"] * 1_000_000

    columns = ["iso_code", "week_start", "new_cases", "new_deaths", "days"] + LOCATION_INFO + [
        "new_cases_per_million", "new_deaths_per_million"]
    weekly = weekly[columns].sort_values(["iso_code", "week_start"]).reset_index(drop=True)
    log(f"Недельная таблица: {len(weekly):,} строк, {weekly['iso_code'].nunique()} локаций")
    return weekly


def make_monthly_table(df):
    """Месячные случаи и смерти на миллион по локациям — для анимированной карты."""
    grouper = pd.Grouper(key="date", freq="MS")           # метка — первое число месяца
    monthly = (
        df.groupby(["iso_code", grouper], observed=True)
          .agg(new_cases=("new_cases", "sum"), new_deaths=("new_deaths", "sum"), days=("date", "size"))
          .reset_index()
          .rename(columns={"date": "month"})
    )
    monthly = monthly[monthly["days"] > 0].copy()
    info = df.groupby("iso_code", observed=True)[LOCATION_INFO].first().reset_index()
    monthly = monthly.merge(info, on="iso_code", how="left")
    monthly["new_cases_per_million"] = monthly["new_cases"] / monthly["population"] * 1_000_000
    monthly["new_deaths_per_million"] = monthly["new_deaths"] / monthly["population"] * 1_000_000
    return monthly


def weekly_series(weekly, location, column="new_cases_per_million"):
    """Недельный ряд одной локации из недельной таблицы (индекс — week_start)."""
    rows = weekly[weekly["location"] == location]
    if rows.empty:
        raise ValueError(f"Локация «{location}» не найдена в недельной таблице")
    return rows.set_index("week_start")[column].sort_index()


def decompose_series(series, period=config.SEASONAL_PERIOD_WEEKS, model="additive"):
    """
    Сезонная декомпозиция ряда (тренд + сезонность + остаток).
    Для недельного ряда period=52 — годовая сезонность; нужно не менее двух полных периодов.
    """
    series = series.dropna()
    if len(series) < 2 * period:
        raise ValueError(f"Для декомпозиции нужно минимум {2 * period} точек, есть {len(series)}")
    return seasonal_decompose(series, model=model, period=period)


def seasonality_strength(result):
    """
    Сила сезонности по формуле Хиндмана: 1 - Var(остаток) / Var(сезонность + остаток).
    Значение близко к 0 — сезонности почти нет, близко к 1 — сезонность сильная.
    """
    resid = result.resid.dropna()
    seasonal = result.seasonal.loc[resid.index]
    strength = 1 - resid.var() / (seasonal + resid).var()
    return round(float(max(0.0, strength)), 3)
