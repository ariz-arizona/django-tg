# management/commands/import_cards_from_json.py

import json
import requests
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.files.base import ContentFile
from django.contrib.contenttypes.models import ContentType

from tarot.models import TarotCardItem, TarotDeck, TarotCard
from tg_bot.models import Bot, BotFile, BotFileCache


class Command(BaseCommand):
    help = "Импортирует карты из JSON файла, создаёт карты и загружает картинки в бота"

    def add_arguments(self, parser):
        parser.add_argument(
            "--json_file",
            type=str,
            required=True,
            help="Путь к JSON файлу с данными карт"
        )
        parser.add_argument(
            "--bot_id",
            type=int,
            required=True,
            help="ID бота для загрузки картинок"
        )
        parser.add_argument(
            "--chat_id",
            type=str,
            required=True,
            help="ID чата для загрузки картинок"
        )
        parser.add_argument(
            "--deck_id",
            type=int,
            default=None,
            help="ID существующей колоды (если не указан, будет создана новая)"
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=5,
            help="Таймаут в секундах"
        )

    def handle(self, *args, **options):
        json_file_path = options["json_file"]
        bot_id = options["bot_id"]
        chat_id = options["chat_id"]
        deck_id = options.get("deck_id")
        timeout = options.get("timeout", 5)

        # Проверяем существование JSON файла
        json_path = Path(json_file_path)
        if not json_path.exists():
            raise CommandError(f"JSON файл не найден: {json_file_path}")

        # Проверяем существование бота
        try:
            bot = Bot.objects.get(id=bot_id, is_enabled=True)
        except Bot.DoesNotExist:
            raise CommandError(
                f"Бот с ID {bot_id} не найден или отключен"
            )

        # Получаем или создаём колоду
        if deck_id:
            try:
                deck = TarotDeck.objects.get(id=deck_id)
                self.stdout.write(
                    self.style.SUCCESS(f"Используем существующую колоду: {deck.name} (ID: {deck.id})")
                )
            except TarotDeck.DoesNotExist:
                raise CommandError(f"Колода с ID {deck_id} не найдена")
        else:
            # Создаём новую колоду
            deck_name = json_path.stem  # Имя файла без расширения как название колоды
            deck, created = TarotDeck.objects.get_or_create(
                name=deck_name,
                defaults={
                    "slug": deck_name.lower().replace(" ", "-"),
                    "is_active": True
                }
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f"Создана новая колода: {deck.name} (ID: {deck.id})")
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f"Колода уже существует: {deck.name} (ID: {deck.id})")
                )

        # Читаем JSON файл
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                cards_data = json.load(f)
        except json.JSONDecodeError as e:
            raise CommandError(f"Ошибка парсинга JSON файла: {e}")
        except Exception as e:
            raise CommandError(f"Ошибка чтения файла: {e}")

        # Если JSON - это список карт
        if isinstance(cards_data, dict):
            # Может быть обёртка с ключом "cards" или просто одна карта
            if "cards" in cards_data:
                cards_data = cards_data["cards"]
            else:
                cards_data = [cards_data]
        
        if not isinstance(cards_data, list):
            raise CommandError("JSON должен содержать список карт или объект с ключом 'cards'")

        total = len(cards_data)
        self.stdout.write(f"Найдено карт для обработки: {total}")
        self.stdout.write("=" * 60)

        created_count = 0
        error_count = 0
        skipped_count = 0

        for idx, card_data in enumerate(cards_data, 1):
            try:
                card_id = card_data.get("card_id")
                if not card_id:
                    self.stdout.write(
                        self.style.WARNING(f"[{idx}/{total}] Пропущена: нет card_id")
                    )
                    skipped_count += 1
                    continue

                self.stdout.write(f"[{idx}/{total}] Обработка card_id: {card_id}")

                # Ищем TarotCard
                try:
                    tarot_card = TarotCard.objects.get(card_id=card_id)
                except TarotCard.DoesNotExist:
                    raise ValueError(f"Карта с ID '{card_id}' не найдена в базе данных")

                # Ищем или создаём TarotCardItem в колоде
                card_item, item_created = TarotCardItem.objects.get_or_create(
                    deck=deck,
                    tarot_card=tarot_card,
                    defaults={
                        "custom_name": card_data.get("name"),
                        "custom_description": card_data.get("description"),
                    }
                )

                if item_created:
                    self.stdout.write(f"  Карта добавлена в колоду")
                    created_count += 1
                else:
                    self.stdout.write(f"  Карта уже существует в колоде")

                # Обрабатываем изображение
                image_url = card_data.get("image_url") or card_data.get("image")
                if image_url:
                    self.upload_card_image(
                        bot=bot,
                        chat_id=chat_id,
                        card_item=card_item,
                        image_url=image_url,
                        card_name=str(card_item)
                    )
                    time.sleep(timeout)
                else:
                    self.stdout.write(
                        self.style.WARNING(f"  Нет image_url для карты")
                    )

            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"  Ошибка обработки: {e}")
                )
                import traceback
                self.stdout.write(
                    self.style.ERROR(f"  {traceback.format_exc()}")
                )

            # Небольшая пауза между картами
            if idx < total:
                time.sleep(0.5)

        # Итоговый отчёт
        self.stdout.write("=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 ОТЧЕТ О ВЫПОЛНЕНИИ\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 Всего карт в JSON: {total}\n"
                f"✅ Создано новых карт в колоде: {created_count}\n"
                f"⏭️ Пропущено (нет card_id): {skipped_count}\n"
                f"❌ Ошибок: {error_count}\n"
                f"📚 Колода: {deck.name} (ID: {deck.id})"
            )
        )

    def upload_card_image(self, bot, chat_id, card_item, image_url, card_name):
        """
        Скачивает изображение по URL, загружает в бота и сохраняет в BotFile
        """
        try:
            self.stdout.write(f"  📥 Скачиваю изображение: {image_url}")
            response = requests.get(image_url, timeout=60)
            
            if not response.ok:
                self.stdout.write(
                    self.style.ERROR(f"  ❌ Ошибка скачивания: HTTP {response.status_code}")
                )
                return None
            
            self.stdout.write(f"  ✅ Изображение скачано, размер: {len(response.content)} байт")

            # Загружаем в бота
            files = {
                'photo': (f"{card_name}.jpg", response.content, 'image/jpeg')
            }
            
            send_photo_url = f"https://api.telegram.org/bot{bot.token}/sendPhoto"
            self.stdout.write(f"  📤 Загружаю в бота...")
            
            upload_response = requests.post(
                send_photo_url,
                files=files,
                data={'chat_id': chat_id},
                timeout=60
            )
            
            if upload_response.ok:
                result = upload_response.json()
                if result.get('ok'):
                    if 'photo' in result.get('result', {}):
                        photo_sizes = result['result']['photo']
                        file_id = photo_sizes[-1]['file_id']  # Берём самый большой размер
                        
                        # Сохраняем в BotFile
                        content_type = ContentType.objects.get_for_model(card_item)
                        BotFile.objects.update_or_create(
                            content_type=content_type,
                            object_id=card_item.id,
                            bot=bot,
                            defaults={"file_id": file_id}
                        )
                        
                        self.stdout.write(
                            self.style.SUCCESS(f"  ✅ Изображение загружено! file_id: {file_id[:30]}...")
                        )
                        return file_id
                    else:
                        self.stdout.write(
                            self.style.ERROR(f"  ❌ В ответе нет photo: {result}")
                        )
                        return None
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"  ❌ Telegram вернул ошибку: {result.get('description', 'Unknown error')}"
                        )
                    )
                    return None
            else:
                self.stdout.write(
                    self.style.ERROR(f"  ❌ Ошибка загрузки: HTTP {upload_response.status_code}")
                )
                self.stdout.write(
                    self.style.ERROR(f"     Ответ: {upload_response.text[:200]}")
                )
                return None

        except requests.exceptions.Timeout:
            self.stdout.write(
                self.style.ERROR(f"  ❌ Таймаут при запросе (60 секунд)")
            )
            return None
        except requests.exceptions.ConnectionError as e:
            self.stdout.write(
                self.style.ERROR(f"  ❌ Ошибка соединения: {str(e)}")
            )
            return None
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"  ❌ Непредвиденная ошибка: {str(e)}")
            )
            import traceback
            self.stdout.write(
                self.style.ERROR(f"     {traceback.format_exc()}")
            )
            return None