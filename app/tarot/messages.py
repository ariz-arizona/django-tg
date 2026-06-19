from abc import ABC, abstractmethod
from html import escape
from typing import Optional, List, Dict, Any

TAROT_3_TRIGGER = "✨ Три карты"
CANVAS_3_TRIGGER = "🖼 Холст"


class Messages(ABC):
    """Абстрактный базовый класс для сообщений"""
    
    INITIALIZING = "⏳ <b>Формирование расклада...</b>"
    LOADING = "🃏 <b>Подбираю карты для вас...</b>"
    RENDERING = "🎨 <b>Создаю изображение расклада...</b>"
    UPLOADING = "🚀 <b>Отправка результатов...</b>"
    DECK_TITLE = "🔮 <b>Расклад из колоды «{deck_name}»:</b>"
    
    ERROR_MESSAGES = {
        "cooldown": "⏳ <b>Подождите немного!</b>\nПожалуйста, подождите {wait_time} секунд перед следующим раскладом.",
        "no_cards": "❌ <b>Карты не найдены</b>\nНе удалось найти карты для этого расклада.",
        "no_deck": "❌ <b>Колода не найдена</b>\nУказанная колода не существует или недоступна.",
        "invalid_options": "❌ <b>Неверные параметры</b>\nПроверьте правильность введенных данных.",
        "image_failed": "❌ <b>Ошибка создания изображения</b>\nНе удалось создать изображение расклада. Попробуйте позже.",
        "file_not_found": "❌ <b>Файл не найден</b>\nНе удалось загрузить изображение для карты {card_name}.",
        "generic": "❌ <b>Произошла ошибка</b>\n{error_details}",
        "flip_required": "❌ <b>Требуется переворот</b>\nДля этого расклада обязательно указание параметра 'flip'.",
        "major_only_invalid": "❌ <b>Неверный параметр</b>\nПараметр 'major' должен быть логическим значением (True/False).",
        "counter_invalid": "❌ <b>Неверное количество карт</b>\nКоличество карт должно быть от 1 до 10.",
        "user_not_found": "❌ <b>Пользователь не найден</b>\nНе удалось идентифицировать пользователя.",
    }
    
    def get_initializing(self) -> str:
        return self.INITIALIZING
    
    def get_loading(self) -> str:
        return self.LOADING
    
    def get_rendering(self) -> str:
        return self.RENDERING
    
    def get_uploading(self) -> str:
        return self.UPLOADING
    
    def get_deck_title(self, deck_name: str) -> str:
        return self.DECK_TITLE.format(deck_name=escape(deck_name or "Стандартная колода"))
    
    def format_description(self, deck_name: str, cards_description: List[str], 
                          stats_str: Optional[str] = None, 
                          try_all_str: Optional[str] = None) -> str:
        deck_str = escape(deck_name or "Стандартная колода")
        lines = [self.DECK_TITLE.format(deck_name=deck_str)]
        lines.extend(f"   • {desc}" for desc in cards_description)
        
        if stats_str:
            lines.append(f"\n{stats_str}")
        if try_all_str:
            lines.append(try_all_str)
            
        return "\n".join(lines)
    
    def get_error_message(self, error_type: str, **kwargs) -> str:
        error_template = self.ERROR_MESSAGES.get(error_type, self.ERROR_MESSAGES["generic"])
        
        if error_type == "generic" and "error_details" not in kwargs:
            kwargs["error_details"] = "Неизвестная ошибка"
        
        try:
            return error_template.format(**kwargs)
        except KeyError as e:
            return self.ERROR_MESSAGES["generic"].format(
                error_details=f"Ошибка форматирования: {str(e)}"
            )


class CanvasMessages(Messages):
    """Класс сообщений для Canvas раскладов"""
    pass

