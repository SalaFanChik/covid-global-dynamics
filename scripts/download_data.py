"""
Скачивает файл данных OWID в data/raw/.

Запуск из корня проекта:  python scripts/download_data.py
"""
import sys
from pathlib import Path

# добавляем корень проекта в пути импорта, чтобы работал import config / src
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data_loader import download_dataset  # noqa: E402

if __name__ == "__main__":
    try:
        download_dataset()
    except ConnectionError as error:
        print(error)
        sys.exit(1)
