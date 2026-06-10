# management/commands/cache_tarot_files.py

import requests
import time
import json
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from django.contrib.contenttypes.models import ContentType

from tarot.models import TarotCardItem, BotFile, TarotFileCache, TarotDeck
from tg_bot.models import Bot


class Command(BaseCommand):
    help = "Проходит по всем TarotCardItem, кеширует и отправляет в целевого бота"

    def add_arguments(self, parser):
        parser.add_argument(
            "bot_id", type=int, help="ID бота, для которого нужно получить кеш файлов"
        )
        parser.add_argument(
            "--deck",
            type=str,
            default=None,
            help="Название колоды для фильтрации (можно использовать частичное совпадение)",
        )
        parser.add_argument(
            "--deck_id",
            type=int,
            default=None,
            help="ID колоды для фильтрации",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Лимит количества обрабатываемых карт (по умолчанию без лимита)",
        )
        parser.add_argument(
            "--offset",
            type=int,
            default=0,
            help="Смещение (пропустить первые N карт)",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=2.0,
            help="Задержка между картами в секундах (по умолчанию 2)",
        )
        parser.add_argument(
            "--chat_target",
            type=str,
            default=None,
            help="Chat ID целевого чата (если не указан, используется chat_id из модели бота)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Принудительно обновить все файлы, даже если они уже есть",
        )

    def handle(self, *args, **options):
        target_bot_id = options["bot_id"]
        deck_name = options.get("deck")
        deck_id = options.get("deck_id")
        limit = options.get("limit")
        offset = options.get("offset", 0)
        delay = options.get("delay", 2.0)
        chat_target = options.get("chat_target")
        force = options.get("force", False)

        # Проверяем существование целевого бота
        try:
            target_bot = Bot.objects.get(id=target_bot_id, is_enabled=True)
        except Bot.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"Бот с ID {target_bot_id} не найден или отключен")
            )
            return

        self.stdout.write(
            f"Начинаем обработку для бота: {target_bot.name} (ID: {target_bot_id})"
        )
        
        # Формируем queryset
        cards_queryset = (
            TarotCardItem.objects.select_related("tarot_card", "deck")
            .prefetch_related("files")
            .all()
        )
        
        # Фильтр по ID колоды
        if deck_id:
            try:
                deck = TarotDeck.objects.get(id=deck_id)
                cards_queryset = cards_queryset.filter(deck=deck)
                self.stdout.write(f"📚 Фильтр по ID колоды: {deck_id} ({deck.name})")
            except TarotDeck.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"Колода с ID {deck_id} не найдена")
                )
                return
        
        # Фильтр по названию колоды
        elif deck_name:
            decks = TarotDeck.objects.filter(name__icontains=deck_name)
            if not decks.exists():
                self.stdout.write(
                    self.style.ERROR(f"Колоды с названием '{deck_name}' не найдены")
                )
                return
            
            cards_queryset = cards_queryset.filter(deck__in=decks)
            self.stdout.write(f"📚 Фильтр по названию колоды: {deck_name}")
            for deck in decks:
                self.stdout.write(f"   - {deck.name} (ID: {deck.id})")
        
        total_count = cards_queryset.count()
        
        if offset:
            cards_queryset = cards_queryset[offset:]
        
        if limit:
            cards = cards_queryset[:limit]
            total = min(total_count - offset, limit)
        else:
            cards = cards_queryset
            total = total_count - offset
        
        if total <= 0:
            self.stdout.write(self.style.WARNING(f"Нет карт для обработки"))
            return
        
        self.stdout.write("=" * 60)
        self.stdout.write(f"📊 Статистика обработки:")
        self.stdout.write(f"   Всего карт в выборке: {total}")
        self.stdout.write(f"   Задержка между картами: {delay} сек")
        self.stdout.write(f"   Принудительное обновление: {force}")
        self.stdout.write("=" * 60)
        
        chat_id = chat_target if chat_target else target_bot.chat_id
        self.stdout.write(f"🎯 Целевой чат: {chat_id}\n")
        
        processed = 0
        uploaded_count = 0
        skipped_count = 0
        error_count = 0
        start_time = time.time()
        
        for card in cards:
            processed += 1
            card_start_time = time.time()
            
            self.stdout.write(f"[{processed}/{total}] Обработка: {card} (колода: {card.deck.name})")
            
            # Проверяем, есть ли уже файл в целевом боте
            if not force:
                existing_file = BotFile.objects.filter(
                    content_type=ContentType.objects.get_for_model(card),
                    object_id=card.id,
                    bot=target_bot
                ).first()
                
                if existing_file:
                    skipped_count += 1
                    self.stdout.write(
                        self.style.WARNING(f"  ⏭️ Уже есть в целевом боте, пропускаем")
                    )
                    if processed < total:
                        time.sleep(delay)
                    continue
            
            # Получаем исходный файл
            file_obj = card.files.first()
            if not file_obj:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"  ❌ Нет исходного файла")
                )
                if processed < total:
                    time.sleep(delay)
                continue
            
            source_bot = file_obj.bot
            self.stdout.write(f"  📎 Источник: {source_bot.name}")
            
            # Получаем file_path (с кешированием)
            file_path = self.get_file_path_with_cache(card, file_obj.file_id, source_bot)
            
            if not file_path:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"  ❌ Не удалось получить file_path")
                )
                if processed < total:
                    time.sleep(delay)
                continue
            
            # Загружаем фото в целевого бота
            uploaded_file_id = self.upload_photo_to_target_bot(
                source_bot=source_bot,
                target_bot=target_bot,
                file_path=file_path,
                chat_id=chat_id,
                card=card
            )
            
            if uploaded_file_id:
                # Сохраняем в БД
                try:
                    BotFile.objects.update_or_create(
                        content_type=ContentType.objects.get_for_model(card),
                        object_id=card.id,
                        bot=target_bot,
                        defaults={"file_id": uploaded_file_id},
                    )
                    uploaded_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"  ✅ Загружено! file_id: {uploaded_file_id[:30]}...")
                    )
                except Exception as e:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(f"  ❌ Ошибка сохранения в БД: {str(e)}")
                    )
            else:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"  ❌ Не удалось загрузить фото")
                )
            
            card_time = time.time() - card_start_time
            self.stdout.write(f"  ⏱️ Время: {card_time:.2f} сек")
            
            # Пауза перед следующей картой
            if processed < total:
                self.stdout.write(f"  ⏳ Пауза {delay} сек...\n")
                time.sleep(delay)
            else:
                self.stdout.write("")
        
        # Итоговый отчет
        total_time = time.time() - start_time
        hours = int(total_time // 3600)
        minutes = int((total_time % 3600) // 60)
        seconds = int(total_time % 60)
        
        self.stdout.write("=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"\n📊 ОТЧЕТ О ВЫПОЛНЕНИИ\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 Всего карт: {total}\n"
                f"✅ Успешно загружено: {uploaded_count}\n"
                f"⏭️ Пропущено (уже есть): {skipped_count}\n"
                f"❌ Ошибок: {error_count}\n"
                f"⏰ Общее время: {hours}ч {minutes}м {seconds}с\n"
                f"⚡ Среднее время на карту: {total_time/total:.2f} сек"
            )
        )

    def get_file_path_with_cache(self, card_item, file_id: str, source_bot: Bot) -> str:
        """
        Получает file_path с использованием кеша.
        Если кеш есть и не истек - берем из кеша.
        Если нет или истек - запрашиваем новый и сохраняем в кеш.
        """
        
        # Проверяем кеш
        try:
            cache = TarotFileCache.objects.get(card_item=card_item)
            if not cache.is_expired():
                self.stdout.write(f"  📦 Кеш действителен до {cache.expires_at.strftime('%H:%M:%S')}")
                return cache.file_path
            else:
                self.stdout.write(f"  ⏰ Кеш истек, запрашиваем новый...")
        except TarotFileCache.DoesNotExist:
            self.stdout.write(f"  🆕 Кеша нет, запрашиваем...")
        
        # Запрашиваем свежий file_path
        file_path = self.get_file_path_from_telegram(source_bot, file_id)
        
        if file_path:
            with transaction.atomic():
                cache, created = TarotFileCache.objects.update_or_create(
                    card_item=card_item,
                    defaults={
                        "file_path": file_path,
                        "expires_at": timezone.now() + timezone.timedelta(hours=1),
                    }
                )
                if created:
                    self.stdout.write(f"  ✨ Новый кеш создан")
                else:
                    self.stdout.write(f"  🔄 Кеш обновлен")
                return file_path
        
        return None

    def get_file_path_from_telegram(self, bot: Bot, file_id: str) -> str:
        """Запрашивает у Telegram API временный путь к файлу"""
        
        get_file_url = f"https://api.telegram.org/bot{bot.token}/getFile"

        try:
            response = requests.post(get_file_url, json={"file_id": file_id}, timeout=30)
            
            if not response.ok:
                return None

            data = response.json()
            if not data.get("ok"):
                return None

            return data["result"]["file_path"]

        except Exception:
            return None

    def upload_photo_to_target_bot(
        self, source_bot: Bot, target_bot: Bot, file_path: str, chat_id: str, card
    ) -> str:
        """
        Скачивает файл от source_bot и загружает его в target_bot
        
        Returns:
            file_id загруженного файла или None при ошибке
        """
        
        # Скачиваем файл от бота-источника
        file_url = f"https://api.telegram.org/file/bot{source_bot.token}/{file_path}"
        
        try:
            response = requests.get(file_url, timeout=60)
            if not response.ok:
                return None
            
            # Загружаем в целевого бота
            files = {
                'photo': (f"{card}.jpg", response.content, 'image/jpeg')
            }
            
            send_photo_url = f"https://api.telegram.org/bot{target_bot.token}/sendPhoto"
            upload_response = requests.post(
                send_photo_url,
                files=files,
                data={'chat_id': chat_id},
                timeout=60
            )
            
            if upload_response.ok:
                result = upload_response.json()
                if result.get('ok') and 'photo' in result.get('result', {}):
                    photo_sizes = result['result']['photo']
                    return photo_sizes[-1]['file_id']
            
            return None
            
        except Exception:
            return None