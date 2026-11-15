"""
Мини-дашборд Dash (требование 7, задача кластера 2).

Фильтры: диапазон лет, континент, страны, показатель.
Все данные читаются из SQLite (data/covid.db) параметризованными SQL-запросами —
дашборд не трогает исходный CSV и стартует за секунды.

Запуск из корня проекта:  python dashboard/app.py   ->  http://127.0.0.1:8050
"""
import socket
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, dash_table, dcc, html

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # чтобы работал import config / src
import config                                                   # noqa: E402
from src.sql_queries import QUERIES                             # noqa: E402

# Показатели, доступные в фильтре: столбец weekly_stats -> подпись
METRICS = {
    "new_cases_per_million": "случаев на млн жителей",
    "new_deaths_per_million": "смертей на млн жителей",
}
CONTINENT_COLORS = {"Africa": "#2a78d6", "Asia": "#eb6834", "Europe": "#1baf7a",
                    "North America": "#eda100", "Oceania": "#e87ba4", "South America": "#008300"}
LINE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
DEFAULT_COUNTRIES = ["Kazakhstan", "Germany", "United States", "Brazil"]
YEARS = list(range(2020, 2027))


# ---------- доступ к базе ----------

def query(sql, params=()):
    """Выполняет SQL-запрос к базе проекта и возвращает DataFrame."""
    if not config.DB_PATH.exists():
        raise FileNotFoundError(f"Нет базы {config.DB_PATH}: выполните «python main.py --all» или шаг 5 меню")
    with closing(sqlite3.connect(config.DB_PATH)) as connection:     # closing закрывает соединение после запроса
        return pd.read_sql_query(sql, connection, params=params)


def load_locations():
    """Справочник стран (без агрегатов) с континентом и датой последнего отчёта о случаях."""
    return query("""
        SELECT l.iso_code, l.location, c.continent_name AS continent, l.population, l.last_report_date
        FROM locations l JOIN continents c ON c.continent_id = l.continent_id
        WHERE l.is_aggregate = 0
        ORDER BY l.location
    """)


def placeholders(values):
    """Строка '?, ?, ?' для списка значений (безопасная подстановка в IN)."""
    return ", ".join("?" for _ in values)


def weekly_for(iso_codes, year_from, year_to):
    """Недельные ряды выбранных стран за выбранные годы."""
    if not iso_codes:
        return pd.DataFrame(columns=["iso_code", "week_start", *METRICS])
    sql = f"""
        SELECT w.iso_code, l.location, w.week_start, w.new_cases_per_million, w.new_deaths_per_million
        FROM weekly_stats w JOIN locations l ON l.iso_code = w.iso_code
        WHERE w.iso_code IN ({placeholders(iso_codes)})
          AND w.week_start BETWEEN ? AND ?
        ORDER BY w.iso_code, w.week_start
    """
    table = query(sql, (*iso_codes, f"{year_from}-01-01", f"{year_to}-12-31"))
    table["week_start"] = pd.to_datetime(table["week_start"])
    return table


def waves_for(iso_code, year_from, year_to):
    """Волны (метод пиков) одной страны, пересекающиеся с выбранными годами."""
    return query("""
        SELECT wave_no, start_date, peak_date, end_date, duration_weeks,
               peak_weekly_cases_per_million, total_cases, total_deaths, cfr_pct, size
        FROM waves
        WHERE method = 'peaks' AND iso_code = ? AND end_date >= ? AND start_date <= ?
        ORDER BY wave_no
    """, (iso_code, f"{year_from}-01-01", f"{year_to}-12-31"))


