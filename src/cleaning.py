"""
Аудит качества данных и очистка (критерий 2: «полный аудит качества, обоснованная очистка»).

Каждый шаг очистки записывается в журнал — список словарей
{"шаг", "затронуто_строк", "обоснование"} — и сохраняется в JSON,
чтобы в отчёте можно было объяснить каждое решение.
"""
import pandas as pd

import config
from src.utils import log, save_json

# Счётчики новых случаев и смертей: пропуск означает «отчёта за этот день не было»
COUNT_COLUMNS = ["new_cases", "new_deaths"]


def audit_quality(df):
    """Собирает отчёт о качестве данных в виде словаря (ничего не меняет)."""
    report = {}
    report["строк"] = int(len(df))
    report["столбцов"] = int(df.shape[1])
    report["период"] = [str(df["date"].min().date()), str(df["date"].max().date())]
    report["локаций"] = int(df["location"].nunique())

    # пропуски
    missing_share = df.isna().mean().sort_values(ascending=False)
    report["доля_пропусков_по_столбцам"] = missing_share.round(4).to_dict()
    report["полностью_пустые_столбцы"] = missing_share[missing_share == 1.0].index.tolist()
    report["столбцов_с_пропусками_более_50%"] = int((missing_share > 0.5).sum())

    # ключ и дубликаты
    report["дубликаты_ключа_location_date"] = int(df.duplicated(["location", "date"]).sum())

    # отрицательные значения (их быть не должно)
    negatives = {}
    for column in ["new_cases", "new_deaths", "new_vaccinations", "new_tests"]:
        if column in df.columns:
            negatives[column] = int((df[column] < 0).sum())
    report["отрицательные_значения"] = negatives

    # нули: после 2023 г. отчётность недельная, поэтому нулей много
    report["доля_нулей_new_cases"] = round(float((df["new_cases"] == 0).mean()), 4)
    report["доля_нулей_new_deaths"] = round(float((df["new_deaths"] == 0).mean()), 4)

    # локации без ISO-кода / без континента / без населения
    report["локации_без_iso_code"] = sorted(df.loc[df["iso_code"].isna(), "location"].unique().tolist())
    report["локации_без_континента"] = sorted(df.loc[df["continent"].isna(), "location"].unique().tolist())
    report["локации_без_населения"] = sorted(df.loc[df["population"].isna(), "location"].unique().tolist())

    # покрытие отчётностью: когда каждая локация сообщала о случаях в первый и последний раз
    coverage = location_coverage(df)
    report["локации_без_данных_о_случаях"] = coverage.loc[coverage["n_days_reported"] == 0, "location"].tolist()
    last_date = df["date"].max()
    early_stop = coverage[(coverage["n_days_reported"] > 0) &
                          (coverage["last_report_date"] < last_date - pd.Timedelta(days=365))]
    report["локаций_прекративших_отчётность_более_года_назад"] = int(len(early_stop))

    # согласованность накопленных и новых случаев: total_cases.diff() должен совпадать с new_cases
    report["несогласованность_total_cases_и_new_cases"] = total_vs_new_mismatch(df)

    # полнота дат по локациям: сколько дней есть у каждой
    days_per_location = df.groupby("location", observed=True)["date"].agg(["min", "max", "count"])
    report["дней_на_локацию_мин"] = int(days_per_location["count"].min())
    report["дней_на_локацию_макс"] = int(days_per_location["count"].max())
    report["локаций_с_неполным_рядом"] = int((days_per_location["count"] < days_per_location["count"].max()).sum())

    log(f"Аудит: {report['строк']:,} строк, {report['локаций']} локаций, "
        f"пустых столбцов {len(report['полностью_пустые_столбцы'])}, "
        f"дубликатов {report['дубликаты_ключа_location_date']}, "
        f"локаций без ISO-кода {len(report['локации_без_iso_code'])}")
    return report


