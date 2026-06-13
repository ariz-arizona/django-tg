import json
import os
import time
from datetime import datetime

import requests
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from roster.models.team import Card, Team, Season
from tg_bot.models import Bot, BotFile


class Command(BaseCommand):
    help = 'Загружает карты из data/data.json с изображениями из data/img/'

    def add_arguments(self, parser):
        parser.add_argument(
            '--bot-id',
            type=int,
            required=True,
            help='ID бота из tg_bot.Bot',
        )
        parser.add_argument(
            '--chat-id',
            type=str,
            required=True,
            help='ID чата для загрузки фото (например, @channel или числовой ID)',
        )
        parser.add_argument(
            '--timeout',
            type=float,
            default=2.0,
            help='Задержка между загрузками фото в секундах (по умолчанию: 2.0)',
        )

    def handle(self, *args, **options):
        bot_id = options['bot_id']
        chat_id = options['chat_id']
        delay = options['timeout']

        # Фиксированные пути
        source_dir = os.path.join(settings.BASE_DIR, 'data')
        json_path = os.path.join(source_dir, 'data.json')
        images_dir = os.path.join(source_dir, 'img')

        # Проверяем существование файлов
        if not os.path.exists(json_path):
            raise CommandError(f'JSON не найден: {json_path}')
        if not os.path.exists(images_dir):
            raise CommandError(f'Папка с изображениями не найдена: {images_dir}')

        # Получаем бота и его токен
        try:
            bot = Bot.objects.get(id=bot_id, is_enabled=True)
        except Bot.DoesNotExist:
            raise CommandError(f'Бот с ID {bot_id} не найден или отключен')

        token = bot.token

        # Загружаем JSON
        self.stdout.write(f'📄 Читаю JSON: {json_path}')
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # === Создаём или получаем сезон ===
        season_data = data['season']
        season_name = season_data['name']

        season, season_created = Season.objects.get_or_create(
            name=season_name,
            defaults={
                'start_date': parse_datetime(season_data['start_date']) or datetime.now(),
                'end_date': parse_datetime(season_data['end_date']) or datetime.now(),
                'is_active': season_data.get('is_active', True),
                'bot': bot,
            },
        )

        if season_created:
            self.stdout.write(self.style.SUCCESS(
                f'\n✅ Создан сезон: {season_name}'
            ))
        else:
            self.stdout.write(
                f'\n🔄 Сезон уже существует: {season_name}'
            )

        total_teams = 0
        total_cards = 0
        total_backgrounds = 0
        success_images = 0

        # ContentType для Team и Card
        team_ct = ContentType.objects.get_for_model(Team)
        card_ct = ContentType.objects.get_for_model(Card)

        for team_data in data['teams']:
            team_name = team_data['name']
            team_stars = team_data.get('stars', 1)

            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n=== Команда: {team_name} ({team_stars}⭐) ==='
            ))

            # Создаём или получаем команду
            team, created = Team.objects.get_or_create(
                season=season,
                name=team_name,
                defaults={'stars': team_stars},
            )

            if created:
                self.stdout.write(self.style.SUCCESS(f'  ✅ Создана команда: {team_name}'))
                total_teams += 1
            else:
                if team.stars != team_stars:
                    team.stars = team_stars
                    team.save(update_fields=['stars'])
                self.stdout.write(f'  🔄 Команда существует: {team_name}')

            # Обрабатываем элементы (и фоны, и карты)
            for item_data in team_data.get('items', []):
                item_name = item_data['name']
                item_stars = item_data.get('stars', 1)
                item_description = item_data.get('description', '')
                image_filename = item_data.get('image', '')

                # Определяем, это фон или карта
                is_background = item_name.startswith('Фон команды:')

                if is_background:
                    # === ОБРАБОТКА ФОНА КОМАНДЫ ===
                    self.stdout.write(f'  🖼️  Фон команды: {item_name}')
                    
                    if not image_filename:
                        self.stderr.write(self.style.WARNING(f'    ⚠️  Нет изображения для фона'))
                        continue

                    bg_path = os.path.join(images_dir, image_filename)

                    if not os.path.exists(bg_path):
                        self.stderr.write(self.style.WARNING(
                            f'    ⚠️  Файл фона не найден: {bg_path}'
                        ))
                        continue

                    # Загружаем фон через Telegram API так же, как и карты
                    self.stdout.write(f'    📤 Загружаю фон: {image_filename}')
                    file_id = self.upload_photo(token, chat_id, bg_path)

                    if file_id:
                        # Удаляем старые фоны для этой команды
                        BotFile.objects.filter(
                            bot=bot,
                            content_type=team_ct,
                            object_id=team.id,
                        ).delete()

                        # Сохраняем file_id для фона команды
                        BotFile.objects.create(
                            bot=bot,
                            content_type=team_ct,
                            object_id=team.id,
                            file_id=file_id,
                        )
                        self.stdout.write(self.style.SUCCESS(f'    ✅ Фон сохранён: {file_id}'))
                        total_backgrounds += 1
                        success_images += 1
                        
                        # Задержка между загрузками
                        self.stdout.write(f'    ⏳ Ожидание {delay}с...')
                        time.sleep(delay)
                    else:
                        self.stderr.write(self.style.ERROR(f'    ❌ Ошибка загрузки фона'))

                else:
                    # === ОБРАБОТКА КАРТЫ ===
                    self.stdout.write(f'  📇 Карта: {item_name} ({item_stars}⭐)')

                    # Создаём или обновляем карту
                    card, created = Card.objects.update_or_create(
                        team=team,
                        name=item_name,
                        defaults={
                            'stars': item_stars,
                            'description': item_description,
                        },
                    )

                    if created:
                        self.stdout.write(self.style.SUCCESS(f'    ✅ Создана'))
                    else:
                        self.stdout.write(f'    🔄 Обновлена')

                    total_cards += 1

                    # Загружаем изображение карты (через Telegram API как file_id)
                    if image_filename:
                        image_path = os.path.join(images_dir, image_filename)

                        if not os.path.exists(image_path):
                            self.stderr.write(self.style.WARNING(
                                f'    ⚠️  Файл не найден: {image_path}'
                            ))
                            continue

                        self.stdout.write(f'    📤 Загружаю: {image_filename}')
                        file_id = self.upload_photo(token, chat_id, image_path)

                        if file_id:
                            # Удаляем старые BotFile для этой карты
                            BotFile.objects.filter(
                                bot=bot,
                                content_type=card_ct,
                                object_id=card.id,
                            ).delete()

                            # Создаём новый BotFile для карты (с file_id)
                            BotFile.objects.create(
                                bot=bot,
                                content_type=card_ct,
                                object_id=card.id,
                                file_id=file_id,
                            )
                            self.stdout.write(self.style.SUCCESS(f'    🖼️  file_id: {file_id}'))
                            success_images += 1

                            # Задержка между загрузками
                            self.stdout.write(f'    ⏳ Ожидание {delay}с...')
                            time.sleep(delay)
                        else:
                            self.stderr.write(self.style.ERROR(f'    ❌ Ошибка загрузки'))

        self.stdout.write(self.style.SUCCESS(
            f'\n{"="*50}\n'
            f'📊 Импорт завершён:\n'
            f'  • Сезон: {season_name} ({"новый" if season_created else "существующий"})\n'
            f'  • Команд: {total_teams} (создано)\n'
            f'  • Фонов команд: {total_backgrounds}\n'
            f'  • Карт: {total_cards}\n'
            f'  • Изображений: {success_images}\n'
            f'{"="*50}'
        ))

    def upload_photo(self, token, chat_id, image_path):
        """Загружает фото в Telegram и возвращает file_id."""
        url = f'https://api.telegram.org/bot{token}/sendPhoto'

        try:
            with open(image_path, 'rb') as photo:
                response = requests.post(
                    url,
                    data={'chat_id': chat_id},
                    files={'photo': photo},
                    timeout=30,
                )

            if response.status_code == 200:
                result = response.json()
                if result.get('ok'):
                    photo_sizes = result['result']['photo']
                    largest = max(photo_sizes, key=lambda x: x.get('file_size', 0))
                    return largest['file_id']
                else:
                    self.stderr.write(f'    Telegram API: {result.get("description")}')
                    return None
            else:
                self.stderr.write(f'    HTTP {response.status_code}: {response.text[:200]}')
                return None

        except Exception as e:
            self.stderr.write(f'    Ошибка: {e}')
            return None