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
    
    def get_try_all_deck(self, deck_id: str, flip_flag: str = "") -> str:
        return self.TRY_ALL_DECK.format(deck_id=deck_id, flip_flag=flip_flag)
    
    def get_favorite_command(self, command: str) -> str:
        return self.FAVORITE_COMMAND.format(command=command)