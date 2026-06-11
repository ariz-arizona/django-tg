# roster/management/commands/upload_card_images.py

import requests
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from roster.models.team import Card, Team
from tg_bot.models import Bot, BotFile


# Открытые картинки карт
CARD_IMAGES = {
    # Синяя команда
    "Циклоп": {
        "image": "img/cyclops.jpg",
    },
    "Росомаха": {
        "image": "img/wolverine.jpg",
    },
    "Шторм": {
        "image": "img/storm.jpg",
    },
    "Зверь": {
        "image": "img/beast.jpg",
    },
    "Джубили": {
        "image": "img/jubilee.jpg",
    },
    # Фантастическая четвёрка
    "Мистер Фантастик": {
        "image": "img/mr_fantastic.jpg",
    },
    "Невидимая Леди": {
        "image": "img/invisible_woman.jpg",
    },
    "Человек-Факел": {
        "image": "img/human_torch.jpg",
    },
    "Существо": {
        "image": "img/the_thing.jpg",
    },
    "Франклин Ричардс": {
        "image": "img/franklin_richards.jpg",
    },
    # Громовержцы*
    "Баки Барнс": {
        "image": "img/bucky_barnes.png",
    },
    "Елена Белова": {
        "image": "img/yelena_belova.jpg",
    },
    "Красный Страж": {
        "image": "img/red_guardian.jpg",
    },
    "Джон Уокер": {
        "image": "img/john_walker.jpg",
    },
    "Таскмастер": {
        "image": "img/taskmaster.jpg",
    },
    # Могучие Мстители
    "Капитан Америка": {
        "image": "img/captain_america.jpg",
    },
    "Железный Человек": {
        "image": "img/iron_man.jpg",
    },
    "Тор": {
        "image": "img/thor.webp",
    },
    "Халк": {
        "image": "img/hulk.jpg",
    },
    "Чёрная Вдова": {
        "image": "img/black_widow.jpg",
    },
}

# Картинки команд
TEAM_IMAGES = {
    "Синяя команда": "img/teams/blue_team.jpg",
    "Фантастическая четвёрка": "img/teams/fantastic_four.jpg",
    "Громовержцы*": "img/teams/thunderbolts.jpg",
    "Могучие Мстители": "img/teams/mighty_avengers.jpg",
}


class Command(BaseCommand):
    help = 'Загружает картинки карт и команд через Telegram Bot API'

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

        # --- Загрузка картинок карт ---
        total_cards = len(CARD_IMAGES)
        success_cards = 0

        self.stdout.write(self.style.MIGRATE_HEADING('\n=== Загрузка картинок карт ==='))
        for card_name, filenames in CARD_IMAGES.items():
            self.stdout.write(f'Обрабатываю карту: {card_name}...')

            try:
                card = Card.objects.get(name__iexact=card_name)
            except Card.DoesNotExist:
                self.stderr.write(self.style.WARNING(f'  Карта не найдена: {card_name}'))
                continue
            except Card.MultipleObjectsReturned:
                self.stderr.write(self.style.WARNING(f'  Несколько карт: {card_name}'))
                continue

            image_file_id = self.upload_photo(token, chat_id, filenames['image'])
            if image_file_id:
                card.image.update_or_create(
                    bot=bot,
                    defaults={'file_id': image_file_id},
                )
                self.stdout.write(self.style.SUCCESS(f'  ✅ image → {image_file_id}'))
                success_cards += 1
            else:
                self.stderr.write(self.style.ERROR(f'    ERROR'))

        # --- Загрузка картинок команд ---
        total_teams = len(TEAM_IMAGES)
        success_teams = 0

        self.stdout.write(self.style.MIGRATE_HEADING('\n=== Загрузка картинок команд ==='))
        for team_name, image_path in TEAM_IMAGES.items():
            self.stdout.write(f'Обрабатываю команду: {team_name}...')

            try:
                team = Team.objects.get(name__iexact=team_name)
            except Team.DoesNotExist:
                self.stderr.write(self.style.WARNING(f'  Команда не найдена: {team_name}'))
                continue
            except Team.MultipleObjectsReturned:
                self.stderr.write(self.style.WARNING(f'  Несколько команд: {team_name}'))
                continue

            image_file_id = self.upload_photo(token, chat_id, image_path)
            if image_file_id:
                team.files.update_or_create(
                    bot=bot,
                    defaults={'file_id': image_file_id},
                )
                self.stdout.write(self.style.SUCCESS(f'  ✅ team image → {image_file_id}'))
                success_teams += 1
            else:
                self.stderr.write(self.style.ERROR(f'    ERROR'))

        # --- Итоги ---
        self.stdout.write(self.style.SUCCESS(
            f'\nГотово!'
            f'\n  Карты: {success_cards} из {total_cards}'
            f'\n  Команды: {success_teams} из {total_teams}'
        ))

    def upload_photo(self, token: str, chat_id: str, file_path: str) -> str | None:
        """Загружает фото через Telegram API и возвращает file_id самой большой версии."""
        url = f'https://api.telegram.org/bot{token}/sendPhoto'

        try:
            with open(file_path, 'rb') as f:
                response = requests.post(
                    url,
                    data={'chat_id': chat_id},
                    files={'photo': f},
                    timeout=30,
                )
            data = response.json()

            if not data.get('ok'):
                self.stderr.write(f'    Ошибка API: {data.get("description")}')
                return None

            result = data['result']
            photo = result.get('photo', [])

            if photo:
                largest = max(photo, key=lambda p: p.get('file_size', 0))
                return largest.get('file_id', '')
            else:
                self.stderr.write('    Пустой массив photo в ответе API')
                return None

        except Exception as e:
            self.stderr.write(f'    Ошибка: {e}')
            return None