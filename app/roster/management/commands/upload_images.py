# roster/management/commands/upload_card_images.py

import asyncio
from django.core.management.base import BaseCommand
from tg_bot.models import Bot, BotFile
from roster.models.team import Card
import requests


# Заглушка: имена файлов для всех карт из первой миграции
CARD_IMAGES = {
    # Синяя команда
    "Циклоп": {
        "image": "cyclops.jpg",
        "hidden": "cyclops_hidden.jpg",
    },
    "Росомаха": {
        "image": "wolverine.jpg",
        "hidden": "wolverine_hidden.jpg",
    },
    "Шторм": {
        "image": "storm.jpg",
        "hidden": "storm_hidden.jpg",
    },
    "Зверь": {
        "image": "beast.jpg",
        "hidden": "beast_hidden.jpg",
    },
    "Джубили": {
        "image": "jubilee.jpg",
        "hidden": "jubilee_hidden.jpg",
    },
    # Фантастическая четвёрка
    "Мистер Фантастик": {
        "image": "mr_fantastic.jpg",
        "hidden": "mr_fantastic_hidden.jpg",
    },
    "Невидимая Леди": {
        "image": "invisible_woman.jpg",
        "hidden": "invisible_woman_hidden.jpg",
    },
    "Человек-Факел": {
        "image": "human_torch.jpg",
        "hidden": "human_torch_hidden.jpg",
    },
    "Существо": {
        "image": "the_thing.jpg",
        "hidden": "the_thing_hidden.jpg",
    },
    "Франклин Ричардс": {
        "image": "franklin_richards.jpg",
        "hidden": "franklin_richards_hidden.jpg",
    },
    # Громовержцы*
    "Баки Барнс": {
        "image": "bucky_barnes.jpg",
        "hidden": "bucky_barnes_hidden.jpg",
    },
    "Елена Белова": {
        "image": "yelena_belova.jpg",
        "hidden": "yelena_belova_hidden.jpg",
    },
    "Красный Страж": {
        "image": "red_guardian.jpg",
        "hidden": "red_guardian_hidden.jpg",
    },
    "Джон Уокер": {
        "image": "john_walker.jpg",
        "hidden": "john_walker_hidden.jpg",
    },
    "Таскмастер": {
        "image": "taskmaster.jpg",
        "hidden": "taskmaster_hidden.jpg",
    },
    # Могучие Мстители
    "Капитан Америка": {
        "image": "captain_america.jpg",
        "hidden": "captain_america_hidden.jpg",
    },
    "Железный Человек": {
        "image": "iron_man.jpg",
        "hidden": "iron_man_hidden.jpg",
    },
    "Тор": {
        "image": "thor.jpg",
        "hidden": "thor_hidden.jpg",
    },
    "Халк": {
        "image": "hulk.jpg",
        "hidden": "hulk_hidden.jpg",
    },
    "Чёрная Вдова": {
        "image": "black_widow.jpg",
        "hidden": "black_widow_hidden.jpg",
    },
}


class Command(BaseCommand):
    help = 'Загружает открытые и скрытые картинки карт через Telegram Bot API'

    def add_arguments(self, parser):
        parser.add_argument('--bot-id', type=int, required=True, help='ID бота в базе')
        parser.add_argument('--chat-id', type=str, required=True, help='Chat ID для загрузки файлов')

    def handle(self, *args, **options):
        bot_id = options['bot_id']
        chat_id = options['chat_id']

        # Получаем бота
        try:
            bot = Bot.objects.get(id=bot_id, is_enabled=True)
        except Bot.DoesNotExist:
            self.stderr.write(self.style.ERROR(f'Бот с ID {bot_id} не найден или отключен'))
            return

        token = bot.token
        total = len(CARD_IMAGES)
        success = 0

        for card_name, filenames in CARD_IMAGES.items():
            self.stdout.write(f'Обрабатываю: {card_name}...')

            # Ищем карту
            try:
                card = Card.objects.get(name__iexact=card_name)
            except Card.DoesNotExist:
                self.stderr.write(self.style.WARNING(f'  Карта не найдена: {card_name}'))
                continue
            except Card.MultipleObjectsReturned:
                self.stderr.write(self.style.WARNING(f'  Несколько карт: {card_name}'))
                continue

            # Открытая картинка
            image_file_id = self.upload_file(token, chat_id, filenames['image'])
            if image_file_id:
                BotFile.objects.update_or_create(
                    content_type=Card.get_content_type(),
                    object_id=card.id,
                    bot=bot,
                    file_type='image',
                    defaults={'file_id': image_file_id},
                )
                self.stdout.write(self.style.SUCCESS(f'  ✅ image → {image_file_id}'))
                success += 1
            else:
                self.stderr.write(self.style.ERROR(f'  ❌ не загрузилось: {filenames["image"]}'))

            # Скрытая картинка
            hidden_file_id = self.upload_file(token, chat_id, filenames['hidden'])
            if hidden_file_id:
                BotFile.objects.update_or_create(
                    content_type=Card.get_content_type(),
                    object_id=card.id,
                    bot=bot,
                    file_type='image_hidden',
                    defaults={'file_id': hidden_file_id},
                )
                self.stdout.write(self.style.SUCCESS(f'  ✅ image_hidden → {hidden_file_id}'))
                success += 1
            else:
                self.stderr.write(self.style.ERROR(f'  ❌ не загрузилось: {filenames["hidden"]}'))

        self.stdout.write(self.style.SUCCESS(f'\nГотово! Загружено {success} из {total * 2} файлов.'))

    def upload_file(self, token: str, chat_id: str, file_path: str) -> str | None:
        """Загружает файл через Telegram API и возвращает file_id самой большой версии."""
        url = f'https://api.telegram.org/bot{token}/sendDocument'

        try:
            with open(file_path, 'rb') as f:
                response = requests.post(
                    url,
                    data={'chat_id': chat_id},
                    files={'document': f},
                    timeout=30,
                )
            data = response.json()

            if not data.get('ok'):
                self.stderr.write(f'    Ошибка API: {data.get("description")}')
                return None

            result = data['result']
            document = result.get('document', {})

            # Берём самый большой размер из миниатюр
            thumbs = document.get('thumbnails', document.get('thumbs', []))
            if thumbs:
                largest = max(thumbs, key=lambda t: t.get('file_size', 0))
                return largest.get('file_id', '')
            else:
                return document.get('file_id', '')

        except Exception as e:
            self.stderr.write(f'    Ошибка: {e}')
            return None