def location_coverage(df):
    """
    По каждой локации: дата первого и последнего отчёта о случаях и число дней с данными.
    NaN в new_cases означает «отчёта не было» (до первого случая или после прекращения отчётности).
    """
    reported = df[df["new_cases"].notna()]
    first_last = reported.groupby("iso_code", observed=True)["date"].agg(["min", "max", "count"])
    names = df.groupby("iso_code", observed=True)["location"].first()
    coverage = pd.DataFrame({"location": names})
    coverage["first_report_date"] = first_last["min"]          # NaT, если отчётов не было вовсе
    coverage["last_report_date"] = first_last["max"]
    coverage["n_days_reported"] = first_last["count"]           # выравнивается по iso_code, пропуски -> 0
    coverage["n_days_reported"] = coverage["n_days_reported"].fillna(0).astype(int)
    coverage = coverage.reset_index()
    coverage["location"] = coverage["location"].astype(str)
    coverage["iso_code"] = coverage["iso_code"].astype(str)
    return coverage


def total_vs_new_mismatch(df, start="2020-01-01", end="2022-12-31"):
    """
    Сколько строк, где прирост накопленных случаев не совпадает с new_cases (на дневной отчётности
    2020–2022). Небольшое число расхождений — ретроспективные правки статистики, а не ошибка загрузки.
    """
    part = df[(df["date"] >= start) & (df["date"] <= end) & df["continent"].notna()]
    part = part.sort_values(["iso_code", "date"])
    diff = part.groupby("iso_code", observed=True)["total_cases"].diff()
    both = diff.notna() & part["new_cases"].notna()
    mismatch = (diff[both] - part.loc[both, "new_cases"]).abs() > 0.5
    return {"строк_проверено": int(both.sum()), "расхождений": int(mismatch.sum())}


def clean_data(df):
    """
    Очищает данные. Возвращает (очищенный DataFrame, журнал шагов).

    Решения:
    1. полностью пустые столбцы удаляем;
    2. локации без ISO-кода (части Великобритании, группы по доходу, «мир без Китая», Олимпиады)
       удаляем — это не страны и не официальные агрегаты OWID;
    3. агрегаты OWID (World, континенты, ЕС) оставляем, но помечаем флагом is_aggregate,
       чтобы не смешивать их со странами в статистике;
    4. пропуски в new_cases/new_deaths НЕ заполняем нулями: NaN означает «отчёта не было»
       (до первого случая или после того, как страна перестала отчитываться), а ноль —
       «отчёт был, новых случаев нет». Подмена NaN нулями исказила бы прогноз и графики;
    5. локации, у которых нет ни одного отчёта о случаях, удаляем;
    6. отрицательные значения (корректировки статистики) обрезаем до нуля;
    7. сортируем по локации и дате, добавляем столбец year.
    """
    steps = []

    def note(step, rows, why):
        steps.append({"шаг": step, "затронуто_строк": int(rows), "обоснование": why})
        log(f"  {step}: {int(rows):,} строк")

    # 1. пустые столбцы
    empty_columns = [column for column in df.columns if df[column].isna().all()]
    if empty_columns:
        df = df.drop(columns=empty_columns)
        note("удалены пустые столбцы: " + ", ".join(empty_columns), len(df),
             "столбцы не содержат ни одного значения")

    # 2. локации без ISO-кода
    no_code = df["iso_code"].isna()
    removed = sorted(df.loc[no_code, "location"].unique().tolist())
    df = df[~no_code].copy()
    note("удалены локации без ISO-кода", no_code.sum(),
         "не страны и не официальные агрегаты OWID: " + ", ".join(removed))

    # 3. флаг агрегатов (у агрегатов нет континента)
    df["is_aggregate"] = df["continent"].isna()
    note("помечены агрегаты OWID (is_aggregate=True)", df["is_aggregate"].sum(),
         "World, континенты и ЕС нужны для графиков, но не должны попадать в статистику по странам")

    # 4. пропуски в счётчиках оставляем как есть (это информация, а не ошибка)
    for column in COUNT_COLUMNS:
        note(f"пропуски {column} сохранены как NaN", df[column].isna().sum(),
             "NaN = отчёта не было (до первого случая или после прекращения отчётности), 0 = случаев не было")

    # 5. локации без единого отчёта о случаях
    coverage = location_coverage(df)
    no_data = coverage.loc[coverage["n_days_reported"] == 0, "iso_code"].tolist()
    rows_no_data = df["iso_code"].isin(no_data)
    df = df[~rows_no_data].copy()
    note("удалены локации без данных о случаях", rows_no_data.sum(),
         "ни одного отчёта о случаях (ВОЗ не публикует данные): " +
         ", ".join(coverage.loc[coverage["iso_code"].isin(no_data), "location"]))

    # 6. отрицательные значения -> 0
    for column in COUNT_COLUMNS:
        negative = df[column] < 0
        if negative.any():
            df.loc[negative, column] = 0
        note(f"отрицательные {column} обрезаны до 0", negative.sum(),
             "отрицательные значения — это задним числом исправленная статистика, а не реальные случаи")

    # 7. порядок строк и вспомогательные столбцы
    df = df.sort_values(["location", "date"]).reset_index(drop=True)
    df["year"] = df["date"].dt.year.astype("int16")
    # category после фильтрации хранит удалённые значения -> убираем лишние категории
    for column in ["iso_code", "location", "continent"]:
        df[column] = df[column].cat.remove_unused_categories()
    note("сортировка по (location, date), добавлен столбец year", len(df),
         "год нужен для фильтров дашборда и SQL-запросов")

    return df, steps


