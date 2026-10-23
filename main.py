import sys
import os
import time
import json
import shutil
import hashlib

# Устанавливаем базовые зависимости перед импортом Utils.cardinal_tools.
# Одна попытка автозагрузки на пакет: если pip не смог (нет сети/прав/несовместимость),
# печатаем инструкцию и выходим, а не крутим бесконечный цикл.
_BOOTSTRAP_PACKAGES = (
    ("lxml", "lxml>=5.2.2"),
    ("bcrypt", "bcrypt>=4.2.0"),
    ("psutil", "psutil>=5.9.4"),
)


def _bootstrap_pip() -> None:
    try:
        from pip._internal.cli.main import main as pip_main
    except ModuleNotFoundError:
        print(
            "pip не найден в текущем Python. Установите зависимости вручную:\n"
            f'  "{sys.executable}" -m pip install -U -r requirements.txt'
        )
        sys.exit(1)

    for module_name, requirement in _BOOTSTRAP_PACKAGES:
        try:
            __import__(module_name)
            continue
        except ModuleNotFoundError:
            pass

        print(f"Устанавливаю {requirement}...")
        try:
            pip_main(["install", "-U", requirement])
        except SystemExit:
            pass
        except Exception as ex:
            print(f"  не удалось вызвать pip: {ex}")

        try:
            __import__(module_name)
        except ModuleNotFoundError:
            print()
            print(f"  Не удалось установить {module_name}.")
            print("  Установите зависимости тем же Python, которым запускается бот:")
            print(f'    "{sys.executable}" -m pip install -U -r requirements.txt')
            sys.exit(1)


_bootstrap_pip()

try:
    import Utils.cardinal_tools
    import Utils.config_loader as cfg_loader
    from first_setup import first_setup, setup_from_env, env_diagnostics
    from colorama import Fore, Style
    from Utils.logger import LOGGER_CONFIG
    import logging.config
    import colorama
    from cardinal import Cardinal
    import Utils.exceptions as excs
    from Utils.banner import build_banner
except ModuleNotFoundError as ex:
    missing = getattr(ex, "name", None) or str(ex)
    print()
    print("=" * 62)
    print(f"  Не хватает зависимости: {missing}")
    print()
    print("  Установите зависимости тем же Python, которым запускается бот:")
    print(f'    "{sys.executable}" -m pip install -U -r requirements.txt')
    print()
    print("  Рекомендуемый TLS-бэкенд (необязательно, но желательно):")
    print(f'    "{sys.executable}" -m pip install -U -r requirements-tls.txt')
    print()
    print("  Диагностика окружения:")
    print(f'    "{sys.executable}" scripts/check_env.py')
    print("=" * 62)
    sys.exit(1)

VERSION = "1.1.9"

Utils.cardinal_tools.set_console_title(f"Playerok Cardinal v{VERSION}")

if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(__file__))


def _copy_missing(src: str, dst: str) -> None:
    """Копирует файлы из src в dst, не перезаписывая существующие."""
    for root, _dirs, filenames in os.walk(src):
        rel = os.path.relpath(root, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_dir, exist_ok=True)
        for name in filenames:
            target = os.path.join(target_dir, name)
            if not os.path.exists(target):
                try:
                    shutil.copy2(os.path.join(root, name), target)
                except OSError:
                    pass


def _setup_persistent_data() -> None:
    """
    Хранит изменяемые данные в каталоге ``DATA_DIR`` (на хостингах вроде Bothost
    это ``/app/data``, который сохраняется между пересборками контейнера).

    Каталоги ``configs``, ``storage``, ``plugins`` и ``logs`` заменяются
    символическими ссылками на ``DATA_DIR/<name>``. Если ссылку создать нельзя,
    бот продолжает работать с локальными каталогами.
    """
    data_dir = os.getenv("DATA_DIR")

    if not data_dir:
        return

    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError as ex:
        print(f"[DATA_DIR] не удалось создать {data_dir}: {ex}")
        return

    for name in ("configs", "storage", "plugins", "logs"):
        target = os.path.join(data_dir, name)
        local = name

        try:
            os.makedirs(target, exist_ok=True)

            if os.path.islink(local):
                if os.path.realpath(local) == os.path.realpath(target):
                    continue
                os.unlink(local)
            elif os.path.isdir(local):
                _copy_missing(local, target)
                shutil.rmtree(local, ignore_errors=True)

            os.symlink(target, local, target_is_directory=True)
        except OSError as ex:
            print(f"[DATA_DIR] каталог {name} останется локальным: {ex}")


_setup_persistent_data()

folders = ["configs", "logs", "storage", "storage/cache", "storage/products", "plugins"]
for i in folders:
    if not os.path.exists(i):
        os.makedirs(i)


