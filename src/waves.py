"""
Детекция волн заболеваемости — главный фокус темы 10.

Волна — период, когда недельная заболеваемость заметно выросла, достигла пика и спала.
Работаем с недельным рядом «новые случаи на миллион жителей» (сравним между странами),
обрезанным до периода, когда страна реально отчитывалась (NaN после прекращения отчётности
не превращаются в нули).

Два метода, которые сравниваются между собой (требование 9):
1. baseline «порог»: волна = отрезок не короче 4 недель, где сглаженный ряд выше ТИПИЧНОГО
   уровня страны (медианы ненулевых недельных значений за период анализа); разрывы ≤ 2 недель склеиваются;
2. «пики»: scipy.signal.find_peaks на логарифме ряда — пик считается волной, если до и после него
   заболеваемость менялась не менее чем в 2 раза (prominence >= ln 2) и пики разделены >= 8 неделями.
   Эпидемия делится на волны «от впадины до впадины»: граница между соседними пиками — минимум
   ряда между ними, поэтому каждая неделя принадлежит ровно одной волне, и случаи не считаются дважды.
"""
import numpy as np
import pandas as pd
from scipy.signal import find_peaks

import config
from src.utils import Timer, log


def smooth_weekly(series, window=config.WAVE_SMOOTH_WEEKS):
    """Центрированное скользящее среднее недельного ряда (сглаживает зубцы отчётности)."""
    return series.rolling(window, center=True, min_periods=1).mean()


def prepare_series(per_million):
    """Обрезает ряд до периода отчётности страны и заполняет одиночные пропуски внутри него нулями."""
    first, last = per_million.first_valid_index(), per_million.last_valid_index()
    if first is None:
        return per_million.iloc[0:0]
    return per_million.loc[first:last].fillna(0.0)


def classify_wave(peak_per_million):
    """Размер волны по пиковой недельной заболеваемости на миллион (if / elif / else)."""
    if peak_per_million >= 5000:
        return "крупная"
    elif peak_per_million >= 1000:
        return "средняя"
    else:
        return "малая"


def describe_wave(cases, deaths, smooth, start, end, death_lag=config.WAVE_DEATH_LAG_WEEKS):
    """
    Характеристики одной волны по индексам начала и конца (включительно).
    Смерти считаем со сдвигом death_lag недель: смерти отстают от заражений.
    """
    window = smooth.iloc[start:end + 1]
    peak_index = start + int(np.argmax(window.values))
    total_cases = float(cases.iloc[start:end + 1].sum())
    death_end = min(end + death_lag, len(deaths) - 1)
    total_deaths = float(deaths.iloc[start + death_lag:death_end + 1].sum())
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


def relative_threshold(per_million, start=config.ANALYSIS_START, end=config.ANALYSIS_END):
    """Порог baseline-метода: медиана ненулевых недельных значений страны за период анализа."""
    window = per_million.loc[start:end]
    nonzero = window[window > 0]
    if nonzero.empty:
        return None
    return float(nonzero.median())


