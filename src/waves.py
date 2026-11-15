"""
Детекция волн заболеваемости — главный фокус темы 10.

Волна — период, когда недельная заболеваемость заметно выросла, достигла пика и спала.
Работаем с недельным рядом «новые случаи на миллион жителей» (сравним между странами).

Реализованы два метода, которые сравниваются между собой (требование 9):
1. baseline «порог»: волна = отрезок, где сглаженный ряд держится выше фиксированного порога
   не меньше заданного числа недель (простое правило, но зависит от уровня тестирования);
2. «пики»: scipy.signal.find_peaks на логарифме ряда — пик считается волной, если ряд вокруг него
   вырос и упал не менее чем в 2 раза (prominence >= ln 2); границы волны — где ряд опускается
   ниже 10 % от пика (или впадина между соседними пиками).
"""
import numpy as np
import pandas as pd
from scipy.signal import find_peaks

import config
from src.utils import Timer, log


def smooth_weekly(series, window=config.WAVE_SMOOTH_WEEKS):
    """Центрированное скользящее среднее недельного ряда (сглаживает зубцы отчётности)."""
    return series.rolling(window, center=True, min_periods=1).mean()


def classify_wave(peak_per_million):
    """Размер волны по пиковой недельной заболеваемости на миллион (if / elif / else)."""
    if peak_per_million >= 5000:
        return "крупная"
    elif peak_per_million >= 1000:
        return "средняя"
    else:
        return "малая"


def describe_wave(cases, deaths, smooth, start, end):
    """Собирает словарь с характеристиками одной волны по индексам начала и конца (включительно)."""
    window = smooth.iloc[start:end + 1]
    peak_position = int(np.argmax(window.values))
    peak_index = start + peak_position
    total_cases = float(cases.iloc[start:end + 1].sum())
    total_deaths = float(deaths.iloc[start:end + 1].sum())
    return {
        "start_date": smooth.index[start].date(),
        "peak_date": smooth.index[peak_index].date(),
        "end_date": smooth.index[end].date(),
        "duration_weeks": int(end - start + 1),
        "peak_weekly_cases_per_million": round(float(smooth.iloc[peak_index]), 1),
        "total_cases": total_cases,
        "total_deaths": total_deaths,
        "cfr_pct": round(100 * total_deaths / total_cases, 2) if total_cases > 0 else None,
        "size": classify_wave(float(smooth.iloc[peak_index])),
    }


def detect_waves_threshold(per_million, cases, deaths,
                           threshold=config.WAVE_THRESHOLD_PER_MILLION,
                           min_weeks=config.WAVE_MIN_WEEKS,
                           max_gap=config.WAVE_MAX_GAP_WEEKS):
    """
    Метод 1 (baseline «порог»). Возвращает список словарей-волн.

    Шаги: 1) отмечаем недели, где сглаженный ряд >= порога;
          2) объединяем соседние отмеченные недели в отрезки;
          3) склеиваем отрезки, между которыми разрыв <= max_gap недель;
          4) отбрасываем отрезки короче min_weeks.
    """
    smooth = smooth_weekly(per_million)
    above = (smooth >= threshold).tolist()

    segments = []           # список [начало, конец] в индексах
    start = None
    for position, flag in enumerate(above):
        if flag and start is None:
            start = position                      # началась новая серия недель выше порога
        elif not flag and start is not None:
            segments.append([start, position - 1])
            start = None
    if start is not None:                         # ряд закончился выше порога
        segments.append([start, len(above) - 1])

    merged = []
    for segment in segments:
        if merged and segment[0] - merged[-1][1] - 1 <= max_gap:
            merged[-1][1] = segment[1]            # короткий разрыв — это та же волна
        else:
            merged.append(segment)

    waves = []
    for start, end in merged:
        if end - start + 1 >= min_weeks:
            waves.append(describe_wave(cases, deaths, smooth, start, end))
    return waves


