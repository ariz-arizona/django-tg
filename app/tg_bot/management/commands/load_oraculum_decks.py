import json
from django.core.management.base import BaseCommand
from django.conf import settings
from tg_bot.models import OraculumItem, OraculumDeck
from server.logger import logger


class Command(BaseCommand):
    help = "Загружает колоды и карты из JSON-файлов в папке decks."

    def handle(self, *args, **kwargs):
        # Путь к файлу decks.json
        decks_file_path = settings.BASE_DIR / "tg_bot" / "tarot_data" / "oraculum.json"

        # Проверяем, существует ли файл
        if not decks_file_path.exists():
            self.stdout.write(self.style.ERROR(f"Файл {decks_file_path} не найден."))
            return

        # Чтение файла decks.json
        with open(decks_file_path, "r", encoding="utf-8") as file:
            decks_data = json.load(file)

        # Путь к папке с картами
        decks_dir = settings.BASE_DIR / "tg_bot" / "tarot_data" / "oraculum"

        # Проходим по всем колодам
        for deck_data in decks_data:
            # Создание или обновление колоды
            deck, created = OraculumDeck.objects.update_or_create(name=deck_data)

            # Путь к файлу с картами для этой колоды
            deck_file_path = decks_dir / f"{deck_data}.json"

            # Проверяем, существует ли файл с картами
            if not deck_file_path.exists():
                logger.info(
                    self.style.WARNING(
                        f"Файл с картами для колоды '{deck.name}' не найден: {deck_file_path}"
                    )
                )
                continue

            # Чтение файла с картами
            with open(deck_file_path, "r", encoding="utf-8") as file:
                cards_data = json.load(file)

            # Обработка карт в колоде
            for card_data in cards_data:
                OraculumItem.objects.get_or_create(
                    deck=deck,
                    name=card_data["name"],
                    defaults={
                        "img_id":card_data["fileId"],
                        "description": card_data.get("description", None),
                        "direct": card_data.get("direct", None),
                        "inverted": card_data.get("inverted", None),
                    },
                )
            logger.info(self.style.SUCCESS(f"Колода '{deck.name}' успешно загружена."))

        logger.info(
            self.style.SUCCESS("Все колоды и карты успешно загружены в базу данных.")
        )
