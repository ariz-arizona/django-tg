from html import escape

class SpreadMessages:
    INITIALIZING = "⏳ <b>Формирование расклада...</b>"
    LOADING = "🃏 <b>Подбираю карты для вас...</b>"
    RENDERING = "🎨 <b>Создаю изображение расклада...</b>"
    UPLOADING = "🚀 <b>Отправка результатов...</b>"
    
    DECK_TITLE = "🔮 <b>Расклад из колоды «{deck_name}»:</b>"
    
    # Шаблон команды, куда мы будем подставлять готовый кусок строки {flip_flag}
    TRY_ALL_DECK = (
        "💡 <b>Большие расклады удобнее смотреть в режиме «Вся колода»: </b>\n"
        "/all_deck_{deck_id}{flip_flag}"
    )
    
    @staticmethod
    def format_description(deck_name, cards_description, stats_str=None, try_all_str=None):
        deck_str = escape(deck_name or "Стандартная колода")
        
        lines = [SpreadMessages.DECK_TITLE.format(deck_name=deck_str)]
        
        for desc in cards_description:
            lines.append(f"   • {desc}")
            
        if stats_str:
            lines.append(f"\n{stats_str}")
            
        if try_all_str:
            lines.append(try_all_str)
            
        return "\n".join(lines)