def detect_waves_threshold(per_million, cases, deaths,
                           min_weeks=config.WAVE_MIN_WEEKS, max_gap=config.WAVE_MAX_GAP_WEEKS):
    """
    Метод 1 (baseline «порог»). Возвращает список словарей-волн.

    1) порог = медиана ненулевых недельных значений страны («типичный уровень»);
    2) отмечаем недели, где сглаженный ряд >= порога, и объединяем соседние недели в отрезки;
    3) склеиваем отрезки, между которыми разрыв <= max_gap недель;
    4) отбрасываем отрезки короче min_weeks.
    """
    threshold = relative_threshold(per_million)
    if threshold is None:
        return []
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
    Метод 2 («пики»). Возвращает список словарей-волн, которые не пересекаются.

    1) ищем пики на log(1 + ряд): prominence >= ln 2 означает, что до и после пика
       заболеваемость была как минимум вдвое ниже; distance — минимум недель между пиками;
    2) границы между соседними пиками — впадина (минимум сглаженного ряда) между ними;
    3) внешние границы первой и последней волны — где ряд опускается ниже boundary_share от пика.
    """
    smooth = smooth_weekly(per_million)
    values = smooth.to_numpy(dtype=float)
    peaks, _ = find_peaks(np.log1p(values), prominence=min_prominence,
                          distance=min_distance, height=np.log1p(min_peak))
    peaks = list(peaks)
    if not peaks:
        return []

    # впадины между соседними пиками
    troughs = []
    for left_peak, right_peak in zip(peaks[:-1], peaks[1:]):
        troughs.append(left_peak + int(np.argmin(values[left_peak:right_peak + 1])))

    waves = []
    for number, peak in enumerate(peaks):
        level = boundary_share * values[peak]
        if number == 0:                           # первая волна: идём влево, пока ряд >= 10 % пика
            start = peak
            while start > 0 and values[start - 1] >= level:
                start -= 1
        else:
            start = troughs[number - 1]           # после впадины начинается новая волна
        if number == len(peaks) - 1:              # последняя волна: идём вправо, пока ряд >= 10 % пика
            end = peak
            while end < len(values) - 1 and values[end + 1] >= level:
                end += 1
        else:
            end = troughs[number] - 1             # неделя впадины уже относится к следующей волне
        waves.append(describe_wave(cases, deaths, smooth, start, end))
    return waves


DETECTORS = {"threshold": detect_waves_threshold, "peaks": detect_waves_peaks}


def detect_waves_for_all(weekly, method="peaks", only_countries=True, **parameters):
    """
    Запускает выбранный метод для всех локаций недельной таблицы.
    Возвращает DataFrame волн (одна строка — одна волна) и время работы в секундах.
    """
    if method not in DETECTORS:
        raise ValueError(f"Неизвестный метод «{method}», доступны: {list(DETECTORS)}")
    detect = DETECTORS[method]

    data = weekly[~weekly["is_aggregate"]] if only_countries else weekly
    rows = []
    with Timer() as timer:
        for iso_code, group in data.groupby("iso_code", observed=True):
            group = group.set_index("week_start").sort_index()
            per_million = prepare_series(group["new_cases_per_million"])
            if len(per_million) < 10:             # нет населения или почти нет отчётов
                continue
            cases = group.loc[per_million.index, "new_cases"].fillna(0.0)
            deaths = group.loc[per_million.index, "new_deaths"].fillna(0.0)
            waves = detect(per_million, cases, deaths, **parameters)
            for number, wave in enumerate(waves, start=1):
                wave.update({"iso_code": iso_code, "location": group["location"].iloc[0],
                             "continent": group["continent"].iloc[0],
                             "population": float(group["population"].iloc[0]),
                             "method": method, "wave_no": number})
                rows.append(wave)
    result = pd.DataFrame(rows)
    log(f"Метод «{method}»: {len(result)} волн в {result['iso_code'].nunique() if len(result) else 0} странах "
        f"за {timer.seconds} с")
    return result, timer.seconds


def check_wave_coverage(waves, weekly):
    """
    Проверка корректности: сумма случаев по волнам страны не может превышать все случаи страны.
    Возвращает таблицу по странам с долей случаев, попавших в волны.
    """
    totals = (weekly[~weekly["is_aggregate"]].groupby("iso_code", observed=True)["new_cases"].sum())
    by_country = waves.groupby("iso_code", observed=True)["total_cases"].sum()
    coverage = pd.DataFrame({"cases_in_waves": by_country, "cases_total": totals.reindex(by_country.index)})
    coverage["share_in_waves"] = (coverage["cases_in_waves"] / coverage["cases_total"]).round(3)
    worst = float(coverage["share_in_waves"].max())
    if worst > 1.001:
        raise ValueError(f"Волны пересекаются: доля случаев в волнах {worst:.3f} > 1")
    return coverage.reset_index()


def match_peaks(waves_a, waves_b, tolerance_weeks=config.WAVE_MATCH_TOLERANCE_WEEKS):
    """
    Сопоставляет пики двух методов по странам (один к одному, окно ± tolerance_weeks).
    Возвращает таблицу: страна, число волн каждым методом, совпавших пиков, индекс Жаккара.
    """
    rows = []
    for iso_code in sorted(set(waves_a["iso_code"]) | set(waves_b["iso_code"])):
        peaks_a = sorted(pd.Timestamp(d) for d in waves_a.loc[waves_a["iso_code"] == iso_code, "peak_date"])
        peaks_b = sorted(pd.Timestamp(d) for d in waves_b.loc[waves_b["iso_code"] == iso_code, "peak_date"])
        unused_b = list(peaks_b)
        matched = 0
        for peak in peaks_a:                        # жадно: каждому пику A — ближайший свободный пик B
            candidates = [other for other in unused_b if abs((other - peak).days) <= 7 * tolerance_weeks]
            if candidates:
                closest = min(candidates, key=lambda other: abs((other - peak).days))
                unused_b.remove(closest)
                matched += 1
        union = len(peaks_a) + len(peaks_b) - matched
        rows.append({"iso_code": iso_code, "n_threshold": len(peaks_a), "n_peaks": len(peaks_b),
                     "matched": matched, "jaccard": round(matched / union, 3) if union else None})
    return pd.DataFrame(rows)


def compare_methods(waves_threshold, waves_peaks, seconds_threshold, seconds_peaks,
                    min_population=config.MIN_POPULATION_FOR_STATS):
    """
    Сравнивает два метода на странах с населением >= min_population:
    число волн на страну, медианная длительность, доля «волн» длиннее года (неправдоподобных),
    согласованность пиков (медиана индекса Жаккара) и время работы.
    """
    big_threshold = waves_threshold[waves_threshold["population"] >= min_population]
    big_peaks = waves_peaks[waves_peaks["population"] >= min_population]
    matching = match_peaks(big_threshold, big_peaks)

    def row(name, waves, seconds):
        countries = waves["iso_code"].nunique()
        recall, _ = reference_recall(waves)
        return {"метод": name, "волн_всего": int(len(waves)), "стран": int(countries),
                "найдено_эталонных_пиков_%": recall,
                "волн_на_страну": round(len(waves) / countries, 2) if countries else None,
                "медиана_длительности_нед": float(waves["duration_weeks"].median()),
                "доля_волн_длиннее_года_%": round(100 * float((waves["duration_weeks"] > 52).mean()), 1),
                "медиана_жаккара": round(float(matching["jaccard"].median()), 3),
                "доля_стран_жаккар_≥0.5_%": round(100 * float((matching["jaccard"] >= 0.5).mean()), 1),
                "время_с": seconds}

    summary = pd.DataFrame([row("порог (baseline)", big_threshold, seconds_threshold),
                            row("пики (find_peaks)", big_peaks, seconds_peaks)])
    return summary, matching


def reference_recall(waves, reference=config.REFERENCE_PEAKS,
                     tolerance_weeks=config.WAVE_MATCH_TOLERANCE_WEEKS):
    """
    Метрика качества: какая доля эталонных (известных из публикаций) пиков найдена методом
    в окне ± tolerance_weeks. Возвращает (доля в %, таблица по странам).
    """
    rows = []
    for location, months in reference.items():
        found_peaks = [pd.Timestamp(d) for d in waves.loc[waves["location"] == location, "peak_date"]]
        found = 0
        for month in months:
            target = pd.Timestamp(month + "-15")            # середина эталонного месяца
            if any(abs((peak - target).days) <= 7 * tolerance_weeks for peak in found_peaks):
                found += 1
        rows.append({"location": location, "эталонных_пиков": len(months), "найдено": found,
                     "всего_волн_методом": len(found_peaks)})
    table = pd.DataFrame(rows)
    recall = round(100 * table["найдено"].sum() / table["эталонных_пиков"].sum(), 1)
    return recall, table


def sensitivity_table(weekly, locations, factors=(1.0, 1.5, 2.0, 3.0)):
    """
    Чувствительность метода пиков к главному параметру: число волн у выбранных стран
    при prominence = ln 2 × factor (во сколько раз должна меняться заболеваемость вокруг пика).
    """
    rows = []
    subset = weekly[weekly["location"].isin(locations)]
    for factor in factors:
        waves, _ = detect_waves_for_all(subset, "peaks", min_prominence=config.WAVE_MIN_PROMINENCE * factor)
        counts = waves.groupby("location", observed=True).size() if len(waves) else pd.Series(dtype=int)
        row = {"множитель": factor, "prominence": round(config.WAVE_MIN_PROMINENCE * factor, 3),
               "рост_и_спад_не_менее_раз": round(2 ** factor, 1)}
        for location in locations:
            row[location] = int(counts.get(location, 0))
        rows.append(row)
    return pd.DataFrame(rows)
