#!/usr/bin/env python3
"""
Проверка окружения Playerok Cardinal.

Скрипт проверяет, что запущен нужный интерпретатор, что установлены все
зависимости из requirements.txt и что модули проекта импортируются.

Запускать ТЕМ ЖЕ интерпретатором, которым запускается main.py
(обычно это venv):

    /home/<user>/pyvenv/bin/python scripts/check_env.py

или, если venv активирован:

    python scripts/check_env.py
"""
from __future__ import annotations

import importlib
import importlib.metadata as metadata
import os
import sys
import traceback

MIN_PYTHON = (3, 10)

# (имя пакета в pip, имя модуля для импорта)
REQUIRED_PACKAGES: list[tuple[str, str]] = [
    ("psutil", "psutil"),
    ("beautifulsoup4", "bs4"),
    ("colorama", "colorama"),
    ("requests", "requests"),
    ("pytelegrambotapi", "telebot"),
    ("pillow", "PIL"),
    ("lxml", "lxml"),
    ("bcrypt", "bcrypt"),
    ("curl-cffi", "curl_cffi"),
    ("validators", "validators"),
    ("colorlog", "colorlog"),
    ("tqdm", "tqdm"),
    ("websocket-client", "websocket"),
]

# модули самого проекта
PROJECT_MODULES = [
    "PlayerokAPI",
    "PlayerokAPI.account",
    "PlayerokAPI.types",
    "Utils.cardinal_tools",
    "cardinal",
]


def _ok(msg: str) -> None:
    print(f"  [ OK ] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _skip(msg: str) -> None:
    print(f"  [ -- ] {msg}")


def check_python() -> list[str]:
    problems: list[str] = []
    print("Интерпретатор")
    print(f"  executable: {sys.executable}")
    print(f"  version:    {sys.version.split()[0]}")

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    print(f"  venv:       {'да' if in_venv else 'нет (используется системный Python)'}")

    if sys.version_info < MIN_PYTHON:
        _fail(f"нужен Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")
        problems.append("python-version")
    else:
        _ok("версия Python подходит")

    if not in_venv:
        _skip("рекомендуется запускать бота из venv (pyvenv)")

    return problems


def check_packages() -> list[str]:
    problems: list[str] = []
    print("\nЗависимости (requirements.txt)")

    for pip_name, import_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except Exception as ex:
            _fail(f"{pip_name} — {type(ex).__name__}: {ex}")
            problems.append(pip_name)
            continue

        try:
            version = metadata.version(pip_name)
        except Exception:
            version = "?"
        _ok(f"{pip_name} {version}")

    return problems


def check_project() -> list[str]:
    problems: list[str] = []
    print("\nМодули проекта")

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.getcwd() != project_dir:
        print(f"  cwd:        {os.getcwd()}")
        print(f"  project:    {project_dir}")
        os.chdir(project_dir)
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    for module in PROJECT_MODULES:
        try:
            importlib.import_module(module)
        except Exception as ex:
            _fail(f"{module} — {type(ex).__name__}: {ex}")
            traceback.print_exc(limit=3)
            problems.append(module)
        else:
            _ok(module)

    # tls_requests больше не нужен — предупреждаем, если он остался в venv
    try:
        importlib.import_module("tls_requests")
    except Exception:
        pass
    else:
        _skip("tls_requests установлен, но проекту он больше не нужен")

    return problems


def main() -> int:
    print("=" * 60)
    print("Playerok Cardinal — проверка окружения")
    print("=" * 60)

    problems = []
    problems += check_python()
    problems += check_packages()
    problems += check_project()

    print("\n" + "=" * 60)
    if not problems:
        print("Всё в порядке: зависимости на месте, модули импортируются.")
        print("Запуск бота:")
        print(f'  "{sys.executable}" main.py')
        return 0

    print(f"Найдено проблем: {len(problems)}")
    for name in problems:
        print(f"  - {name}")
    print("\nИсправить зависимости можно командой:")
    print(f'  "{sys.executable}" -m pip install -U pip')
    print(f'  "{sys.executable}" -m pip install -U -r requirements.txt')
    print("\nВажно: команда должна использовать ТОТ ЖЕ python, что и запуск main.py.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