def totals_by_country(metric, year_from, year_to, continent):
    """Сумма показателя по странам за период (для карты и топ-10)."""
    if metric not in METRICS:              # имя столбца подставляется в текст SQL, поэтому только из белого списка
        raise ValueError(f"неизвестный показатель {metric}")
    continent_filter = "" if continent == "Все" else "AND c.continent_name = ?"
    params = [config.MIN_POPULATION_FOR_STATS, f"{year_from}-01-01", f"{year_to}-12-31"]
    if continent != "Все":
        params.append(continent)
    return query(f"""
        SELECT l.iso_code, l.location, c.continent_name AS continent, SUM(w.{metric}) AS value
        FROM weekly_stats w
        JOIN locations  l ON l.iso_code = w.iso_code
        JOIN continents c ON c.continent_id = l.continent_id
        WHERE l.is_aggregate = 0 AND l.population >= ? AND w.week_start BETWEEN ? AND ? {continent_filter}
        GROUP BY l.iso_code
        ORDER BY value DESC
    """, params)


# ---------- графики ----------

def empty_figure(text):
    """Пустой график с подсказкой (когда ничего не выбрано)."""
    fig = go.Figure()
    fig.add_annotation(text=text, showarrow=False, font={"size": 14})
    fig.update_layout(template="plotly_white", xaxis={"visible": False}, yaxis={"visible": False})
    return fig


def figure_weekly(table, metric, waves, first_country, last_report=None):
    """Линейный график недельных рядов; волны первой выбранной страны закрашены."""
    if table.empty:
        return empty_figure("Выберите хотя бы одну страну")
    fig = go.Figure()
    for number, (location, part) in enumerate(table.groupby("location", sort=False)):
        fig.add_trace(go.Scatter(x=part["week_start"], y=part[metric], mode="lines", name=location,
                                 line={"width": 2, "color": LINE_COLORS[number % len(LINE_COLORS)]},
                                 hovertemplate="%{x|%d.%m.%Y}: %{y:,.1f}<extra>" + location + "</extra>"))
    for wave in waves.itertuples():
        fig.add_vrect(x0=wave.start_date, x1=wave.end_date, fillcolor="#eb6834", opacity=0.12, line_width=0)
        fig.add_vline(x=pd.Timestamp(wave.peak_date).timestamp() * 1000, line_dash="dot",
                      line_color="#eb6834", opacity=0.6)
    title = f"Недельная динамика: {METRICS[metric]}"
    if len(waves):
        title += f" (закрашены {len(waves)} волн — {first_country})"
    if last_report:
        title += f"<br><sup>{first_country}: данные о случаях до {last_report}; разрыв линии = отчёта не было</sup>"
    fig.update_layout(title=title, template="plotly_white", hovermode="x unified",
                      yaxis_title=METRICS[metric], xaxis_title="неделя", legend={"orientation": "h", "y": -0.2},
                      margin={"l": 40, "r": 20, "t": 60, "b": 40})
    return fig


def figure_map(table, metric, year_from, year_to):
    """Карта-хороплет: сумма показателя за период."""
    if table.empty:
        return empty_figure("Нет данных для карты")
    upper = float(table["value"].quantile(0.95))
    fig = px.choropleth(table, locations="iso_code", color="value", hover_name="location",
                        color_continuous_scale=["#cde2fb", "#6da7ec", "#256abf", "#0d366b"],
                        range_color=(0, upper), labels={"value": METRICS[metric]})
    fig.update_layout(title=f"{METRICS[metric].capitalize()} за {year_from}–{year_to} гг.",
                      template="plotly_white", margin={"l": 0, "r": 0, "t": 50, "b": 0},
                      geo={"showframe": False, "projection_type": "natural earth"})
    return fig


def figure_top(table, metric, continent):
    """Топ-10 стран по показателю."""
    if table.empty:
        return empty_figure("Нет данных")
    top = table.head(10).sort_values("value")
    fig = go.Figure(go.Bar(x=top["value"], y=top["location"], orientation="h",
                           marker_color=[CONTINENT_COLORS.get(c, "gray") for c in top["continent"]],
                           hovertemplate="%{y}: %{x:,.0f}<extra></extra>"))
    fig.update_layout(title=f"Топ-10: {METRICS[metric]} ({continent}, страны ≥ 1 млн)", template="plotly_white",
                      xaxis_title=METRICS[metric], margin={"l": 40, "r": 20, "t": 50, "b": 40})
    return fig


