import os
import json
import requests
import shutil
import time
from django.core.management.base import BaseCommand
from django.forms.models import model_to_dict
from tarot.models import TarotCardItem, OraculumItem, TarotDeck
from tg_bot.models import BotFileCache

class Command(BaseCommand):
    help = "Скачивает файлы и JSON метаданные с поддержкой фильтров и умной очистки"

    def add_arguments(self, parser):
        parser.add_argument("--key", type=str, default="tarot", choices=["tarot", "oraculum"], 
                            help="Тип: tarot или oraculum")
        parser.add_argument("--deck_id", type=int, default=None, help="ID колоды для фильтрации")
        parser.add_argument("--clear", action="store_true", help="Очистить целевые папки перед скачиванием. Если не указан — докачивает пропущенное.")
        parser.add_argument("--timeout", type=int, default=5,)

    def handle(self, *args, **options):
        key = options["key"]
        deck_id = options["deck_id"]
        clear = options["clear"]
        timeout = options["timeout"]
        base_dir = "backup"
        
        target_dir = os.path.join(base_dir, key)

        model_class = TarotCardItem if key == "tarot" else OraculumItem
        queryset = model_class.objects.select_related("deck").prefetch_related("files").all()
        
        if deck_id:
            queryset = queryset.filter(deck_id=deck_id)
            self.stdout.write(f"Применен фильтр по deck_id: {deck_id}")

        # ЛОГИКА ОЧИСТКИ
        if clear:
            if deck_id:
                deck = TarotDeck.objects.filter(id=deck_id).first()
                if deck:
                    safe_deck_name = "".join([c for c in deck.name if c.isalnum() or c in (' ', '_', '-')]).strip()
                    specific_deck_dir = os.path.join(target_dir, safe_deck_name)
                    if os.path.exists(specific_deck_dir):
                        self.stdout.write(self.style.WARNING(f"Очистка папки колоды: {specific_deck_dir}"))
                        shutil.rmtree(specific_deck_dir)
                else:
                    self.stdout.write(self.style.ERROR(f"Колода с ID {deck_id} не найдена для очистки."))
            else:
                if os.path.exists(target_dir):
                    self.stdout.write(self.style.WARNING(f"Очистка всей директории: {target_dir}"))
                    shutil.rmtree(target_dir)
        else:
            self.stdout.write(self.style.SUCCESS("Режим: докачка пропущенного (без очистки)."))

        total = queryset.count()
        self.stdout.write(f"Найдено элементов для обработки: {total}")
        
        processed_decks = set()

        for item in queryset:
            deck = item.deck
            deck_name = deck.name if deck else "Unknown_Deck"
            safe_deck_name = "".join([c for c in deck_name if c.isalnum() or c in (' ', '_', '-')]).strip()
            deck_path = os.path.join(base_dir, key, safe_deck_name)
            json_path = os.path.join(deck_path, "json")
            os.makedirs(json_path, exist_ok=True)
            time.sleep(timeout)

            # 1. СОХРАНЯЕМ DECK_INFO.JSON
            if deck and deck.id not in processed_decks:
                deck_info_path = os.path.join(json_path, "deck_info.json")
                if not os.path.exists(deck_info_path):
                    deck_data = model_to_dict(deck)
                    self.save_json(deck_info_path, deck_data)
                else:
                    self.stdout.write(f"Пропущено (уже есть): {deck_info_path}")
                processed_decks.add(deck.id)

            # 2. Сохраняем JSON карты
            card_json_path = os.path.join(json_path, f"{item.id}_info.json")
            if not os.path.exists(card_json_path):
                item_data = model_to_dict(item)
                self.save_json(card_json_path, item_data)
            else:
                self.stdout.write(f"Пропущено (уже есть): {card_json_path}")

            # 3. Загрузка файла
            file_obj = item.files.first()
            if file_obj:
                cache, _ = BotFileCache.objects.get_or_create(bot_file=file_obj)
                file_link = cache.get_cache_link()
                if file_link:
                    file_path = os.path.join(deck_path, f"{item.id}.jpg")
                    if not os.path.exists(file_path):
                        self.download_file(file_link, file_path)
                    else:
                        self.stdout.write(f"Пропущен файл (уже есть): {file_path}")
                else:
                    self.stdout.write(self.style.WARNING(f"Нет ссылки на файл для item_id {item.id}"))
            else:
                self.stdout.write(self.style.WARNING(f"Файл отсутствует в базе для item_id {item.id}"))

    def save_json(self, path, data):
        for key, value in data.items():
            if hasattr(value, 'pk'): 
                data[key] = value.pk
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        self.stdout.write(self.style.SUCCESS(f"Создан JSON: {path}"))

    def download_file(self, url, path):
        try:
            response = requests.get(url, stream=True, timeout=30)
            if response.status_code == 200:
                with open(path, 'wb') as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                self.stdout.write(self.style.SUCCESS(f"Скачан файл: {path}"))
            else:
                self.stdout.write(self.style.ERROR(f"Ошибка HTTP {response.status_code} для {url}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ошибка при скачивании файла {path}: {e}"))