"""
Рисует ER-диаграмму базы data/covid.db (требование 10).

Схема читается прямо из SQLite (PRAGMA table_info / foreign_key_list), поэтому диаграмма
всегда соответствует реальной базе. Результат: docs/erd.png.
Запуск из корня проекта:  python scripts/make_erd.py
"""
import sqlite3
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

# Колонки диаграммы: таблицы раскладываются сверху вниз, locations — в центре,
# чтобы линии связей не пересекали другие таблицы
COLUMNS = {
    "left":   {"x": 0.4,  "width": 4.3, "tables": ["vaccinations", "hospitalizations", "testing"]},
    "center": {"x": 6.4,  "width": 3.8, "tables": ["continents", "locations", "waves"]},
    "right":  {"x": 12.1, "width": 4.3, "tables": ["daily_stats", "weekly_stats", "excess_mortality", "forecast_results"]},
}
CANVAS_WIDTH, CANVAS_HEIGHT, GAP = 16.8, 14.2, 0.55
ROW_HEIGHT = 0.27
HEADER_HEIGHT = 0.42
COLORS = {"reference": "#cde2fb", "fact": "#e6f4ee", "result": "#fdebd3"}
KIND = {"continents": "reference", "locations": "reference", "waves": "result", "forecast_results": "result"}


def box_height(columns):
    """Высота прямоугольника таблицы по числу столбцов."""
    return HEADER_HEIGHT + ROW_HEIGHT * len(columns) + 0.15


def compute_layout(schema):
    """Координаты (x, y, width) каждой таблицы: колонки центрируются по вертикали."""
    layout = {}
    for column in COLUMNS.values():
        tables = [table for table in column["tables"] if table in schema]
        total = sum(box_height(schema[table]) for table in tables) + GAP * (len(tables) - 1)
        y_top = (CANVAS_HEIGHT + total) / 2
        for table in tables:
            height = box_height(schema[table])
            layout[table] = (column["x"], y_top - height, column["width"])
            y_top -= height + GAP
    return layout


def read_schema(db_path):
    """Возвращает {таблица: [(столбец, тип, pk)]} и список связей (таблица, столбец, ссылка на таблицу)."""
    connection = sqlite3.connect(db_path)
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    columns, relations = {}, []
    for table in tables:
        info = connection.execute(f"PRAGMA table_info({table})").fetchall()
        columns[table] = [(row[1], row[2], row[5] > 0) for row in info]     # имя, тип, признак PK
        for fk in connection.execute(f"PRAGMA foreign_key_list({table})").fetchall():
            relations.append((table, fk[3], fk[2]))                        # таблица, столбец, таблица-родитель
    connection.close()
    return columns, relations


def draw_table(ax, name, columns, x, y, width, foreign_keys):
    """Рисует прямоугольник таблицы со списком столбцов. Возвращает границы (x0, y0, x1, y1)."""
    height = box_height(columns)
    kind = KIND.get(name, "fact")
    ax.add_patch(FancyBboxPatch((x, y), width, height, boxstyle="round,pad=0.02", linewidth=1.2,
                                edgecolor="#333333", facecolor="white"))
    ax.add_patch(FancyBboxPatch((x, y + height - HEADER_HEIGHT), width, HEADER_HEIGHT,
                                boxstyle="round,pad=0.02", linewidth=1.2, edgecolor="#333333",
                                facecolor=COLORS[kind]))
    ax.text(x + width / 2, y + height - HEADER_HEIGHT / 2, name, ha="center", va="center",
            fontsize=10, fontweight="bold")
    for number, (column, col_type, is_pk) in enumerate(columns):
        marks = []
        if is_pk:
            marks.append("PK")
        if column in foreign_keys:
            marks.append("FK")
        label = f"{column}  {col_type.lower()}"
        if marks:
            label = f"{'/'.join(marks)}  " + label
        ax.text(x + 0.12, y + height - HEADER_HEIGHT - ROW_HEIGHT * (number + 0.75), label,
                fontsize=7.5, va="center", fontweight="bold" if is_pk else "normal")
    return (x, y, x + width, y + height)


def connect_boxes(ax, child_box, parent_box):
    """Линия «один ко многим» от родительской таблицы (1) к дочерней (N)."""
    cx0, cy0, cx1, cy1 = child_box
    px0, py0, px1, py1 = parent_box
    child_center_x = (cx0 + cx1) / 2
    if child_center_x < px0:                                   # дочерняя таблица слева
        start, end = (px0, (py0 + py1) / 2), (cx1, (cy0 + cy1) / 2)
    elif child_center_x > px1:                                 # справа
        start, end = (px1, (py0 + py1) / 2), (cx0, (cy0 + cy1) / 2)
    elif cy1 <= py0:                                           # снизу
        start, end = ((px0 + px1) / 2, py0), ((cx0 + cx1) / 2, cy1)
    else:                                                      # сверху
        start, end = ((px0 + px1) / 2, py1), ((cx0 + cx1) / 2, cy0)
    ax.annotate("", xy=end, xytext=start, arrowprops={"arrowstyle": "-", "color": "#555555", "lw": 1.1})
    for share, label in [(0.08, "1"), (0.92, "N")]:
        ax.text(start[0] + (end[0] - start[0]) * share, start[1] + (end[1] - start[1]) * share + 0.13,
                label, fontsize=9, color="#555555", ha="center")


def make_erd(db_path=config.DB_PATH, output=config.BASE_DIR / "docs" / "erd.png"):
    columns, relations = read_schema(db_path)
    layout = compute_layout(columns)
    fig, ax = plt.subplots(figsize=(CANVAS_WIDTH, CANVAS_HEIGHT))
    ax.set_xlim(0, CANVAS_WIDTH)
    ax.set_ylim(0, CANVAS_HEIGHT + 0.4)
    ax.axis("off")
    boxes = {}
    for table, (x, y, width) in layout.items():
        foreign_keys = {column for child, column, _ in relations if child == table}
        boxes[table] = draw_table(ax, table, columns[table], x, y, width, foreign_keys)
    for child, _, parent in relations:
        if child in boxes and parent in boxes:
            connect_boxes(ax, boxes[child], boxes[parent])
    ax.set_title("ER-диаграмма базы covid.db (SQLite, 3НФ): справочники — синие, факты — зелёные, "
                 "результаты анализа — оранжевые", fontsize=12, fontweight="bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150, bbox_inches="tight")
    print(f"ER-диаграмма сохранена: {output} ({len(boxes)} таблиц, {len(relations)} связей)")
    return output


if __name__ == "__main__":
    make_erd()
