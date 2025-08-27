import json
from django.core.management.base import BaseCommand
from django.conf import settings
from tarot.models import TarotCard, ExtendedMeaning, TarotMeaningCategory
from server.logger import logger


class Command(BaseCommand):
    help = "Загружает данные о картах Таро из JSON-файла в базу данных."

    def handle(self, *args, **kwargs):
        # Путь к JSON-файлу
        json_file_path = settings.BASE_DIR / "tg_bot" / "tarot_data" / "cards.json"

        # Чтение JSON-файла
        with open(json_file_path, "r", encoding="utf-8") as file:
            tarot_data = json.load(file)

        for card_id, card_data in tarot_data.items():
            # Создание или обновление карты
            tarot_card, created = TarotCard.objects.update_or_create(
                card_id=card_id,  # Используем ключ из JSON как card_id
                defaults={
                    "name": card_data["name"],
                    "meaning": card_data["meaning"],
                    "meaning_url": card_data.get("meaning_url", ""),
                },
            )

            # Создание расширенных значений
            for category, text in card_data["extended_meanings"].items():
                cat, _ = TarotMeaningCategory.objects.get_or_create(name=category)
                ExtendedMeaning.objects.update_or_create(
                    tarot_card=tarot_card,
                    category_base=cat,
                    category=category,
                    defaults={"text": text},
                )

            logger.info(
                self.style.SUCCESS(f"Карта '{card_data['name']}' успешно загружена.")
            )

        logger.info(self.style.SUCCESS("Все данные успешно загружены в базу данных."))