def _sync_plugin_pack() -> None:
    """
    Устанавливает плагины из ``plugin_pack/`` в ``plugins/``.

    * нет файла в ``plugins/`` — копируем;
    * файл есть и не редактировался — обновляем;
    * файл правили вручную — не трогаем (манифест сохраняет прошлую версию).
    """
    src = "plugin_pack"
    if not os.path.isdir(src):
        return

    manifest_path = "storage/cache/plugin_pack.json"
    manifest: dict = {}
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            manifest = {}

    def _hash(path: str) -> str:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    try:
        os.makedirs("plugins", exist_ok=True)
        files = [n for n in os.listdir(src) if n.endswith(".py")]
    except OSError:
        return

    changed = False
    for name in files:
        source = os.path.join(src, name)
        target = os.path.join("plugins", name)
        try:
            shipped = _hash(source)
        except OSError:
            continue

        if not os.path.exists(target):
            shutil.copy2(source, target)
            manifest[name] = shipped
            changed = True
            continue

        try:
            local = _hash(target)
        except OSError:
            continue

        if manifest.get(name) == local and local != shipped:
            shutil.copy2(source, target)
            manifest[name] = shipped
            changed = True
        elif manifest.get(name) is None and local == shipped:
            manifest[name] = shipped
            changed = True

    if changed:
        try:
            os.makedirs("storage/cache", exist_ok=True)
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=4, sort_keys=True)
        except OSError:
            pass


_sync_plugin_pack()

files = ["configs/auto_delivery.cfg", "configs/auto_response.cfg"]
for i in files:
    if not os.path.exists(i):
        with open(i, "w", encoding="utf-8") as f:
            ...

import Utils.cardinal_tools as _ct
_ct.ensure_automation_configs()

colorama.init()

logging.config.dictConfig(LOGGER_CONFIG)
logging.raiseExceptions = False
logger = logging.getLogger("main")

print(f"{Style.RESET_ALL}{build_banner(VERSION)}")

if not os.path.exists("configs/_main.cfg"):
    # На хостинге (Bothost и др.) интерактивной консоли нет — пробуем собрать
    # конфиг из переменных окружения.
    if setup_from_env():
        pass
    elif not sys.stdin or not sys.stdin.isatty():
        print()
        print("=" * 62)
        print("  Не найден файл конфигурации configs/_main.cfg")
        print()
        print("  На хостинге задайте переменные окружения и перезапустите бота:")
        print("    POC_PLAYEROK_TOKEN — токен аккаунта Playerok (из Cookie)")
        print("    BOT_TOKEN          — токен Telegram-бота (Bothost передаёт сам)")
        print("    POC_PASSWORD       — пароль для входа в Telegram-бота")
        print()
        print("  Что видит процесс сейчас:")
        for line in env_diagnostics():
            print(f"    {line}")
        print()
        print("  Частые причины:")
        print("    * переменные добавлены, но бот не перезапущен — нажмите «Перезапустить»;")
        print("    * не задан именно токен Playerok (BOT_TOKEN даёт сам Bothost);")
        print("    * токен Playerok вставлен с пробелами/переносом строки.")
        print("  Либо один раз запустите бота в интерактивном терминале.")
        print("=" * 62)
        sys.exit(1)
    else:
        first_setup()
        sys.exit()

if sys.platform == "linux" and os.getenv('POC_IS_RUNNING_AS_SERVICE', '0') == '1':
    import getpass
    pid = str(os.getpid())
    pidFile = open(f"/run/PlayerokCardinal/{getpass.getuser()}/PlayerokCardinal.pid", "w")
    pidFile.write(pid)
    pidFile.close()
    logger.info(f"$GREENPID файл создан, PID процесса: {pid}")

try:
    MAIN_CFG = cfg_loader.load_main_config("configs/_main.cfg")

    AR_CFG = cfg_loader.load_auto_response_config("configs/auto_response.cfg")
    RAW_AR_CFG = cfg_loader.load_raw_auto_response_config("configs/auto_response.cfg")

    RAW_AD_CFG = cfg_loader.load_raw_auto_delivery_config("configs/auto_delivery.cfg")
    AD_CFG = cfg_loader.load_auto_delivery_config("configs/auto_delivery.cfg")
except excs.ConfigParseError as e:
    logger.error(e)
    logger.error("Завершаю программу...")
    time.sleep(5)
    sys.exit()
except UnicodeDecodeError:
    logger.error("Произошла ошибка при расшифровке UTF-8. Убедитесь, что кодировка файла = UTF-8, а формат конца строк = LF.")
    logger.error("Завершаю программу...")
    time.sleep(5)
    sys.exit()
except:
    logger.critical("Произошла непредвиденная ошибка.")
    logger.warning("TRACEBACK", exc_info=True)
    logger.error("Завершаю программу...")
    time.sleep(5)
    sys.exit()

try:
    Cardinal(MAIN_CFG, AD_CFG, AR_CFG, RAW_AR_CFG, RAW_AD_CFG, VERSION).init().run()
except KeyboardInterrupt:
    logger.info("Завершаю программу...")
    sys.exit()
except:
    logger.critical("При работе Cardinal произошла необработанная ошибка.")
    logger.warning("TRACEBACK", exc_info=True)
    logger.critical("Завершаю программу...")
    time.sleep(5)
    sys.exit()