def write_audit_txt(report, steps, path):
    """Пишет короткий текстовый отчёт об аудите и очистке (требование 5: запись в .txt)."""
    lines = ["ОТЧЁТ ОБ АУДИТЕ КАЧЕСТВА ДАННЫХ OWID COVID-19", ""]
    lines.append(f"Строк: {report['строк']:,}, столбцов: {report['столбцов']}, локаций: {report['локаций']}")
    lines.append(f"Период: {report['период'][0]} — {report['период'][1]}")
    lines.append(f"Дубликатов ключа (location, date): {report['дубликаты_ключа_location_date']}")
    lines.append(f"Отрицательные значения: {report['отрицательные_значения']}")
    lines.append(f"Доля нулей в new_cases: {report['доля_нулей_new_cases']:.1%} (недельная отчётность с 2023 г.)")
    lines.append(f"Полностью пустые столбцы: {report['полностью_пустые_столбцы']}")
    lines.append(f"Локации без ISO-кода: {report['локации_без_iso_code']}")
    lines.append(f"Локации без данных о случаях: {report['локации_без_данных_о_случаях']}")
    lines.append(f"Локаций, прекративших отчётность более года назад: "
                 f"{report['локаций_прекративших_отчётность_более_года_назад']}")
    lines.append(f"Проверка total_cases.diff() == new_cases (2020–2022): {report['несогласованность_total_cases_и_new_cases']}")
    lines.append("")
    lines.append("Шаги очистки:")
    for number, step in enumerate(steps, start=1):
        lines.append(f"{number}. {step['шаг']} — {step['затронуто_строк']:,} строк. {step['обоснование']}")
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines))
    return path


def run_audit_and_clean(df):
    """Полный шаг: аудит -> очистка -> сохранение журнала и текстового отчёта."""
    report = audit_quality(df)
    clean_df, steps = clean_data(df)
    save_json(report, config.TABLES_DIR / "data_audit.json")
    save_json(steps, config.TABLES_DIR / "cleaning_log.json")
    write_audit_txt(report, steps, config.TABLES_DIR / "data_audit.txt")
    location_coverage(clean_df).to_csv(config.TABLES_DIR / "location_coverage.csv", index=False)
    log(f"После очистки: {len(clean_df):,} строк, {clean_df['location'].nunique()} локаций")
    return clean_df, report, steps
