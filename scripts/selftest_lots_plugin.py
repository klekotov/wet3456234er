"""
Самопроверка плагина Playerok Lots Loader без реального аккаунта Playerok.

Запуск:  python scripts/selftest_lots_plugin.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PlayerokAPI.enums import GameCategoryDataFieldTypes  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "plugin_pack"))
import playerok_lots_loader as plugin  # noqa: E402


def _field(fid: str, label: str, required: bool = False, hidden: bool = False):
    return SimpleNamespace(
        id=fid,
        label=label,
        type=GameCategoryDataFieldTypes.ITEM_DATA,
        input_type=None,
        copyable=True,
        hidden=hidden,
        required=required,
        value=None,
    )


class FakeAccount:
    def __init__(self):
        self.created: list[dict] = []
        self.published: list[tuple[str, str]] = []
        self.raised: list[tuple[str, str]] = []

    GAME_ID = "1ecc48ce-4f1a-6531-300d-9faaa8c3ab04"
    CATEGORY_ID = "9d3f77b1-2c4e-4a55-8f60-11aa22bb33cc"

    def get_game(self, id=None, slug=None):
        if id == self.GAME_ID or slug == "roblox":
            return SimpleNamespace(id=self.GAME_ID, name="Roblox", slug="roblox", categories=[
                SimpleNamespace(id=self.CATEGORY_ID, name="Аккаунты", slug="accounts"),
                SimpleNamespace(id="cat-robux", name="Валюта", slug="robux"),
            ])
        return None

    def get_game_category(self, id=None, game_id=None, slug=None):
        if game_id == self.GAME_ID and slug == "accounts":
            return SimpleNamespace(id=self.CATEGORY_ID, name="Аккаунты", slug="accounts")
        if id == "9d3f77b1-2c4e-4a55-8f60-11aa22bb33cc":
            return SimpleNamespace(id=self.CATEGORY_ID, name="Аккаунты", slug="accounts")
        if id is not None and id.startswith("cat-"):
            raise ValueError("категория не найдена")
        return SimpleNamespace(
            id=id,
            name="Тестовая категория",
            slug="test-category",
            options=[
                SimpleNamespace(field="gender", label="Мужской", group="Пол", value="male"),
                SimpleNamespace(field="gender", label="Женский", group="Пол", value="female"),
                SimpleNamespace(field="game", label="Blox Fruits", group="Игра", value="blox-fruits"),
                SimpleNamespace(field="game", label="Adopt Me", group="Игра", value="adopt-me"),
                SimpleNamespace(field="country", label="Россия", group="Страна", value="russia"),
                SimpleNamespace(field="country", label="Казахстан", group="Страна", value="kazakhstan"),
                SimpleNamespace(field="country", label="Австрия", group="Страна", value="Austria"),
                SimpleNamespace(field="mail", label="Почта", group="Почта", value="true"),
            ],
        )

    def get_game_category_data_fields(self, category_id, obtaining_type_id, count=24, type=None,
                                      after_cursor=None):
        fields = [
            _field("df-login", "Логин", required=True),
            _field("df-pass", "Пароль", required=True, hidden=True),
            _field("df-note", "Примечание"),
        ]
        return SimpleNamespace(
            data_fields=fields,
            page_info=SimpleNamespace(has_next_page=False, end_cursor=None),
            total_count=len(fields),
        )

    def create_item(self, game_category_id, obtaining_type_id, name, price, description,
                    options, data_fields, attachments=None):
        item = SimpleNamespace(
            id=f"item-{len(self.created) + 1}",
            name=name,
            price=price,
            status="DRAFT",
        )
        self.created.append({
            "game_category_id": game_category_id,
            "obtaining_type_id": obtaining_type_id,
            "name": name,
            "price": price,
            "description": description,
            "options": options,
            "data_fields": {f.id: f.value for f in data_fields},
            "attachments": attachments,
            "item": item,
        })
        return item

    def get_item_priority_statuses(self, item_id, item_price):
        return [
            SimpleNamespace(id=plugin.STATUS_FREE_ID, price=0, name="Обычный", type=None),
            SimpleNamespace(id="premium-1", price=25, name="Премиум", type="PREMIUM"),
        ]

    def publish_item(self, item_id, status_id, transaction_provider_id=None):
        self.published.append((item_id, status_id))
        return SimpleNamespace(id=item_id)

    def increase_item_priority_status(self, item_id, status_id, **kwargs):
        self.raised.append((item_id, status_id))
        return SimpleNamespace(id=item_id)

    def get_my_items(self, statuses=None, count=24, after_cursor=None):
        items = [SimpleNamespace(id="item-1", name="Первый лот", price=100, status="APPROVED")]
        return SimpleNamespace(items=items,
                               page_info=SimpleNamespace(has_next_page=False, end_cursor=None))

    def get_games(self, name=None, count=20):
        return SimpleNamespace(games=[SimpleNamespace(id="g1", name="Steam", slug="steam")])



class FakeCardinal:
    def __init__(self):
        self.account = FakeAccount()
        self.telegram = None
        self.MAIN_CFG = {"Playerok": {"restorePriorityMode": "free"}}

    def get_balance(self):
        return SimpleNamespace(available=1000)


CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, details: str = "") -> None:
    CHECKS.append((name, bool(condition), details))
    print(("  [ OK ] " if condition else "  [FAIL] ") + name + (f" — {details}" if details and not condition else ""))


def main() -> int:
    temp_dir = tempfile.mkdtemp(prefix="pkll_test_")
    plugin.SETTINGS_PATH = os.path.join(temp_dir, "settings.json")
    plugin.STATE_PATH = os.path.join(temp_dir, "state.json")
    plugin.COVERS_DIR = os.path.join(temp_dir, "covers")
    plugin.CACHE_DIR = os.path.join(temp_dir, "cache")
    plugin._FIELDS_CACHE.clear()
    os.makedirs(plugin.COVERS_DIR, exist_ok=True)

    # картинки-заглушки
    for number in ("1", "2"):
        with open(os.path.join(plugin.COVERS_DIR, number + ".png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + number.encode())

    cardinal = FakeCardinal()
    plugin.save_settings({"publish": True, "delay": 0.5})

    print("1. Полный формат с шапкой, разные обложки и опции")
    payload = {
        "game_category_id": "1089",
        "obtaining_type_id": "obt-1",
        "publish": True,
        "lots": [
            {
                "name": "Лот один",
                "price": "100",
                "description": "Описание 1",
                "images": ["1", "2"],
                "options": {"Мужской": "male", "Blox Fruits": "blox-fruits",
                            "Россия": "russia", "Почта": "true"},
                "data_fields": {"Логин": "user1", "Пароль": "pass1"},
            },
            {
                "name": "Лот два",
                "price": 150,
                "description": "Описание 2",
                "image": "2",
                "options": {"gender": "female", "Игра": "adopt-me"},
                "data_fields": {"df-login": "user2", "Пароль": "pass2"},
            },
        ],
    }
    plan = plugin.build_plan(cardinal, payload)
    check("нет ошибок", not plan["errors"], str(plan["errors"]))
    check("оба лота собраны", len(plan["lots"]) == 2)
    check("обложки найдены", all(lot["images"] for lot in plan["lots"]),
          str([lot["images"] for lot in plan["lots"]]))
    check("опции сопоставлены по названиям",
          plan["lots"][0]["options"] == {"gender": "male", "game": "blox-fruits",
                                         "country": "russia", "mail": "true"},
          str(plan["lots"][0]["options"]))
    check("опции: старый вариант «Платформа/PC» больше не проходит",
          "неизвестная опция" in " ".join(
              plugin._match_options(cardinal.account.get_game_category(id="1089"),
                                    {"Платформа": "PC"})[1]))
    check("поля данных сопоставлены по названию и по id",
          plan["lots"][1]["data_fields"][0].id == "df-login"
          and plan["lots"][1]["data_fields"][0].value == "user2")

    print("2. Создание лотов и публикация")
    for lot in plan["lots"]:
        ok, message, item = plugin.create_one(cardinal, lot, plugin.load_settings())
        check(f"лот «{lot['name']}» создан", ok, message)
    check("в create_item переданы обе обложки списком",
          len(plan["lots"][0]["images"]) == 2, str(plan["lots"][0]["images"]))
    check("в create_item передана обложка",
          cardinal.account.created[0]["attachments"]
          and cardinal.account.created[0]["attachments"][0].endswith("1.png"),
          str(cardinal.account.created[0]["attachments"]))
    check("лотов опубликовано: 2", len(cardinal.account.published) == 2,
          str(cardinal.account.published))
    check("публикация с бесплатным приоритетом",
          all(status == plugin.STATUS_FREE_ID for _, status in cardinal.account.published))

    print("3. Предупреждения: неизвестное поле, обязательное поле, нет обложки")
    plan = plugin.build_plan(cardinal, {"game_category_id": "1089", "obtaining_type_id": "obt-1",
                                        "lots": [{"name": "Лот", "price": 10,
                                                  "data_fields": {"Что-то": "x"}}]})
    warnings = " ".join(plan["warnings"])
    check("неизвестное поле данных", "неизвестное поле данных" in warnings, warnings)
    check("обязательное поле не заполнено", "обязательное поле" in warnings, warnings)
    check("обложка не найдена", "обложка не указана" in warnings, warnings)
    check("лот всё равно собран", len(plan["lots"]) == 1)

    print("4. Ошибки валидации")
    plan = plugin.build_plan(cardinal, {
        "game_category_id": "1089", "obtaining_type_id": "obt-1",
        "lots": [
            {"name": "", "price": 10},
            {"name": "Без цены"},
            {"name": "Кривая цена", "price": "abc"},
            {"name": "Ноль", "price": 0},
            {"name": "Нет категории"},
        ],
    })
    check("5 ошибок", len(plan["errors"]) == 5, str(plan["errors"]))
    check("лотов не собрано", not plan["lots"])

    print("5. Формат «массив лотов» с категорией в каждом лоте")
    plan = plugin.build_plan(cardinal, [
        {"name": "A", "price": 5, "game_category_id": "1089", "obtaining_type_id": "obt-1",
         "data_fields": {"Логин": "u", "Пароль": "p"}},
    ])
    check("массив поддержан", len(plan["lots"]) == 1 and not plan["errors"], str(plan["errors"]))

    print("6. Черновики без публикации")
    plugin.save_settings({"publish": False})
    cardinal.account.created.clear()
    cardinal.account.published.clear()
    plan = plugin.build_plan(cardinal, {"game_category_id": "1089", "obtaining_type_id": "obt-1",
                                        "publish": False,
                                        "lots": [{"name": "Черновик", "price": 7}]})
    ok, message, _ = plugin.create_one(cardinal, plan["lots"][0], plugin.load_settings())
    check("создан без публикации", ok and "черновиком" in message and not cardinal.account.published,
          message)

    print("7. Автоподнятие")
    plugin.save_settings({"publish": True, "auto_raise": True, "raise_interval_minutes": 240})
    state = plugin.load_state()
    item = SimpleNamespace(id="item-1", name="Первый лот", price=100)
    ok, message = plugin.raise_one(cardinal, item, plugin.load_settings())
    check("raise_one сработал", ok, message)
    check("поднятие бесплатным статусом",
          cardinal.account.raised == [("item-1", plugin.STATUS_FREE_ID)],
          str(cardinal.account.raised))
    check("состояние сохраняется", isinstance(state.get("last_raise"), dict))

    print("8. Премиум-поднятие: только по разрешению и в пределах лимита")
    item = SimpleNamespace(id="item-1", name="Первый лот", price=100)
    check("по умолчанию премиум запрещён",
          plugin._pick_priority_status(cardinal, item, {"allow_premium": False})
          == plugin.STATUS_FREE_ID)
    check("премиум дороже лимита не берётся",
          plugin._pick_priority_status(
              cardinal, item, {"allow_premium": True, "premium_max_price": 10})
          == plugin.STATUS_FREE_ID)
    check("премиум в пределах лимита берётся",
          plugin._pick_priority_status(
              cardinal, item, {"allow_premium": True, "premium_max_price": 100}) == "premium-1")

    cardinal.account.get_item_priority_statuses = lambda item_id, price: []
    check("нет списка статусов — бесплатный",
          plugin._pick_priority_status(cardinal, item, {"allow_premium": True}) == plugin.STATUS_FREE_ID)

    print("9. Несколько обложек (до 10)")
    for number in range(3, 13):
        with open(os.path.join(plugin.COVERS_DIR, f"{number}.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + str(number).encode())

    many = [str(i) for i in range(1, 13)]
    plan = plugin.build_plan(cardinal, {"game_category_id": "1089", "obtaining_type_id": "obt-1",
                                        "lots": [{"name": "Много обложек", "price": 10,
                                                  "images": many}]})
    check("12 обложек обрезаны до 10", len(plan["lots"][0]["images"]) == 10,
          str(len(plan["lots"][0]["images"])))
    check("про обрезку есть предупреждение",
          any("не больше 10" in w for w in plan["warnings"]), str(plan["warnings"]))

    plan = plugin.build_plan(cardinal, {"game_category_id": "1089", "obtaining_type_id": "obt-1",
                                        "lots": [{"name": "Одна строка", "price": 10, "image": "1"},
                                                 {"name": "Дубли", "price": 10,
                                                  "images": ["2", "2"]},
                                                 {"name": "Нет файлов", "price": 10,
                                                  "images": ["nope.png"]}]})
    check("строка вместо списка работает", len(plan["lots"][0]["images"]) == 1,
          str(plan["lots"][0]["images"]))
    check("дубликаты не дублируются", len(plan["lots"][1]["images"]) == 1,
          str(plan["lots"][1]["images"]))
    check("ненайденный файл — предупреждение и пусто",
          not plan["lots"][2]["images"]
          and any("не найдена" in w for w in plan["warnings"]), str(plan["warnings"]))

    cardinal.account.created.clear()
    for lot in plan["lots"]:
        plugin.create_one(cardinal, lot, plugin.load_settings())
    check("в create_item ушёл список файлов",
          isinstance(cardinal.account.created[0]["attachments"], list)
          and cardinal.account.created[0]["attachments"][0].endswith("1.png"),
          str(cardinal.account.created[0]["attachments"]))

    print("10. publish: false из JSON делает черновик")
    cardinal.account.created.clear()
    cardinal.account.published.clear()
    plugin.save_settings({"publish": True})
    plan = plugin.build_plan(cardinal, {"game_category_id": "1089", "obtaining_type_id": "obt-1",
                                        "publish": False,
                                        "lots": [{"name": "Черновик из JSON", "price": 9}]})
    check("план помечен как черновик", plan["publish"] is False, str(plan["publish"]))
    check("лот помечен как черновик", plan["lots"][0]["publish"] is False,
          str(plan["lots"][0]["publish"]))
    ok, message, _ = plugin.create_one(cardinal, plan["lots"][0], plugin.load_settings())
    check("создан без публикации, хотя в настройках publish=true",
          ok and "черновиком" in message and not cardinal.account.published, message)

    plan = plugin.build_plan(cardinal, {"game_category_id": "1089", "obtaining_type_id": "obt-1",
                                        "lots": [{"name": "Черновик у лота", "price": 9,
                                                  "publish": False},
                                                 {"name": "Публикация у лота", "price": 9}]})
    check("publish можно задать у отдельного лота",
          plan["lots"][0]["publish"] is False and plan["lots"][1]["publish"] is True,
          str([lot["publish"] for lot in plan["lots"]]))

    print("11. Характеристики: группы и проверка значений")
    category = cardinal.account.get_game_category(id="1089")
    schema = plugin.build_option_schema(category)
    check("группы собраны", set(schema) == {"пол", "игра", "страна", "почта"}, str(list(schema)))
    check("в группе «Страна» три значения",
          len(schema["страна"]["options"]) == 3, str(len(schema["страна"]["options"])))

    example = plugin.example_options(category)
    check("в шаблоне по одному значению на группу", len(example) == 4, str(example))

    options, warns = plugin._match_options(category, {"Страна": "kazakhstan"})
    check("выбор по названию группы", options == {"country": "kazakhstan"}, str(options))
    options, warns = plugin._match_options(category, {"Страна": "Казахстан"})
    check("значение можно писать по-русски", options == {"country": "kazakhstan"}, str(options))
    options, warns = plugin._match_options(category, {"country": "Austria"})
    check("значение без учёта регистра", options == {"country": "Austria"}, str(options))

    options, warns = plugin._match_options(category, {"Страна": "Атлантида"})
    check("недопустимое значение отклонено", options == {} and any(
        "недопустимое значение" in w for w in warns), str(warns))

    options, warns = plugin._match_options(category, {"Россия": "russia", "Казахстан": "kazakhstan"})
    check("две страны → последняя + предупреждение",
          options == {"country": "kazakhstan"}
          and any("несколько значений" in w for w in warns), str((options, warns)))

    options, warns = plugin._match_options(category, {"Почта": "true"})
    check("переключатель → true", options == {"mail": "true"}, str(options))
    options, warns = plugin._match_options(category, {"Почта": "false"})
    check("переключатель false → подсказка убрать",
          options == {} and any("выключена" in w for w in warns), str(warns))

    text = plugin.format_option_schema(category)
    check("в списке характеристик есть группы и значения",
          "Страна" in text and "kazakhstan" in text and "Австрия" in text, text[:200])

    print("12. Поиск категории: UUID игры, UUID категории, название")
    check("UUID распознаётся как ID", plugin._is_id("1ecc48ce-4f1a-6531-300d-9faaa8c3ab04"))
    check("число распознаётся как ID", plugin._is_id("1089"))
    check("slug — не ID", not plugin._is_id("accounts"))
    check("пустая строка — не ID", not plugin._is_id(""))

    found = plugin.resolve_query(cardinal.account, "1ecc48ce-4f1a-6531-300d-9faaa8c3ab04")
    check("UUID игры → игра с категориями", found["kind"] == "game", found["kind"])
    check("категории подтянулись", len(found.get("game").categories) == 2)

    found = plugin.resolve_query(cardinal.account, "9d3f77b1-2c4e-4a55-8f60-11aa22bb33cc")
    check("UUID категории → категория", found["kind"] == "category", found["kind"])

    found = plugin.resolve_query(cardinal.account, "Roblox")
    check("название → поиск", found["kind"] == "search", found["kind"])

    found = plugin.resolve_query(cardinal.account, "cat-unknown")
    check("неизвестный id → поиск, без падения", found["kind"] == "search", found["kind"])

    print("13. Готовые команды в подсказках")
    games_text = plugin.format_games([SimpleNamespace(
        id="1ecc48ce-4f1a-6531-300d-9faaa8c3ab04", name="Roblox", slug="roblox")])
    check("в списке игр есть команда /pk_cat с UUID",
          "/pk_cat 1ecc48ce-4f1a-6531-300d-9faaa8c3ab04" in games_text, games_text)

    cat_text = plugin.format_categories(
        plugin.resolve_query(cardinal.account, "1ecc48ce-4f1a-6531-300d-9faaa8c3ab04")["game"])
    check("в списке категорий есть /pk_template с ID категории",
          "/pk_template 9d3f77b1-2c4e-4a55-8f60-11aa22bb33cc" in cat_text, cat_text)
    check("подсказка про slug-путь",
          "/pk_template 1ecc48ce-4f1a-6531-300d-9faaa8c3ab04 accounts" in cat_text, cat_text)

    print("14. Аргументы /pk_template")
    parsed = plugin.parse_template_args(
        cardinal.account, ["9d3f77b1-2c4e-4a55-8f60-11aa22bb33cc"])
    check("только ID категории", parsed == {"category_id": "9d3f77b1-2c4e-4a55-8f60-11aa22bb33cc",
                                            "obtaining_type_id": "", "error": ""}, str(parsed))

    parsed = plugin.parse_template_args(
        cardinal.account, ["1ecc48ce-4f1a-6531-300d-9faaa8c3ab04", "accounts"])
    check("игра + slug → ID категории",
          parsed["category_id"] == "9d3f77b1-2c4e-4a55-8f60-11aa22bb33cc" and not parsed["error"],
          str(parsed))

    parsed = plugin.parse_template_args(
        cardinal.account, ["1ecc48ce-4f1a-6531-300d-9faaa8c3ab04", "no-such"])
    check("несуществующий slug → понятная ошибка",
          bool(parsed["error"]) and not parsed["category_id"], str(parsed))

    parsed = plugin.parse_template_args(cardinal.account, ["accounts"])
    check("slug вместо ID → ошибка", bool(parsed["error"]), str(parsed))

    parsed = plugin.parse_template_args(
        cardinal.account, ["9d3f77b1-2c4e-4a55-8f60-11aa22bb33cc",
                           "1efbe5bc-99a7-68e5-4534-85dad913b981"])
    check("категория + способ получения",
          parsed["obtaining_type_id"] == "1efbe5bc-99a7-68e5-4534-85dad913b981", str(parsed))

    print("15. Настройки: переключение автоподнятия и чтение из файла")
    plugin.save_settings({"auto_raise": True, "raise_interval_minutes": 60})
    loaded = plugin.load_settings()
    check("настройки читаются", loaded["auto_raise"] is True and loaded["raise_interval_minutes"] == 60)
    check("неизвестные ключи игнорируются",
          "unknown" not in plugin.load_settings())

    failed = [name for name, ok, _ in CHECKS if not ok]
    print()
    print("=" * 50)
    if failed:
        print(f"Провалено: {len(failed)} из {len(CHECKS)}")
        for name in failed:
            print("  -", name)
        return 1
    print(f"Все проверки пройдены: {len(CHECKS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