def figure_vaccination(continent):
    """Вакцинация к концу 2021 г. против смертности в 2022 г. (данные SQL-запроса из каталога)."""
    table = query(QUERIES["vaccination_vs_deaths_2022"]["sql"])
    table = table.dropna(subset=["fully_vaccinated_pct", "deaths_per_million_2022"])
    table = table[table["deaths_per_million_2022"] > 0]
    if continent != "Все":
        table = table[table["continent"] == continent]
    if table.empty:
        return empty_figure("Нет данных о вакцинации для этого фильтра")
    fig = px.scatter(table, x="fully_vaccinated_pct", y="deaths_per_million_2022", color="continent",
                     hover_name="location", hover_data={"median_age": ":.1f"}, log_y=True,
                     color_discrete_map=CONTINENT_COLORS,
                     labels={"fully_vaccinated_pct": "полностью вакцинировано к концу 2021 г., %",
                             "deaths_per_million_2022": "смертей на млн в 2022 г. (лог.)",
                             "continent": "континент", "median_age": "медианный возраст"})
    fig.update_traces(marker={"size": 9, "opacity": 0.8, "line": {"width": 1, "color": "white"}})
    fig.update_layout(title="Вакцинация и смертность в 2022 г. (страны > 1 млн)", template="plotly_white",
                      margin={"l": 40, "r": 20, "t": 50, "b": 40})
    return fig


# ---------- приложение ----------

