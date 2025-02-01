import json
from django.core.management.base import BaseCommand
from tg_bot.models import Rune
from django.conf import settings

class Command(BaseCommand):
    help = "Загружает данные о рунах из JSON-файла"

    def handle(self, *args, **kwargs):
        json_file_path = settings.BASE_DIR / "tg_bot" / "tarot_data" / "futark.json"

        with open(json_file_path, "r", encoding="utf-8") as file:
            runes_data = json.load(file)

        for rune_data in runes_data:
            # Извлечение данных о рунах
            rune_meaning = rune_data.get("meaning", {})

            # Проверка наличия "straight" и "inverted"
            straight_data = rune_meaning.get("straight", {})
            inverted_data = rune_meaning.get("inverted", {})

            # Создание объекта Rune
            Rune.objects.create(
                type=rune_data["type"],
                symbol=rune_data["symbol"],
                sticker=rune_data["sticker"],
                straight_keys=straight_data.get("keys", []),
                straight_meaning=straight_data.get("meaning", ""),
                straight_pos_1=straight_data.get("pos_1", ""),
                straight_pos_2=straight_data.get("pos_2", ""),
                straight_pos_3=straight_data.get("pos_3", ""),
                inverted_keys=inverted_data.get("keys", []) if inverted_data else [],
                inverted_meaning=inverted_data.get("meaning", "") if inverted_data else "",
                inverted_pos_1=inverted_data.get("pos_1", "") if inverted_data else "",
                inverted_pos_2=inverted_data.get("pos_2", "") if inverted_data else "",
                inverted_pos_3=inverted_data.get("pos_3", "") if inverted_data else "",
            )

        self.stdout.write(self.style.SUCCESS("Данные о рунах успешно загружены!"))