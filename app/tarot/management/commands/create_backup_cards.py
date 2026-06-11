import os
import json
import requests
import shutil
from django.core.management.base import BaseCommand
from django.forms.models import model_to_dict
from tarot.models import TarotCardItem, OraculumItem, TarotDeck
from tg_bot.models import BotFileCache

class Command(BaseCommand):
    help = "Скачивает файлы и JSON метаданные с поддержкой фильтров"

    def add_arguments(self, parser):
        parser.add_argument("--key", type=str, default="tarot", choices=["tarot", "oraculum"], 
                            help="Тип: tarot или oraculum")
        parser.add_argument("--deck_id", type=int, default=None, help="ID колоды для фильтрации")

    def handle(self, *args, **options):
        key = options["key"]
        deck_id = options["deck_id"]
        base_dir = "backup"
        
        target_dir = os.path.join(base_dir, key)
        if deck_id:
            # Если указан deck_id, ищем папку колоды, но это сложно без её названия.
            # Поэтому очищаем только если путь существует. 
            # Либо чистим всю папку ключа (безопаснее просто перезаписывать файлы).
            self.stdout.write(self.style.WARNING(f"Очистка папки {target_dir} (фильтр по ID)"))
        else:
            if os.path.exists(target_dir):
                self.stdout.write(self.style.WARNING(f"Очистка всей директории: {target_dir}"))
                shutil.rmtree(target_dir) # Удаляет всё содержимое папки ключа

        # Определяем модель на основе ключа
        model_class = TarotCardItem if key == "tarot" else OraculumItem
        
        # Получаем данные
        queryset = model_class.objects.select_related("deck").prefetch_related("files").all()
        
        # Фильтр по ID колоды
        if deck_id:
            queryset = queryset.filter(deck_id=deck_id)
            self.stdout.write(f"Применен фильтр по deck_id: {deck_id}")

        total = queryset.count()
        self.stdout.write(f"Найдено элементов: {total}")
        
        processed_decks = set()

        for item in queryset:
            deck = item.deck
            deck_name = deck.name if deck else "Unknown_Deck"
            safe_deck_name = "".join([c for c in deck_name if c.isalnum() or c in (' ', '_', '-')]).strip()
            deck_path = os.path.join(base_dir, key, safe_deck_name)
            json_path = os.path.join(deck_path, "json")
            os.makedirs(json_path, exist_ok=True)

            # 1. СОХРАНЯЕМ DECK_INFO.JSON (только один раз для колоды)
            if deck and deck.id not in processed_decks:
                deck_data = model_to_dict(deck)
                self.save_json(os.path.join(json_path, "deck_info.json"), deck_data)
                processed_decks.add(deck.id)

            # 2. Сохраняем JSON карты
            item_data = model_to_dict(item)
            self.save_json(os.path.join(json_path, f"{item.id}_info.json"), item_data)

            # Загрузка файла
            file_obj = item.files.first()
            if file_obj:
                cache, _ = BotFileCache.objects.get_or_create(bot_file=file_obj)
                file_link = cache.get_cache_link()
                if file_link:
                    # Файлы изображений лежат в корне папки колоды, а не в json/
                    file_path = os.path.join(deck_path, f"{item.id}.jpg")
                    if not os.path.exists(file_path):
                        self.download_file(file_link, file_path)

    def save_json(self, path, data):
        # Превращаем объекты в ID для JSON
        for key, value in data.items():
            if hasattr(value, 'pk'): data[key] = value.pk
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def download_file(self, url, path):
        try:
            response = requests.get(url, stream=True, timeout=30)
            if response.status_code == 200:
                with open(path, 'wb') as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                self.stdout.write(f"Сохранено: {path}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ошибка: {e}"))