def build_app():
    """Собирает приложение Dash: макет и callback-функции."""
    locations = load_locations()
    continents = ["Все"] + sorted(locations["continent"].unique().tolist())
    name_to_iso = dict(zip(locations["location"], locations["iso_code"]))   # словарь страна -> ISO-код
    last_report = dict(zip(locations["location"], locations["last_report_date"]))

    app = Dash(__name__, title="COVID-19 Global Dynamics")
    app.layout = html.Div(style={"fontFamily": "Arial, sans-serif", "margin": "0 24px"}, children=[
        html.H2("COVID-19 Global Dynamics — волны, вакцинация и смертность"),
        html.P("Источник: Our World in Data (compact dataset), 2020-01-01 — 2026-10-18. "
               "Данные читаются из SQLite SQL-запросами. Проект ADA-2602M: Serikov Alikhan, Mashina Veronika.",
               style={"color": "#52514e"}),

        html.Div(style={"display": "flex", "gap": "24px", "flexWrap": "wrap", "alignItems": "flex-end"}, children=[
            html.Div(style={"flex": "2", "minWidth": "280px"}, children=[
                html.Label("Годы"),
                dcc.RangeSlider(id="years", min=YEARS[0], max=YEARS[-1], step=1, value=[2020, 2023],
                                marks={year: str(year) for year in YEARS}),
            ]),
            html.Div(style={"flex": "1", "minWidth": "180px"}, children=[
                html.Label("Континент"),
                dcc.Dropdown(id="continent", options=continents, value="Все", clearable=False),
            ]),
            html.Div(style={"flex": "2", "minWidth": "280px"}, children=[
                html.Label("Страны"),
                dcc.Dropdown(id="countries", options=locations["location"].tolist(),
                             value=DEFAULT_COUNTRIES, multi=True),
            ]),
            html.Div(style={"flex": "1", "minWidth": "200px"}, children=[
                html.Label("Показатель"),
                dcc.RadioItems(id="metric", options=[{"label": label, "value": value}
                                                     for value, label in METRICS.items()],
                               value="new_cases_per_million"),
            ]),
        ]),

        dcc.Graph(id="weekly-graph", style={"height": "420px"}),
        html.Div(style={"display": "flex", "gap": "16px", "flexWrap": "wrap"}, children=[
            dcc.Graph(id="map-graph", style={"flex": "3", "minWidth": "400px", "height": "420px"}),
            dcc.Graph(id="top-graph", style={"flex": "2", "minWidth": "320px", "height": "420px"}),
        ]),
        html.Div(style={"display": "flex", "gap": "16px", "flexWrap": "wrap"}, children=[
            dcc.Graph(id="vaccination-graph", style={"flex": "3", "minWidth": "400px", "height": "420px"}),
            html.Div(style={"flex": "2", "minWidth": "320px"}, children=[
                html.H4(id="waves-title"),
                dash_table.DataTable(id="waves-table", page_size=8,
                                     style_cell={"fontSize": "12px", "padding": "4px", "textAlign": "center"},
                                     style_header={"fontWeight": "bold"}),
            ]),
        ]),
    ])

    @app.callback(Output("countries", "options"), Output("countries", "value"),
                  Input("continent", "value"), Input("countries", "value"))
    def update_country_options(continent, selected):
        """Список стран зависит от континента; уже выбранные страны вне континента убираются."""
        if continent == "Все":
            available = locations["location"].tolist()
        else:
            available = locations.loc[locations["continent"] == continent, "location"].tolist()
        kept = [country for country in (selected or []) if country in available]
        if not kept:
            kept = available[:3] if continent != "Все" else DEFAULT_COUNTRIES
        return available, kept

    @app.callback(Output("weekly-graph", "figure"), Output("map-graph", "figure"), Output("top-graph", "figure"),
                  Output("vaccination-graph", "figure"), Output("waves-table", "data"),
                  Output("waves-table", "columns"), Output("waves-title", "children"),
                  Input("years", "value"), Input("continent", "value"),
                  Input("countries", "value"), Input("metric", "value"))
    def update_figures(years, continent, countries, metric):
        """Главный callback: любой фильтр перестраивает все графики."""
        year_from, year_to = years
        countries = countries or []
        iso_codes = [name_to_iso[name] for name in countries if name in name_to_iso]

        try:
            weekly = weekly_for(iso_codes, year_from, year_to)
            waves = waves_for(iso_codes[0], year_from, year_to) if iso_codes else pd.DataFrame()
            totals = totals_by_country(metric, year_from, year_to, continent)
            vaccination_figure = figure_vaccination(continent)
        except (sqlite3.Error, pd.errors.DatabaseError, ValueError) as error:   # база недоступна или ошибка в запросе
            message = empty_figure(f"Ошибка базы данных: {error}")
            return message, message, message, message, [], [], "Волны"
        first_country = countries[0] if countries else ""

        table_columns = [{"name": name, "id": column} for column, name in {
            "wave_no": "№", "start_date": "начало", "peak_date": "пик", "end_date": "конец",
            "duration_weeks": "недель", "peak_weekly_cases_per_million": "пик, на млн/нед.",
            "cfr_pct": "CFR, %", "size": "размер"}.items()]
        waves_title = f"Волны: {first_country} ({len(waves)})" if first_country else "Волны"
        return (figure_weekly(weekly, metric, waves, first_country, last_report.get(first_country)),
                figure_map(totals, metric, year_from, year_to),
                figure_top(totals, metric, continent),
                vaccination_figure,
                waves.to_dict("records"), table_columns, waves_title)

    return app


def run_dashboard(host=config.DASH_HOST, port=config.DASH_PORT, debug=False):
    """Запускает сервер дашборда. Занятый порт — частая ошибка на демонстрации, поэтому проверяем его заранее."""
    with socket.socket() as probe:                 # Flask при занятом порте завершил бы программу своим сообщением
        if probe.connect_ex((host, port)) == 0:
            print(f"Порт {port} занят: остановите прошлый экземпляр дашборда или измените DASH_PORT в config.py")
            return
    app = build_app()
    print(f"Дашборд запущен: http://{host}:{port}  (остановить — Ctrl+C)")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    try:
        run_dashboard()
    except FileNotFoundError as error:
        print(error)
        sys.exit(1)
