"""
Конфигурация проекта: пути к файлам, ссылка на данные и параметры анализа.

Все остальные модули импортируют настройки отсюда, чтобы константы
не дублировались в разных файлах.
"""
import math
from pathlib import Path

# --- Папки проекта (pathlib строит пути независимо от ОС) ---
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = BASE_DIR / "outputs"
FIGURES_DIR = OUTPUT_DIR / "figures"
TABLES_DIR = OUTPUT_DIR / "tables"

# --- Файлы ---
# Репозиторий https://github.com/owid/covid-19-data с 19.08.2024 не обновляется
# и отсылает к актуальной версии того же набора данных в каталоге OWID:
DATA_URL = "https://catalog.ourworldindata.org/garden/covid/latest/compact/compact.csv"
RAW_CSV = RAW_DIR / "owid_covid_compact.csv"
CODEBOOK_CSV = RAW_DIR / "owid-covid-codebook.csv"
RAW_PARQUET = PROCESSED_DIR / "covid_raw_optimized.parquet"   # те же данные, что CSV, но в Parquet
CLEAN_PARQUET = PROCESSED_DIR / "covid_clean.parquet"         # очищенные данные
DB_PATH = DATA_DIR / "covid.db"
RESULTS_JSON = OUTPUT_DIR / "results.json"

# --- Параметры загрузки ---
CHUNK_SIZE = 100_000   # строк в одном чанке при чтении CSV

# --- Страны для детального анализа (волны, прогноз, графики) ---
FOCUS_COUNTRIES = [
    "Kazakhstan", "Russia", "Germany", "United States", "Brazil",
    "India", "Italy", "Japan", "South Africa", "United Kingdom",
]

# --- Параметры детекции волн (недельные случаи на миллион жителей) ---
WAVE_SMOOTH_WEEKS = 3               # окно центрированного скользящего среднего
WAVE_THRESHOLD_PER_MILLION = 50     # baseline-метод: порог недельных случаев на млн
WAVE_MIN_WEEKS = 4                  # минимальная длительность волны
WAVE_MAX_GAP_WEEKS = 2              # разрывы короче объединяются в одну волну
WAVE_MIN_PROMINENCE = math.log(2)   # метод пиков: рост и спад не менее чем в 2 раза (шкала log)
WAVE_MIN_DISTANCE_WEEKS = 8         # минимальное расстояние между пиками
WAVE_MIN_PEAK_PER_MILLION = 50      # пик ниже этого уровня волной не считается
WAVE_BOUNDARY_SHARE = 0.10          # граница волны: ряд опустился ниже 10 % от пика

# --- Параметры прогноза (недельные случаи на миллион) ---
FORECAST_START = "2021-01-01"       # начало периода оценки (rolling origin)
FORECAST_END = "2023-06-30"         # после 2023 г. ряды почти нулевые, прогноз там неинформативен
FORECAST_HORIZONS = [1, 4]          # горизонты прогноза в неделях
MA_WINDOW = 4                       # окно скользящего среднего
N_LAGS = 4                          # число лагов для линейной регрессии
SEASONAL_PERIOD_WEEKS = 52          # период для seasonal naive и декомпозиции

# --- Дашборд ---
DASH_HOST = "127.0.0.1"
DASH_PORT = 8050
