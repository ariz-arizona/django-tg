from html import escape

class SpreadMessages:
    INITIALIZING = "⏳ <b>Формирование расклада...</b>"
    LOADING = "🃏 <b>Подбираю карты для вас...</b>"
    RENDERING = "🎨 <b>Создаю изображение расклада...</b>"
    UPLOADING = "🚀 <b>Отправка результатов...</b>"
    
    @staticmethod
    def format_description(deck_name, cards_description):
        deck_str = escape(deck_name or "Стандартная колода")
        lines = [f"🔮 <b>Расклад из колоды «{deck_str}»:</b>"]
        for desc in cards_description:
            lines.append(f"   • {desc}")
        return "\n".join(lines)