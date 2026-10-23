#!/usr/bin/env python3
"""
Проверка окружения Playerok Cardinal.

Скрипт проверяет, что запущен нужный интерпретатор, что установлены все
зависимости из requirements.txt (и, по возможности, дополнительный
TLS-бэкенд из requirements-tls.txt) и что модули проекта импортируются.

Запускать ТЕМ ЖЕ интерпретатором, которым запускается main.py
(обычно это venv или python внутри контейнера):

    /home/<user>/pyvenv/bin/python scripts/check_env.py
    python scripts/check_env.py
"""
from __future__ import annotations

import importlib
import importlib.metadata as metadata
import os
import platform
import sys
import traceback

MIN_PYTHON = (3, 10)

# (имя пакета в pip, имя модуля для импорта) — обязательные
REQUIRED_PACKAGES: list[tuple[str, str]] = [
    ("psutil", "psutil"),
    ("beautifulsoup4", "bs4"),
    ("colorama", "colorama"),
    ("requests", "requests"),
    ("pytelegrambotapi", "telebot"),
    ("pillow", "PIL"),
    ("lxml", "lxml"),
    ("bcrypt", "bcrypt"),
    ("validators", "validators"),
    ("colorlog", "colorlog"),
    ("tqdm", "tqdm"),
    ("websocket-client", "websocket"),
]

# необязательные: без них бот работает на резервном бэкенде
OPTIONAL_PACKAGES: list[tuple[str, str]] = [
    ("curl-cffi", "curl_cffi"),
]

# модули самого проекта
PROJECT_MODULES = [
    "PlayerokAPI",
    "PlayerokAPI.http_client",
    "PlayerokAPI.account",
    "PlayerokAPI.types",
    "Utils.cardinal_tools",
    "cardinal",
]


def _ok(msg: str) -> None:
    print(f"  [ OK ] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _skip(msg: str) -> None:
    print(f"  [ -- ] {msg}")


def _libc_info() -> str:
    if not sys.platform.startswith("linux"):
        return "-"
    try:
        libc, version = platform.libc_ver()
        return f"{libc or '?'} {version or '?'}"
    except Exception:
        return "?"


def check_python() -> list[str]:
    problems: list[str] = []
    print("Интерпретатор")
    print(f"  executable: {sys.executable}")
    print(f"  version:    {sys.version.split()[0]}")
    print(f"  platform:   {platform.platform()}")
    print(f"  libc:       {_libc_info()}")

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    print(f"  venv:       {'да' if in_venv else 'нет (используется системный Python)'}")

    try:
        from pip import __version__ as pip_version
        print(f"  pip:        {pip_version}")
    except Exception:
        print("  pip:        не установлен")

    if sys.version_info < MIN_PYTHON:
        _fail(f"нужен Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")
        problems.append("python-version")
    else:
        _ok("версия Python подходит")

    if not in_venv:
        _skip("рекомендуется запускать бота из venv (pyvenv) или контейнера")

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

    print("\nДополнительно (requirements-tls.txt)")
    for pip_name, import_name in OPTIONAL_PACKAGES:
        try:
            importlib.import_module(import_name)
        except Exception as ex:
            _warn(
                f"{pip_name} — {type(ex).__name__}: {ex}\n"
                f"         бот запустится на резервном бэкенде requests "
                f"(без TLS-фингерпринта браузера)"
            )
            continue

        try:
            version = metadata.version(pip_name)
        except Exception:
            version = "?"
        _ok(f"{pip_name} {version}")

    return problems


def check_backend() -> list[str]:
    problems: list[str] = []
    print("\nHTTP-бэкенд")

    mode = os.getenv("POC_HTTP_BACKEND", "auto")
    print(f"  POC_HTTP_BACKEND: {mode}")
    if mode.strip().lower() not in ("auto", "curl_cffi", "requests", ""):
        _warn("неизвестное значение, будет использовано auto")

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    try:
        from PlayerokAPI.http_client import create_session
        from PlayerokAPI import http_client
    except Exception as ex:
        _fail(f"не удалось импортировать PlayerokAPI.http_client — {ex}")
        return problems + ["http_client"]

    try:
        session, backend = create_session()
        _ok(f"выбран бэкенд: {backend}")
        close = getattr(session, "close", None)
        if callable(close):
            close()
    except Exception as ex:
        _fail(f"не удалось создать сессию — {type(ex).__name__}: {ex}")
        problems.append("http-backend")

    if http_client.get_backend_mode() == "curl_cffi" and backend != "curl_cffi":
        problems.append("http-backend")

    return problems


def check_config() -> list[str]:
    problems: list[str] = []
    print("\nКонфигурация и данные")

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_dir)

    configs = os.path.join(project_dir, "configs")
    data_dir = os.getenv("DATA_DIR", "")

    print(f"  DATA_DIR:   {data_dir or 'не задана'}")
    if os.path.islink(configs):
        _ok(f"configs -> {os.path.realpath(configs)}")
    else:
        _skip("configs — обычный каталог (данные не переживут пересборку)")

    main_cfg = os.path.join(configs, "_main.cfg")
    if os.path.exists(main_cfg):
        _ok("configs/_main.cfg найден")
    else:
        _warn(
            "configs/_main.cfg нет — бот соберёт его из переменных окружения:\n"
            "         POC_PLAYEROK_TOKEN, BOT_TOKEN, POC_PASSWORD"
        )

    try:
        from first_setup import env_diagnostics
    except Exception as ex:
        _fail(f"не удалось импортировать first_setup — {ex}")
        return problems + ["first_setup"]

    for line in env_diagnostics():
        print(f"  {line}")

    for name in ("storage", "storage/products", "plugins", "logs"):
        path = os.path.join(project_dir, name)
        if os.path.exists(path):
            _ok(f"{name} — на месте")
        else:
            _skip(f"{name} — будет создан при запуске")

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
    problems += check_backend()
    problems += check_config()
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
    print("Если pip падает при установке curl-cffi — это необязательный пакет:")
    print("  бот запустится без него (резервный бэкенд requests).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
