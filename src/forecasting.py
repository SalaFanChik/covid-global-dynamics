"""
Прогноз недельной заболеваемости и сравнение с baseline (задача кластера 2, требование 9).

Протокол «скользящее начало» (rolling origin): для каждой недели t из периода оценки модель
видит только данные до t включительно и прогнозирует неделю t+h. Так исключена утечка данных.

Модели:
- naive (baseline): прогноз = последнее известное значение;
- seasonal_naive (baseline): прогноз = значение 52 недели назад;
- moving_average: среднее последних 4 недель;
- linear_regression: линейная регрессия на 4 лагах, переобучается в каждой точке t.
Метрика — MAE (средняя абсолютная ошибка) в случаях на миллион жителей в неделю.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

import config
from src.timeseries import weekly_series
from src.utils import Timer, log


def naive_forecast(history, horizon):
    """Последнее известное значение."""
    return float(history.iloc[-1])


def seasonal_naive_forecast(history, horizon, period=config.SEASONAL_PERIOD_WEEKS):
    """Значение ровно год (52 недели) назад относительно прогнозируемой недели."""
    position = len(history) - 1 + horizon - period
    if position < 0:                      # истории меньше года — откат к naive
        return naive_forecast(history, horizon)
    return float(history.iloc[position])


def moving_average_forecast(history, horizon, window=config.MA_WINDOW):
    """Среднее последних window недель."""
    return float(history.iloc[-window:].mean())


def linear_regression_forecast(history, horizon, n_lags=config.N_LAGS):
    """
    Линейная регрессия: y[s] ~ y[s-h], y[s-h-1], ..., y[s-h-n_lags+1] (прямой прогноз на горизонт h).
    Обучающая выборка строится только из истории до текущего момента.
    """
    values = history.to_numpy(dtype=float)
    rows, targets = [], []
    for s in range(n_lags + horizon - 1, len(values)):
        lags = values[s - horizon - n_lags + 1: s - horizon + 1]   # n_lags значений до момента s-h включительно
        rows.append(lags[::-1])                                       # от самого свежего к старому
        targets.append(values[s])
    if len(rows) < 10:                    # мало данных для обучения — откат к naive
        return naive_forecast(history, horizon)
    model = LinearRegression()
    model.fit(np.array(rows), np.array(targets))
    last_lags = values[-n_lags:][::-1]
    prediction = float(model.predict(last_lags.reshape(1, -1))[0])
    return max(prediction, 0.0)           # заболеваемость не бывает отрицательной


# Словарь моделей: имя -> функция (history, horizon) -> прогноз
MODELS = {
    "naive": naive_forecast,
    "seasonal_naive": seasonal_naive_forecast,
    "moving_average": moving_average_forecast,
    "linear_regression": linear_regression_forecast,
}
BASELINES = ["naive", "seasonal_naive"]


def rolling_origin_evaluation(series, start=config.FORECAST_START, end=config.FORECAST_END,
                              horizons=config.FORECAST_HORIZONS, models=MODELS):
    """
    Оценивает все модели на одном ряду. Возвращает (таблица метрик, таблица прогнозов).
    """
    series = series.sort_index().astype(float)
    positions = np.where((series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end)))[0]
    metrics, forecasts = [], []
    for name, model in models.items():
        errors = {h: [] for h in horizons}
        with Timer() as timer:
            for t in positions:
                history = series.iloc[: t + 1]              # данные только до момента t
                for h in horizons:
                    if t + h >= len(series):
                        continue
                    actual = float(series.iloc[t + h])
                    predicted = model(history, h)
                    errors[h].append(abs(actual - predicted))
                    forecasts.append({"model": name, "horizon_weeks": h, "origin": series.index[t],
                                      "target_week": series.index[t + h], "actual": actual, "forecast": predicted})
        for h in horizons:
            metrics.append({"model": name, "horizon_weeks": h, "n_forecasts": len(errors[h]),
                            "mae": round(float(np.mean(errors[h])), 2) if errors[h] else None,
                            "seconds": timer.seconds})
    metrics = pd.DataFrame(metrics)
    # MAE относительно naive: < 1 — модель лучше baseline, > 1 — хуже
    naive_mae = metrics[metrics["model"] == "naive"].set_index("horizon_weeks")["mae"]
    metrics["mae_vs_naive"] = (metrics["mae"] / metrics["horizon_weeks"].map(naive_mae)).round(3)
    metrics["is_baseline"] = metrics["model"].isin(BASELINES)
    return metrics, pd.DataFrame(forecasts)


def evaluate_locations(weekly, locations, column="new_cases_per_million"):
    """Запускает оценку для списка локаций. Возвращает (метрики по всем, прогнозы по всем)."""
    all_metrics, all_forecasts = [], []
    for location in locations:
        try:
            series = weekly_series(weekly, location, column)
        except ValueError as error:
            log(f"  пропуск: {error}")
            continue
        metrics, forecasts = rolling_origin_evaluation(series)
        metrics.insert(0, "location", location)
        forecasts.insert(0, "location", location)
        all_metrics.append(metrics)
        all_forecasts.append(forecasts)
        best = metrics.loc[metrics["mae"].idxmin()]
        log(f"  {location}: лучшая модель {best['model']} (h={best['horizon_weeks']}, MAE={best['mae']})")
    return pd.concat(all_metrics, ignore_index=True), pd.concat(all_forecasts, ignore_index=True)


def summarize_models(metrics):
    """Сводка по моделям: средний MAE и среднее отношение к naive по всем локациям и горизонтам."""
    summary = (metrics.groupby(["model", "horizon_weeks"])
               .agg(mae_mean=("mae", "mean"), mae_vs_naive_mean=("mae_vs_naive", "mean"),
                    wins=("mae", "size"), seconds_total=("seconds", "sum"))
               .reset_index())
    # сколько раз модель оказалась лучшей для (локация, горизонт)
    best = metrics.loc[metrics.groupby(["location", "horizon_weeks"])["mae"].idxmin()]
    win_counts = best.groupby(["model", "horizon_weeks"]).size()
    summary["wins"] = [int(win_counts.get((row.model, row.horizon_weeks), 0)) for row in summary.itertuples()]
    return summary.round(3)


def conclusion(summary):
    """Короткий текстовый вывод по сравнению моделей (if / elif / else по отношению к baseline)."""
    lines = []
    for row in summary.itertuples():
        ratio = row.mae_vs_naive_mean
        if row.model in BASELINES:
            verdict = "baseline"
        elif ratio < 0.95:
            verdict = f"лучше naive на {round(100 * (1 - ratio))} %"
        elif ratio <= 1.05:
            verdict = "примерно как naive"
        else:
            verdict = f"хуже naive на {round(100 * (ratio - 1))} %"
        lines.append(f"{row.model}, горизонт {row.horizon_weeks} нед.: MAE={row.mae_mean}, {verdict}, побед {row.wins}")
    return lines