class CardMessages(Messages):
    """Класс сообщений для Card раскладов"""
    
    TRY_ALL_DECK = (
        "💡 <b>Большие расклады удобнее смотреть в режиме «Вся колода»: </b>\n"
        "/all_deck_{deck_id}{flip_flag}"
    )
    FAVORITE_COMMAND = "❤️ <b>Повторить расклад:</b> {command}"
    DECK_STATS = "<i>Всего в колоде: {current_count}/{total_cards}</i>"
    ELLIPSIS = "   • <i>...</i>"
    
    # Текстовки для результатов поиска колоды
    DECK_NOT_FOUND = (
        "🔍 По запросу «{keyword}» колода не найдена.\n\n"
        "✨ Вы можете получить этот расклад с колодой по умолчанию:\n"
        "{base_command}"
    )
    DECK_MULTIPLE_FOUND = (
        "🔍 По запросу «{keyword}» найдено {count} колод:\n\n"
        "{deck_lines}\n\n"
        "✨ Выберите подходящую команду для расклада"
    )
    DECK_SINGLE_FOUND = (
        "🃏 Колода: <b>{deck_name}</b>\n\n"
        "✨ Вы можете получить этот расклад по команде:\n"
        "{full_command}"
    )
    DECK_SINGLE_WITH_DESCRIPTION = (
        "🃏 Колода: <b>{deck_name}</b>\n"
        "<i>{deck_description}</i>\n\n"
        "✨ Вы можете получить этот расклад по команде:\n"
        "{full_command}"
    )
    
    def get_try_all_deck(self, deck_id: str, flip_flag: str = "") -> str:
        return self.TRY_ALL_DECK.format(deck_id=deck_id, flip_flag=flip_flag)
    
    def get_favorite_command(self, command: str) -> str:
        return self.FAVORITE_COMMAND.format(command=command)
    
    def get_deck_stats(self, current_count: int, total_cards: int) -> str:
        return self.DECK_STATS.format(current_count=current_count, total_cards=total_cards)
    
    def get_deck_not_found(self, keyword: str, base_command: str) -> str:
        return self.DECK_NOT_FOUND.format(keyword=escape(keyword), base_command=base_command)
    
    def build_deck_command(self, base_command: str, deck_slug: str) -> str:
        """Собирает команду с колодой: /card_3_deck_waite"""
        return f"{base_command}_deck_{deck_slug}"

    def get_deck_multiple_found(self, keyword: str, count: int, decks: list, base_command: str) -> str:
        deck_lines = "\n".join([
            f"  • {self.build_deck_command(base_command, d.slug)} — {escape(d.name)}"
            for d in decks[:5]
        ])
        return self.DECK_MULTIPLE_FOUND.format(
            keyword=escape(keyword),
            count=count,
            deck_lines=deck_lines
        )

    def get_deck_single_found(self, deck, base_command: str) -> str:
        full_command = self.build_deck_command(base_command, deck.slug)
        description = getattr(deck, 'description', None)
        
        if description:
            return self.DECK_SINGLE_WITH_DESCRIPTION.format(
                deck_name=escape(deck.name),
                deck_description=escape(description),
                full_command=full_command
            )
        else:
            return self.DECK_SINGLE_FOUND.format(
                deck_name=escape(deck.name),
                full_command=full_command
            )
    
    def get_deck_search_result(self, decks, keyword: str, base_command: str) -> str:
        """
        Формирует сообщение с результатами поиска колоды.
        
        Args:
            decks: None, список колод, или одна колода
            keyword: ключевое слово поиска
            base_command: базовая команда (например, "/card_3")
        """
        if decks is None or (isinstance(decks, list) and len(decks) == 0):
            return self.get_deck_not_found(keyword, base_command)
        elif isinstance(decks, list) and len(decks) > 1:
            return self.get_deck_multiple_found(keyword, len(decks), decks, base_command)
        else:
            deck = decks if not isinstance(decks, list) else decks[0]
            return self.get_deck_single_found(deck, base_command)
    
    def clean_card_name(self, card_name: str) -> str:
        """Очищает имя карты от лишних пробелов и переносов строк"""
        return " ".join(card_name.split())
    
    def format_description(self, deck_name: str, cards_description: List[str], 
                          stats_str: Optional[str] = None, 
                          try_all_str: Optional[str] = None,
                          deck_description: Optional[str] = None) -> str:
        deck_str = escape(deck_name or "Стандартная колода")
        lines = [self.DECK_TITLE.format(deck_name=deck_str)]
        
        # Описание колоды, если есть
        if deck_description:
            lines.append(f"\n<i>{escape(deck_description)}</i>")
        
        # Если карт больше 20, показываем первые 9 + ... + последние 9
        if len(cards_description) > 20:
            lines.extend(f"   • {desc}" for desc in cards_description[:9])
            lines.append(self.ELLIPSIS)
            lines.extend(f"   • {desc}" for desc in cards_description[-9:])
        else:
            lines.extend(f"   • {desc}" for desc in cards_description)
        
        if stats_str:
            lines.append(f"\n{stats_str}")
        if try_all_str:
            lines.append(try_all_str)
            
        return "\n".join(lines)