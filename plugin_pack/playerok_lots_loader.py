"""
Playerok Lots Loader — загрузка лотов Playerok из JSON.

Возможности:
  * создание лотов из JSON-файла (много лотов в одном файле);
  * своя обложка у каждого лота (файл или загрузка через Telegram);
  * шаблон JSON под выбранную категорию — с реальными полями и опциями;
  * публикация сразу или оставление в черновиках;
  * автоматическое поднятие (приоритет) лотов по расписанию.

Данные-поля (логин/пароль/код) можно указывать по человеческим названиям
(«Логин», «Пароль») — плагин сам сопоставит их с ID полей категории.

Формат файла:

{
  "game_category_id": "1089",
  "obtaining_type_id": "…",
  "publish": true,
  "image": "storage/products/covers/1.png",   // общая обложка (необязательно)
  "lots": [
    {
      "name": "Название лота",
      "price": 100,
      "description": "Описание",
      "image": "2",                            // своя обложка: номер, имя файла или путь
      "options": {"Платформа": "PC"},
      "data_fields": {"Логин": "user1", "Пароль": "pass1"}
    }
  ]
}

Допускается и обычный массив лотов, если у каждого указаны
game_category_id и obtaining_type_id.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import threading
import time
import uuid
from logging import getLogger
from typing import TYPE_CHECKING, Any

import telebot
from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K
from telebot.types import CallbackQuery, Message

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "Playerok Lots Loader"
VERSION = "1.0.0"
DESCRIPTION = "Загрузка лотов Playerok из JSON, свои обложки и автоподнятие."
CREDITS = "@you"
UUID = "b3f0c1d2-71a4-4c58-9a61-2e0f7d4b8c15"
SETTINGS_PAGE = False
BIND_TO_DELETE = None


logger = getLogger("POC.lots_loader")

SETTINGS_PATH = "storage/plugins/playerok_lots_loader_settings.json"
STATE_PATH = "storage/plugins/playerok_lots_loader_state.json"
COVERS_DIR = "storage/products/covers"
CACHE_DIR = "storage/cache"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

#: ID бесплатного статуса приоритета (тот же, что использует авто-восстановление).
STATUS_FREE_ID = "1efbe5bc-99a7-68e5-4534-85dad913b981"

DEFAULT_SETTINGS = {
    "delay": 1.5,
    "publish": True,
    "allow_premium": False,
    "premium_max_price": 0,
    "default_image": "",
    "auto_raise": False,
    "raise_interval_minutes": 240,
}


# ── состояния и callback'и ───────────────────────────────────
CB = "pkll:"
CBT_CREATE = CB + "create"
CBT_CANCEL = CB + "cancel"
CBT_RAISE = CB + "raise"
CBT_OBT = CB + "obt"

STATE_UPLOAD = "pkll.upload_json"
STATE_COVER = "pkll.cover"

RUNNING = False
_INITIALIZED = False
_RAISE_THREAD_STARTED = False

#: Ожидающие подтверждения планы: {chat_id: {"token": str, "plan": dict}}
PENDING: dict[int, dict] = {}

#: Кэш полей данных категории: {(category_id, obtaining_type_id): [GameCategoryDataField]}
_FIELDS_CACHE: dict[tuple[str, str], list] = {}


# ── служебное ────────────────────────────────────────────────
def _ensure_dirs() -> None:
    for path in (CACHE_DIR, COVERS_DIR, os.path.dirname(SETTINGS_PATH)):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


def _load_json(path: str, default: dict | list) -> Any:
    _ensure_dirs()
    if not os.path.exists(path):
        return json.loads(json.dumps(default))
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning("Не удалось прочитать %s: %s", path, ex)
        return json.loads(json.dumps(default))
    return data


def _save_json(path: str, data: Any) -> None:
    _ensure_dirs()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    data = _load_json(SETTINGS_PATH, {})
    if isinstance(data, dict):
        settings.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    return settings


def save_settings(settings: dict) -> None:
    _save_json(SETTINGS_PATH, settings)


def load_state() -> dict:
    data = _load_json(STATE_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("last_raise", {})
    data.setdefault("created", 0)
    return data


def save_state(state: dict) -> None:
    _save_json(STATE_PATH, state)


def _e(text: Any) -> str:
    """Экранирование для parse_mode=HTML."""
    return html.escape(str(text), quote=False)


def _short(text: Any, limit: int = 60) -> str:
    value = str(text or "").replace("\n", " ").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ── обложки ──────────────────────────────────────────────────
def resolve_image(value: Any, default: str | None = None) -> str | None:
    """
    Находит файл обложки по значению из JSON.

    Поддерживается: путь от корня проекта, абсолютный путь, имя файла в
    ``storage/products/covers`` и просто номер (``3`` → ``.../covers/3.png``).
    """
    if value in (None, ""):
        value = default
    if value in (None, ""):
        return None

    raw = str(value).strip()
    candidates: list[str] = []

    if raw.isdigit():
        for ext in IMAGE_EXTS:
            candidates.append(os.path.join(COVERS_DIR, raw + ext))
    else:
        candidates.append(raw)
        candidates.append(os.path.join(COVERS_DIR, raw))
        candidates.append(os.path.join("storage/products", raw))

    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


def _ext_from_name(name: str | None) -> str:
    if name:
        ext = os.path.splitext(name)[1].lower()
        if ext in IMAGE_EXTS:
            return ext
    return ".png"


# ── поля данных и опции ──────────────────────────────────────
def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().casefold())


def fetch_data_fields(account, category_id: str, obtaining_type_id: str) -> list:
    """Все поля данных типа ITEM_DATA для категории (с кэшем)."""
    from PlayerokAPI.enums import GameCategoryDataFieldTypes

    key = (str(category_id), str(obtaining_type_id))
    if key in _FIELDS_CACHE:
        return _FIELDS_CACHE[key]

    fields: list = []
    cursor = None
    for _ in range(10):
        page = account.get_game_category_data_fields(
            key[0], key[1], count=24,
            type=GameCategoryDataFieldTypes.ITEM_DATA,
            after_cursor=cursor,
        )
        fields.extend(getattr(page, "data_fields", None) or [])
        info = getattr(page, "page_info", None)
        if not info or not getattr(info, "has_next_page", False):
            break
        cursor = getattr(info, "end_cursor", None)
        if not cursor:
            break

    _FIELDS_CACHE[key] = fields
    return fields


def _match_data_fields(available: list, mapping: Any) -> tuple[list, list[str]]:
    """
    Сопоставляет значения из JSON с полями категории.

    :param available: поля категории (``GameCategoryDataField``).
    :param mapping: ``{"Логин": "user"}`` или ``[{"id": "...", "value": "..."}]``.

    :return: (список полей со значениями, предупреждения).
    """
    warnings: list[str] = []
    by_label = {_normalize(getattr(f, "label", "")): f for f in available}
    by_id = {str(getattr(f, "id", "")): f for f in available}

    items: list[tuple[Any, str]] = []
    if isinstance(mapping, dict):
        for key, value in mapping.items():
            field = by_id.get(str(key)) or by_label.get(_normalize(key))
            if not field:
                warnings.append(f"неизвестное поле данных «{key}»")
                continue
            items.append((field, "" if value is None else str(value)))
    elif isinstance(mapping, list):
        for entry in mapping:
            if not isinstance(entry, dict):
                continue
            field = by_id.get(str(entry.get("id", ""))) or by_label.get(_normalize(entry.get("label")))
            if not field:
                warnings.append(f"неизвестное поле данных «{entry.get('id') or entry.get('label')}»")
                continue
            items.append((field, "" if entry.get("value") is None else str(entry.get("value"))))

    result = []
    for field, value in items:
        result.append(type(field)(
            id=getattr(field, "id", ""),
            label=getattr(field, "label", ""),
            type=getattr(field, "type", None),
            input_type=getattr(field, "input_type", None),
            copyable=getattr(field, "copyable", False),
            hidden=getattr(field, "hidden", False),
            required=getattr(field, "required", False),
            value=value,
        ))

    filled = {str(getattr(f, "id", "")) for f, _ in items}
    for field in available:
        if getattr(field, "required", False) and str(getattr(field, "id", "")) not in filled:
            warnings.append(f"не заполнено обязательное поле «{getattr(field, 'label', '?')}»")

    return result, warnings


def _match_options(category, mapping: Any) -> tuple[dict, list[str]]:
    """Сопоставляет опции (атрибуты) из JSON с опциями категории."""
    warnings: list[str] = []
    if not mapping:
        return {}, warnings
    if not isinstance(mapping, dict):
        warnings.append("options должен быть объектом вида {\"Название\": \"значение\"}")
        return {}, warnings

    by_key: dict[str, Any] = {}
    for option in getattr(category, "options", None) or []:
        for key in (getattr(option, "field", None), getattr(option, "label", None),
                    getattr(option, "group", None)):
            if key:
                by_key.setdefault(_normalize(key), option)

    result: dict[str, str] = {}
    for key, value in mapping.items():
        option = by_key.get(_normalize(key))
        if option is None:
            warnings.append(f"неизвестная опция «{key}»")
            continue
        result[str(getattr(option, "field", key))] = "" if value is None else str(value)
    return result, warnings


# ── сборка и проверка плана ──────────────────────────────────
def build_plan(cardinal: "Cardinal", payload: Any) -> dict:
    """
    Проверяет JSON и собирает план создания лотов.

    :return: ``{"lots": [...], "errors": [...], "warnings": [...], "publish": bool}``
    """
    settings = load_settings()
    errors: list[str] = []
    warnings: list[str] = []
    lots: list[dict] = []

    header: dict = payload if isinstance(payload, dict) else {}
    raw_lots = header.get("lots") if isinstance(payload, dict) else payload

    if not isinstance(raw_lots, list) or not raw_lots:
        return {
            "lots": [], "errors": ["в файле нет списка лотов (ожидается объект с ключом \"lots\")"],
            "warnings": [], "publish": False,
        }

    header_cat = str(header.get("game_category_id") or "").strip()
    header_obt = str(header.get("obtaining_type_id") or "").strip()
    header_image = header.get("image") or header.get("default_image") or settings.get("default_image")
    publish = bool(header.get("publish", settings.get("publish", True)))

    account = cardinal.account
    category_cache: dict[str, Any] = {}

    def get_category(cat_id: str):
        if cat_id not in category_cache:
            category_cache[cat_id] = account.get_game_category(id=cat_id)
        return category_cache[cat_id]

    for index, raw in enumerate(raw_lots, start=1):
        if not isinstance(raw, dict):
            errors.append(f"лот {index}: ожидался объект")
            continue

        name = str(raw.get("name") or "").strip()
        if not name:
            errors.append(f"лот {index}: не указано название (name)")
            continue

        try:
            price = int(float(raw.get("price")))
        except (TypeError, ValueError):
            errors.append(f"лот {index} «{_short(name, 30)}»: некорректная цена (price)")
            continue
        if price <= 0:
            errors.append(f"лот {index} «{_short(name, 30)}»: цена должна быть больше 0")
            continue

        cat_id = str(raw.get("game_category_id") or header_cat).strip()
        obt_id = str(raw.get("obtaining_type_id") or header_obt).strip()
        if not cat_id or not obt_id:
            errors.append(f"лот {index} «{_short(name, 30)}»: не указан game_category_id "
                          f"или obtaining_type_id")
            continue

        description = str(raw.get("description") or "")

        try:
            category = get_category(cat_id)
        except Exception as ex:
            errors.append(f"лот {index} «{_short(name, 30)}»: не удалось получить категорию "
                          f"{cat_id} ({ex})")
            continue

        options, option_warnings = _match_options(category, raw.get("options") or {})
        warnings.extend(f"лот {index}: {w}" for w in option_warnings)

        try:
            available_fields = fetch_data_fields(account, cat_id, obt_id)
        except Exception as ex:
            errors.append(f"лот {index} «{_short(name, 30)}»: не удалось получить поля "
                          f"категории ({ex})")
            continue

        data_fields, field_warnings = _match_data_fields(
            available_fields, raw.get("data_fields") or raw.get("fields") or {}
        )
        warnings.extend(f"лот {index}: {w}" for w in field_warnings)

        image_value = raw.get("image") or raw.get("cover") or header_image
        image_path = resolve_image(image_value)
        if image_value and not image_path:
            warnings.append(f"лот {index}: обложка «{image_value}» не найдена — лот будет без неё")
        elif not image_value:
            warnings.append(f"лот {index}: обложка не указана — лот будет без неё")

        lots.append({
            "index": index,
            "name": name,
            "price": price,
            "description": description,
            "game_category_id": cat_id,
            "obtaining_type_id": obt_id,
            "options": options,
            "data_fields": data_fields,
            "image": image_path,
        })

    return {"lots": lots, "errors": errors, "warnings": warnings, "publish": publish}


def _pick_priority_status(cardinal: "Cardinal", item, settings: dict) -> str:
    """
    Выбирает статус приоритета для публикации/поднятия.

    Бесплатный статус — базовый (константа, как в авто-восстановлении).
    Премиум-статус берётся только если он разрешён в настройках, цена не выше
    ``premium_max_price`` и на балансе достаточно средств.
    """
    premium_id = None
    premium_price = None
    try:
        for status in cardinal.account.get_item_priority_statuses(
                str(item.id), str(getattr(item, "price", 0) or 0)) or []:
            price = getattr(status, "price", 0) or 0
            if price > 0 and (premium_price is None or price < premium_price):
                premium_price = price
                premium_id = getattr(status, "id", None)
    except Exception as ex:
        logger.debug("priority statuses %s: %s", getattr(item, "id", "?"), ex)

    if not premium_id or not settings.get("allow_premium"):
        return STATUS_FREE_ID

    limit = int(settings.get("premium_max_price") or 0)
    if limit and premium_price > limit:
        return STATUS_FREE_ID

    try:
        balance = float(getattr(cardinal.get_balance(), "available", 0) or 0)
    except Exception:
        balance = 0.0
    return premium_id if balance >= premium_price else STATUS_FREE_ID


def publish_item(cardinal: "Cardinal", item, settings: dict) -> tuple[bool, str]:
    """Публикует созданный предмет (черновик)."""
    status_id = _pick_priority_status(cardinal, item, settings)
    if not status_id:
        return False, "нет доступного статуса приоритета (бесплатный недоступен)"
    try:
        cardinal.account.publish_item(str(item.id), status_id)
        return True, ""
    except Exception as ex:
        return False, str(ex)


def create_one(cardinal: "Cardinal", lot: dict, settings: dict) -> tuple[bool, str, Any]:
    """Создаёт (и при необходимости публикует) один лот."""
    attachments = [lot["image"]] if lot.get("image") else []
    try:
        item = cardinal.account.create_item(
            game_category_id=lot["game_category_id"],
            obtaining_type_id=lot["obtaining_type_id"],
            name=lot["name"],
            price=lot["price"],
            description=lot["description"],
            options=lot.get("options") or {},
            data_fields=lot.get("data_fields") or [],
            attachments=attachments,
        )
    except Exception as ex:
        return False, f"ошибка создания: {ex}", None

    if not settings.get("publish", True):
        return True, "создан черновиком", item

    ok, message = publish_item(cardinal, item, settings)
    if ok:
        return True, "опубликован", item
    return True, f"создан черновиком ({message})", item


def execute_plan(cardinal: "Cardinal", bot, chat_id: int, plan: dict) -> None:
    """Создаёт все лоты из плана, отчитываясь в чат."""
    global RUNNING
    settings = load_settings()
    delay = max(0.5, float(settings.get("delay") or 1.5))
    total = len(plan["lots"])
    created = 0
    failed: list[str] = []

    bot.send_message(chat_id, f"⏳ Создаю {total} лот(-ов)…")

    try:
        for number, lot in enumerate(plan["lots"], start=1):
            ok, message, item = create_one(cardinal, lot, settings)
            if ok:
                created += 1
                if item is not None and message != "опубликован":
                    logger.info("Лот «%s» (%s): %s", lot["name"], item.id, message)
            else:
                failed.append(f"{number}. {_short(lot['name'], 40)} — {message}")
                logger.warning("Лот «%s» не создан: %s", lot["name"], message)

            if number % 10 == 0 and number < total:
                bot.send_message(chat_id, f"… {number}/{total}")

            time.sleep(delay)
    except Exception:
        logger.debug("TRACEBACK", exc_info=True)
    finally:
        global RUNNING
        RUNNING = False

    state = load_state()
    state["created"] = int(state.get("created") or 0) + created
    save_state(state)

    text = f"✅ Готово: создано {created} из {total}."
    if failed:
        text += f"\n\n❌ Не удалось ({len(failed)}):\n" + "\n".join(_e(f) for f in failed[:15])
        if len(failed) > 15:
            text += f"\n… и ещё {len(failed) - 15}"
    bot.send_message(chat_id, text)


# ── автоподнятие ─────────────────────────────────────────────
def _approved_items(cardinal: "Cardinal", limit: int = 200) -> list:
    from PlayerokAPI.enums import ItemStatuses

    items: list = []
    cursor = None
    while len(items) < limit:
        page = cardinal.account.get_my_items(
            statuses=[ItemStatuses.APPROVED], count=24, after_cursor=cursor
        )
        page_items = getattr(page, "items", None) or []
        items.extend(page_items)
        info = getattr(page, "page_info", None)
        if not page_items or not info or not getattr(info, "has_next_page", False):
            break
        cursor = getattr(info, "end_cursor", None)
        if not cursor:
            break
    return items[:limit]


def raise_one(cardinal: "Cardinal", item, settings: dict) -> tuple[bool, str]:
    """Поднимает один лот (бесплатный приоритет, при желании — премиум)."""
    status_id = _pick_priority_status(cardinal, item, settings)
    if not status_id:
        return False, "нет доступного статуса приоритета"
    try:
        cardinal.account.increase_item_priority_status(str(item.id), status_id)
        return True, ""
    except Exception as ex:
        return False, str(ex)


def raise_loop(cardinal: "Cardinal") -> None:
    """Фоновый цикл автоподнятия лотов."""
    logger.info("Автоподнятие: поток запущен")
    while True:
        time.sleep(30)
        try:
            settings = load_settings()
            if not settings.get("auto_raise"):
                continue

            interval = max(5, int(settings.get("raise_interval_minutes") or 240)) * 60
            state = load_state()
            last_raise: dict = state.setdefault("last_raise", {})
            now = time.time()

            try:
                items = _approved_items(cardinal)
            except Exception as ex:
                logger.debug("Автоподнятие: не удалось получить лоты: %s", ex)
                continue

            raised = 0
            for item in items:
                item_id = str(getattr(item, "id", "") or "")
                if not item_id:
                    continue
                last = float(last_raise.get(item_id, 0) or 0)
                if now - last < interval:
                    continue
                ok, message = raise_one(cardinal, item, settings)
                if ok:
                    last_raise[item_id] = time.time()
                    raised += 1
                    logger.info("Автоподнятие: «%s» поднят", getattr(item, "name", item_id))
                else:
                    # ставим отметку, чтобы не долбить API каждые 30 секунд
                    last_raise[item_id] = time.time()
                    logger.debug("Автоподнятие «%s»: %s", getattr(item, "name", item_id), message)
                time.sleep(2)

            save_state(state)
            if raised:
                logger.info("Автоподнятие: поднято лотов — %s", raised)
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)


def _start_raise_thread(cardinal: "Cardinal") -> None:
    global _RAISE_THREAD_STARTED
    if _RAISE_THREAD_STARTED:
        return
    _RAISE_THREAD_STARTED = True
    threading.Thread(target=raise_loop, args=(cardinal,), name="pkll-raise", daemon=True).start()


# ── тексты и клавиатуры ──────────────────────────────────────
def _menu_kb() -> K:
    keyboard = K()
    keyboard.add(B("📥 Загрузить JSON", callback_data=CB + "upload"))
    keyboard.add(B("🧩 Шаблон JSON", callback_data=CB + "template"))
    keyboard.add(B("🖼 Загрузить обложку", callback_data=CB + "cover"))
    keyboard.add(B("⬆️ Автоподнятие", callback_data=CB + "raise"))
    return keyboard


def _help_text() -> str:
    return (
        "<b>Playerok Lots Loader</b>\n\n"
        "Команды:\n"
        "• /pk_upload — загрузить лоты из JSON-файла\n"
        "• /pk_template — получить шаблон JSON под категорию\n"
        "• /pk_cat — найти игру и её категории\n"
        "• /pk_cover — загрузить обложку для лота\n"
        "• /pk_raise — автоподнятие лотов вкл/выкл\n"
        "• /pk_raise_now — поднять все лоты сейчас\n\n"
        "Формат файла:\n"
        "<pre>{\n"
        "  \"game_category_id\": \"1089\",\n"
        "  \"obtaining_type_id\": \"…\",\n"
        "  \"publish\": true,\n"
        "  \"lots\": [\n"
        "    {\n"
        "      \"name\": \"Название\",\n"
        "      \"price\": 100,\n"
        "      \"description\": \"Описание\",\n"
        "      \"image\": \"1\",\n"
        "      \"options\": {\"Платформа\": \"PC\"},\n"
        "      \"data_fields\": {\"Логин\": \"user\", \"Пароль\": \"pass\"}\n"
        "    }\n"
        "  ]\n"
        "}</pre>\n"
        "В одном файле может быть сколько угодно лотов, у каждого — своя обложка "
        f"(<code>{COVERS_DIR}</code> или путь к файлу)."
    )


# ── обработчики ──────────────────────────────────────────────
def init_commands(cardinal: "Cardinal"):
    global _INITIALIZED
    if not cardinal.telegram:
        return

    tg = cardinal.telegram
    bot = tg.bot

    if _INITIALIZED:
        # повторный вызов (Cardinal вызывает pre-init дважды) — только меню команд
        cardinal.add_telegram_commands(UUID, [
            ("pk_lots", "Playerok: загрузка лотов из JSON", True),
        ])
        return

    # ── меню ────────────────────────────────────────────────
    def pk_menu(message: Message):
        bot.send_message(message.chat.id, _help_text(), reply_markup=_menu_kb())

    # ── загрузка JSON ───────────────────────────────────────
    def act_upload(message: Message):
        if RUNNING:
            bot.send_message(message.chat.id, "❌ Уже идёт создание лотов. Дождитесь окончания.")
            return
        result = bot.send_message(
            message.chat.id,
            "📥 Отправьте JSON-файл с лотами.\n"
            "Шаблон можно получить командой /pk_template.",
            reply_markup=_menu_kb(),
        )
        tg.set_state(message.chat.id, result.id, message.from_user.id, STATE_UPLOAD)

    def _download(message: Message, file_name: str) -> str | None:
        document = message.document
        if not document:
            return None
        if document.file_size and document.file_size > 20 * 1024 * 1024:
            bot.send_message(message.chat.id, "❌ Файл больше 20 МБ.")
            return None
        try:
            file_info = bot.get_file(document.file_id)
            data = bot.download_file(file_info.file_path)
        except Exception as ex:
            logger.debug("download: %s", ex)
            bot.send_message(message.chat.id, "❌ Не удалось скачать файл.")
            return None

        _ensure_dirs()
        path = os.path.join(CACHE_DIR, file_name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def on_upload_file(message: Message):
        state = tg.get_state(message.chat.id, message.from_user.id) or {}
        if state.get("state") != STATE_UPLOAD:
            return
        tg.clear_state(message.chat.id, message.from_user.id, True)

        if not message.document:
            bot.send_message(message.chat.id, "❌ Нужен файл .json.")
            return
        name = (message.document.file_name or "").lower()
        if not name.endswith(".json"):
            bot.send_message(message.chat.id, "❌ Нужен файл .json.")
            return

        path = _download(message, f"pkll_{message.chat.id}.json")
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except json.JSONDecodeError as ex:
            bot.send_message(message.chat.id, f"❌ Ошибка в JSON: <code>{_e(ex)}</code>")
            return

        bot.send_message(message.chat.id, "🔎 Проверяю лоты…")
        try:
            plan = build_plan(cardinal, payload)
        except Exception as ex:
            logger.debug("TRACEBACK", exc_info=True)
            bot.send_message(message.chat.id, f"❌ Не удалось разобрать файл: {_e(ex)}")
            return

        if not plan["lots"]:
            text = "❌ Нечего создавать.\n"
            if plan["errors"]:
                text += "\n" + "\n".join(_e(err) for err in plan["errors"][:15])
            bot.send_message(message.chat.id, text)
            return

        token = uuid.uuid4().hex[:10]
        PENDING[message.chat.id] = {"token": token, "plan": plan}

        lines = [f"Проверено: <b>{len(plan['lots'])}</b> лот(-ов)."]
        preview = plan["lots"][:5]
        for lot in preview:
            cover = "🖼" if lot.get("image") else "▫️"
            lines.append(f"{cover} {_e(_short(lot['name'], 45))} — {lot['price']} ₽")
        if len(plan["lots"]) > len(preview):
            lines.append(f"… и ещё {len(plan['lots']) - len(preview)}")

        if plan["warnings"]:
            lines.append("\n⚠️ Предупреждения:")
            lines.extend(_e(w) for w in plan["warnings"][:10])
            if len(plan["warnings"]) > 10:
                lines.append(f"… и ещё {len(plan['warnings']) - 10}")

        if plan["errors"]:
            lines.append("\n❌ Ошибки (эти лоты будут пропущены):")
            lines.extend(_e(err) for err in plan["errors"][:10])

        keyboard = K().add(
            B(f"✅ Создать {len(plan['lots'])}", callback_data=f"{CBT_CREATE}:{token}"),
            B("✖️ Отмена", callback_data=f"{CBT_CANCEL}:{token}"),
        )
        bot.send_message(message.chat.id, "\n".join(lines), reply_markup=keyboard)

    # ── создание по подтверждению ───────────────────────────
    def _create_worker(chat_id: int, plan: dict):
        global RUNNING
        RUNNING = True
        execute_plan(cardinal, bot, chat_id, plan)

    def on_callback(call: CallbackQuery):
        data = call.data or ""
        chat_id = call.message.chat.id

        if data.startswith(CBT_CANCEL):
            PENDING.pop(chat_id, None)
            try:
                bot.edit_message_reply_markup(chat_id, call.message.id, reply_markup=None)
            except Exception:
                pass
            bot.answer_callback_query(call.id, "Отменено")
            return

        if data.startswith(CBT_CREATE):
            token = data.split(":", 2)[2] if data.count(":") >= 2 else ""
            pending = PENDING.get(chat_id)
            if not pending or pending.get("token") != token:
                bot.answer_callback_query(call.id, "Этот план уже неактуален", show_alert=True)
                return
            if RUNNING:
                bot.answer_callback_query(call.id, "Уже идёт создание лотов", show_alert=True)
                return
            PENDING.pop(chat_id, None)
            try:
                bot.edit_message_reply_markup(chat_id, call.message.id, reply_markup=None)
            except Exception:
                pass
            bot.answer_callback_query(call.id, "Запускаю…")
            threading.Thread(
                target=_create_worker, args=(chat_id, pending["plan"]), daemon=True
            ).start()
            return

        if data.startswith(CBT_RAISE):
            settings = load_settings()
            settings["auto_raise"] = not settings.get("auto_raise")
            save_settings(settings)
            state = "включено" if settings["auto_raise"] else "выключено"
            bot.answer_callback_query(call.id, f"Автоподнятие {state}")
            bot.send_message(
                chat_id,
                f"⬆️ Автоподнятие <b>{state}</b>.\n"
                f"Интервал: каждые {settings['raise_interval_minutes']} мин. "
                f"на лот (изменить — в {SETTINGS_PATH}).",
            )
            return

        if data.startswith(CBT_OBT):
            _, _, category_id, obtaining_type_id = data.split(":", 3)
            send_template(chat_id, category_id, obtaining_type_id)
            bot.answer_callback_query(call.id, "Готово")
            return

    # ── шаблон ──────────────────────────────────────────────
    def send_template(chat_id: int, category_id: str, obtaining_type_id: str):
        try:
            category = cardinal.account.get_game_category(id=category_id)
            fields = fetch_data_fields(cardinal.account, category_id, obtaining_type_id)
        except Exception as ex:
            bot.send_message(chat_id, f"❌ Не удалось получить данные категории: {_e(ex)}")
            return

        from PlayerokAPI.enums import GameCategoryDataFieldTypes

        options = {}
        for option in getattr(category, "options", None) or []:
            key = getattr(option, "label", None) or getattr(option, "field", None)
            if key:
                options[key] = getattr(option, "value", "") or ""

        data_fields = {
            getattr(f, "label", "поле"): ""
            for f in fields
            if getattr(f, "type", None) == GameCategoryDataFieldTypes.ITEM_DATA
        }

        template = {
            "game_category_id": str(category_id),
            "obtaining_type_id": str(obtaining_type_id),
            "publish": True,
            "lots": [
                {
                    "name": getattr(category, "name", "") or "Название лота",
                    "price": 100,
                    "description": "Описание лота",
                    "image": "1",
                    "options": options,
                    "data_fields": data_fields,
                }
            ],
        }

        buffer = io.BytesIO(json.dumps(template, indent=4, ensure_ascii=False).encode("utf-8"))
        buffer.name = "playerok_lots_template.json"
        try:
            bot.send_document(
                chat_id, buffer, visible_file_name="playerok_lots_template.json",
                caption=f"Шаблон для «{_e(getattr(category, 'name', category_id))}»",
            )
        except Exception as ex:
            logger.debug("send_document: %s", ex)
            bot.send_message(chat_id, f"<pre>{_e(json.dumps(template, indent=2, ensure_ascii=False))}</pre>")

    def act_template(message: Message):
        parts = (message.text or "").split()[1:]
        if not parts:
            bot.send_message(
                message.chat.id,
                "Использование:\n"
                "• <code>/pk_template ID_категории</code> — шаблон сразу\n"
                "• <code>/pk_cat Название игры</code> — найти категории\n\n"
                "ID категории виден в ссылке: playerok.com/<i>игра</i>/<b>категория</b>.",
            )
            return

        category_id = parts[0]
        try:
            types = cardinal.account.get_game_category_obtaining_types(category_id)
            obtaining_types = getattr(types, "obtaining_types", None) or []
        except Exception as ex:
            bot.send_message(message.chat.id, f"❌ Категория не найдена: {_e(ex)}")
            return

        if len(parts) >= 2:
            send_template(message.chat.id, category_id, parts[1])
            return

        if not obtaining_types:
            bot.send_message(message.chat.id, "❌ У категории нет способов получения.")
            return

        if len(obtaining_types) == 1:
            send_template(message.chat.id, category_id, getattr(obtaining_types[0], "id"))
            return

        keyboard = K()
        for obt in obtaining_types:
            keyboard.add(B(
                _short(getattr(obt, "name", None) or getattr(obt, "id"), 50),
                callback_data=f"{CBT_OBT}:{category_id}:{getattr(obt, 'id')}",
            ))
        bot.send_message(message.chat.id, "Выберите способ получения:", reply_markup=keyboard)

    # ── категории ───────────────────────────────────────────
    def act_cat(message: Message):
        parts = (message.text or "").split(maxsplit=1)
        query = parts[1].strip() if len(parts) > 1 else ""
        if not query:
            bot.send_message(
                message.chat.id,
                "Использование:\n"
                "• <code>/pk_cat Steam</code> — найти игры\n"
                "• <code>/pk_cat 123456</code> — категории игры по её ID",
            )
            return

        try:
            if query.isdigit():
                game = cardinal.account.get_game(id=query)
                categories = getattr(game, "categories", None) or []
                if not categories:
                    bot.send_message(message.chat.id, "❌ У игры нет категорий.")
                    return
                lines = [f"<b>{_e(getattr(game, 'name', query))}</b> — категории:"]
                for cat in categories:
                    lines.append(
                        f"• <code>{_e(getattr(cat, 'id', ''))}</code> — "
                        f"{_e(getattr(cat, 'name', ''))} "
                        f"(<i>{_e(getattr(cat, 'slug', ''))}</i>)"
                    )
                lines.append("\nДальше: <code>/pk_template ID_категории</code>")
                bot.send_message(message.chat.id, "\n".join(lines))
                return

            games = cardinal.account.get_games(name=query, count=20)
            game_items = getattr(games, "games", None) or []
            if not game_items:
                bot.send_message(message.chat.id, "❌ Ничего не найдено.")
                return
            lines = [f"Найдено игр: <b>{len(game_items)}</b>"]
            for game in game_items:
                lines.append(
                    f"• <code>{_e(getattr(game, 'id', ''))}</code> — "
                    f"{_e(getattr(game, 'name', ''))} "
                    f"(<i>{_e(getattr(game, 'slug', ''))}</i>)"
                )
            lines.append("\nДальше: <code>/pk_cat ID_игры</code>")
            bot.send_message(message.chat.id, "\n".join(lines))
        except Exception as ex:
            logger.debug("TRACEBACK", exc_info=True)
            bot.send_message(message.chat.id, f"❌ Ошибка: {_e(ex)}")

    # ── обложки ─────────────────────────────────────────────
    def act_cover(message: Message):
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            bot.send_message(
                message.chat.id,
                "Использование: <code>/pk_cover 1</code>, затем отправьте фото.\n"
                f"Файл сохранится как <code>{COVERS_DIR}/1.png</code> — в JSON укажите "
                "<code>\"image\": \"1\"</code>.",
                reply_markup=_menu_kb(),
            )
            return
        index = parts[1]
        result = bot.send_message(
            message.chat.id, f"🖼 Отправьте картинку для обложки №{index} (фото или файл)."
        )
        tg.set_state(message.chat.id, result.id, message.from_user.id, STATE_COVER, {"index": index})

    def on_cover_file(message: Message):
        state = tg.get_state(message.chat.id, message.from_user.id) or {}
        if state.get("state") != STATE_COVER:
            return
        index = str((state.get("data") or {}).get("index") or "").strip()
        tg.clear_state(message.chat.id, message.from_user.id, True)
        if not index:
            return

        if message.photo:
            file_id = message.photo[-1].file_id
            ext = ".png"
        elif message.document:
            file_id = message.document.file_id
            ext = _ext_from_name(message.document.file_name)
        else:
            bot.send_message(message.chat.id, "❌ Нужно фото или файл-картинка.")
            return

        try:
            file_info = bot.get_file(file_id)
            data = bot.download_file(file_info.file_path)
        except Exception as ex:
            logger.debug("download cover: %s", ex)
            bot.send_message(message.chat.id, "❌ Не удалось скачать картинку.")
            return

        _ensure_dirs()
        path = os.path.join(COVERS_DIR, index + ext)
        with open(path, "wb") as f:
            f.write(data)
        bot.send_message(
            message.chat.id,
            f"✅ Обложка сохранена: <code>{_e(path)}</code>\n"
            f"В JSON: <code>\"image\": \"{_e(index)}\"</code>",
        )

    # ── автоподнятие ────────────────────────────────────────
    def act_raise(message: Message):
        parts = (message.text or "").split()[1:]
        settings = load_settings()

        if parts and parts[0].lower() in ("on", "вкл", "1"):
            settings["auto_raise"] = True
        elif parts and parts[0].lower() in ("off", "выкл", "0"):
            settings["auto_raise"] = False
        else:
            settings["auto_raise"] = not settings.get("auto_raise")
        save_settings(settings)

        state = "включено" if settings["auto_raise"] else "выключено"
        bot.send_message(
            message.chat.id,
            f"⬆️ Автоподнятие <b>{state}</b>\n"
            f"• интервал: каждые {settings['raise_interval_minutes']} мин. на лот\n"
            f"• премиум-поднятие: {'разрешено' if settings['allow_premium'] else 'запрещено'}\n\n"
            f"Настройки: <code>{SETTINGS_PATH}</code>\n"
            "Поднять всё сейчас — /pk_raise_now",
        )

    def act_raise_now(message: Message):
        settings = load_settings()

        def worker():
            try:
                items = _approved_items(cardinal)
            except Exception as ex:
                bot.send_message(message.chat.id, f"❌ Не удалось получить лоты: {_e(ex)}")
                return
            if not items:
                bot.send_message(message.chat.id, "Активных лотов нет.")
                return

            bot.send_message(message.chat.id, f"⬆️ Поднимаю {len(items)} лот(-ов)…")
            state = load_state()
            last_raise = state.setdefault("last_raise", {})
            raised = 0
            errors: list[str] = []
            for item in items:
                ok, error = raise_one(cardinal, item, settings)
                if ok:
                    raised += 1
                    last_raise[str(getattr(item, "id", ""))] = time.time()
                else:
                    errors.append(f"{_short(getattr(item, 'name', '?'), 40)} — {error}")
                time.sleep(2)
            save_state(state)

            text = f"✅ Поднято: {raised} из {len(items)}."
            if errors:
                text += "\n\n❌ Не удалось:\n" + "\n".join(_e(err) for err in errors[:10])
            bot.send_message(message.chat.id, text)

        threading.Thread(target=worker, daemon=True).start()

    # ── регистрация ─────────────────────────────────────────
    card = cardinal
    card.add_telegram_commands(UUID, [
        ("pk_lots", "Playerok: лоты из JSON", True),
        ("pk_upload", "загрузить лоты из JSON", False),
        ("pk_template", "шаблон JSON под категорию", False),
        ("pk_cat", "найти игру и категории", False),
        ("pk_cover", "загрузить обложку лота", False),
        ("pk_raise", "автоподнятие вкл/выкл", False),
    ])

    tg.msg_handler(pk_menu, commands=["pk_lots", "pk_help"])
    tg.msg_handler(act_upload, commands=["pk_upload"])
    tg.msg_handler(act_template, commands=["pk_template"])
    tg.msg_handler(act_cat, commands=["pk_cat"])
    tg.msg_handler(act_cover, commands=["pk_cover"])
    tg.msg_handler(act_raise, commands=["pk_raise"])
    tg.msg_handler(act_raise_now, commands=["pk_raise_now"])
    tg.file_handler(STATE_UPLOAD, on_upload_file)
    tg.file_handler(STATE_COVER, on_cover_file)
    tg.cbq_handler(on_callback, lambda c: (c.data or "").startswith(CB))

    _INITIALIZED = True
    _start_raise_thread(cardinal)
    logger.info("Playerok Lots Loader: обработчики зарегистрированы")


BIND_TO_PRE_INIT = [init_commands]