def detect_waves_peaks(per_million, cases, deaths,
                       min_prominence=config.WAVE_MIN_PROMINENCE,
                       min_distance=config.WAVE_MIN_DISTANCE_WEEKS,
                       min_peak=config.WAVE_MIN_PEAK_PER_MILLION,
                       boundary_share=config.WAVE_BOUNDARY_SHARE):
    """
    Метод 2 («пики»). Возвращает список словарей-волн.

    1) ищем пики на log(1 + ряд): prominence >= ln 2 означает, что до и после пика
       заболеваемость была как минимум вдвое ниже; distance — минимум недель между пиками;
    2) границы волны: от пика идём влево и вправо, пока ряд >= boundary_share * пик,
       но не дальше впадины между соседними пиками.
    """
    smooth = smooth_weekly(per_million)
    values = smooth.to_numpy(dtype=float)
    log_values = np.log1p(values)
    peaks, _ = find_peaks(log_values, prominence=min_prominence,
                          distance=min_distance, height=np.log1p(min_peak))
    peaks = list(peaks)

    waves = []
    for number, peak in enumerate(peaks):
        # впадины между соседними пиками ограничивают волну
        left_limit = 0 if number == 0 else peaks[number - 1] + int(np.argmin(values[peaks[number - 1]:peak + 1]))
        right_limit = len(values) - 1 if number == len(peaks) - 1 else peak + int(np.argmin(values[peak:peaks[number + 1] + 1]))
        level = boundary_share * values[peak]

        start = peak
        while start > left_limit and values[start - 1] >= level:
            start -= 1
        end = peak
        while end < right_limit and values[end + 1] >= level:
            end += 1
        waves.append(describe_wave(cases, deaths, smooth, start, end))
    return waves


def detect_waves_for_all(weekly, method="peaks", only_countries=True):
    """
    Запускает выбранный метод для всех локаций недельной таблицы.
    Возвращает DataFrame волн (одна строка — одна волна) и время работы в секундах.
    """
    detectors = {"threshold": detect_waves_threshold, "peaks": detect_waves_peaks}
    if method not in detectors:
        raise ValueError(f"Неизвестный метод «{method}», доступны: {list(detectors)}")
    detect = detectors[method]

    data = weekly[~weekly["is_aggregate"]] if only_countries else weekly
    rows = []
    with Timer() as timer:
        for iso_code, group in data.groupby("iso_code", observed=True):
            group = group.set_index("week_start").sort_index()
            per_million = group["new_cases_per_million"]
            if per_million.isna().all():          # нет населения -> нет показателя на млн
                continue
            waves = detect(per_million.fillna(0), group["new_cases"], group["new_deaths"])
            for number, wave in enumerate(waves, start=1):
                wave.update({"iso_code": iso_code, "location": group["location"].iloc[0],
                             "continent": group["continent"].iloc[0], "method": method, "wave_no": number})
                rows.append(wave)
    result = pd.DataFrame(rows)
    log(f"Метод «{method}»: {len(result)} волн в {result['iso_code'].nunique() if len(result) else 0} странах "
        f"за {timer.seconds} с")
    return result, timer.seconds


def compare_methods(waves_threshold, waves_peaks, seconds_threshold, seconds_peaks, tolerance_weeks=4):
    """
    Сравнивает два метода: число волн, медианная длительность, время работы и согласованность —
    доля волн baseline-метода, у которых пик совпадает с пиком метода «пики» (± tolerance_weeks).
    """
    matched = 0
    for _, wave in waves_threshold.iterrows():
        peaks_same_country = waves_peaks.loc[waves_peaks["iso_code"] == wave["iso_code"], "peak_date"]
        for peak_date in peaks_same_country:
            if abs((pd.Timestamp(peak_date) - pd.Timestamp(wave["peak_date"])).days) <= 7 * tolerance_weeks:
                matched += 1
                break
    agreement = round(100 * matched / len(waves_threshold), 1) if len(waves_threshold) else None

    summary = pd.DataFrame([
        {"метод": "порог (baseline)", "волн_всего": len(waves_threshold),
         "стран_с_волнами": waves_threshold["iso_code"].nunique(),
         "волн_на_страну": round(len(waves_threshold) / max(waves_threshold["iso_code"].nunique(), 1), 2),
         "медиана_длительности_нед": float(waves_threshold["duration_weeks"].median()),
         "время_с": seconds_threshold},
        {"метод": "пики (find_peaks)", "волн_всего": len(waves_peaks),
         "стран_с_волнами": waves_peaks["iso_code"].nunique(),
         "волн_на_страну": round(len(waves_peaks) / max(waves_peaks["iso_code"].nunique(), 1), 2),
         "медиана_длительности_нед": float(waves_peaks["duration_weeks"].median()),
         "время_с": seconds_peaks},
    ])
    return summary, agreement
