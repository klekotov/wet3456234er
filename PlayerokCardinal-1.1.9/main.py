import sys
import os
import time

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
    from first_setup import first_setup
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

folders = ["configs", "logs", "storage", "storage/cache", "storage/products", "plugins"]
for i in folders:
    if not os.path.exists(i):
        os.makedirs(i)

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
