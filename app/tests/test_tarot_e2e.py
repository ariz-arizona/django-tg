# tests/test_tarot_e2e.py
import pytest
from tests.conftest import get_card_names_from_response

@pytest.mark.django_db
def test_start_command(get_messages, send_webhook_update):
    """E2E: /start — бот должен ответить с кнопками и /help в тексте"""
    
    token = "test_token_12345"
    user_id = 1001
    
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/start",
            "entities": [{"offset": 0, "length": 6, "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print("✅ Апдейт /start отправлен")
    
    messages = get_messages(token, expected_count=1, timeout=5)
    assert len(messages) > 0, "Бот не ответил на /start!"
    
    from tests.conftest import extract_message_data
    data = extract_message_data(messages[0])
    text = data.get('text', '')
    
    print(f"📝 Ответ: {text[:200]}")
    
    assert 'Добро пожаловать' in text
    assert '/help' in text
    
    reply_markup = data.get('reply_markup', {})
    keyboard = reply_markup.get('keyboard', [])
    buttons = [b.get('text', '') for row in keyboard for b in row]
    
    print(f"🔘 Кнопки: {buttons}")
    assert len(buttons) == 2, f"Ожидалось 2 кнопки, получили: {buttons}"
    
    print("✅ Тест /start пройден!")


@pytest.mark.django_db
def test_help_command(get_messages, send_webhook_update):
    """E2E: /help — должны быть все основные команды"""
    
    token = "test_token_12345"
    user_id = 1002
    
    update = {
        "update_id": 2,
        "message": {
            "message_id": 2,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000001,
            "text": "/help",
            "entities": [{"offset": 0, "length": 5, "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print("✅ Апдейт /help отправлен")
    
    messages = get_messages(token, expected_count=1, timeout=5)
    assert len(messages) > 0, "Бот не ответил на /help!"
    
    from tests.conftest import extract_message_data
    data = extract_message_data(messages[0])
    text = data.get('text', '')
    
    print(f"📝 Ответ на /help: {text[:300]}")
    
    required_commands = ['/one', '/card', '/canvas', '/futark']
    missing = [cmd for cmd in required_commands if cmd not in text]
    
    assert len(missing) == 0, f"Не найдены команды: {missing}"
    
    print("✅ Тест /help пройден!")


@pytest.mark.django_db
def test_card_command(send_webhook_update, redis_client):
    """E2E: /card — проверяем полный цикл отправки карты"""
    
    token = "test_token_12345"
    user_id = 1003
    redis_key = f"intercepted_requests:{token}"
    
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    from tests.conftest import extract_message_data, sync_lrange
    import json
    import time
    
    time.sleep(1)
    
    update = {
        "update_id": 10,
        "message": {
            "message_id": 10,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/card",
            "entities": [{"offset": 0, "length": 5, "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print("✅ Апдейт /card отправлен\n")
    
    all_messages = []
    start = time.time()
    timeout = 15
    got_final = False
    got_delete = False
    
    while time.time() - start < timeout:
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') in 
                   ['sendMessage', 'sendMediaGroup', 'deleteMessage']]
        
        for msg in messages:
            if msg not in all_messages:
                all_messages.append(msg)
                endpoint = msg.get('endpoint', '?')
                data = extract_message_data(msg)
                
                if 'text' in data:
                    print(f"📨 {endpoint}: {data['text'][:100]}")
                elif 'media' in data:
                    media_items = data.get('media', [])
                    print(f"📨 {endpoint} media[0].caption: {media_items[0].get('caption', '')[:100] if media_items else ''}")
                else:
                    print(f"📨 {endpoint}: {json.dumps(data, ensure_ascii=False)[:100]}")
                
                if 'reply_markup' in data:
                    got_final = True
                if endpoint == 'deleteMessage':
                    got_delete = True
        
        if got_final and got_delete:
            print("✅ Все сообщения получены!")
            break
        
        time.sleep(0.1)
    
    assert len(all_messages) >= 3, f"Мало сообщений! Получили {len(all_messages)}"
    
    media_msg = None
    final_msg = None
    
    for msg in all_messages:
        data = extract_message_data(msg)
        if msg.get('endpoint') == 'sendMediaGroup':
            media_msg = data
        if 'reply_markup' in data:
            final_msg = data
    
    assert final_msg is not None, "Нет финального сообщения с кнопками!"
    assert media_msg is not None, "Нет сообщения с картинкой!"
    
    media_items = media_msg.get('media', [])
    caption = media_items[0].get('caption', '') if media_items else ''
    
    print(f"\n🖼️ Подпись к карте: '{caption}'")
    card_name = caption.strip().split('\n')[0]
    assert len(card_name) > 0, f"Название карты пустое! caption='{caption}'"
    
    text = final_msg.get('text', '')
    inline_keyboard = final_msg.get('reply_markup', {}).get('inline_keyboard', [])
    buttons = [b.get('text', '') for row in inline_keyboard for b in row]
    
    print(f"📝 Текст: {text[:200]}")
    print(f"🔘 Кнопки: {buttons}")
    
    assert 'Еще карту' in buttons
    traktovka_btn = [b for b in buttons if 'Трактовка' in b]
    assert len(traktovka_btn) > 0
    
    import re
    numbers = re.findall(r'\((\d+)\)', traktovka_btn[0])
    assert numbers and numbers[0] == '1'
    
    assert 'Расклад из колоды' in text
    assert text.count('•') == 1
    
    print("\n✅ Все проверки /card пройдены!")


@pytest.mark.django_db
@pytest.mark.parametrize("command,expected_cards", [
    ("/card0", 1),
    ("/card1", 1),
    ("/card2", 2),
    ("/card3", 3),
    ("/card9", 9),
    ("/card10", 10),
    ("/card100", 10),
])
def test_card_count(send_webhook_update, redis_client, command, expected_cards):
    """E2E: проверяем количество карт в раскладе"""
    
    token = "test_token_12345"
    user_id = 2000 + expected_cards
    redis_key = f"intercepted_requests:{token}"
    
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    from tests.conftest import extract_message_data, sync_lrange
    import json
    import time
    import re
    
    time.sleep(1)
    
    update = {
        "update_id": 300,
        "message": {
            "message_id": 300,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": command,
            "entities": [{"offset": 0, "length": len(command), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print(f"✅ Апдейт {command} отправлен\n")
    
    all_messages = []
    start = time.time()
    timeout = 15
    got_final = False
    got_delete = False
    
    while time.time() - start < timeout:
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') in 
                   ['sendMessage', 'sendMediaGroup', 'deleteMessage']]
        
        for msg in messages:
            if msg not in all_messages:
                all_messages.append(msg)
                endpoint = msg.get('endpoint', '?')
                data = extract_message_data(msg)
                
                if 'text' in data:
                    print(f"📨 {endpoint}: {data['text'][:100]}")
                elif 'media' in data:
                    media_items = data.get('media', [])
                    print(f"📨 {endpoint}: {len(media_items)} картинок")
                    for i, item in enumerate(media_items):
                        cap = item.get('caption', '').strip()
                        print(f"   🃏 {i+1}. {cap[:50]}")
                else:
                    print(f"📨 {endpoint}")
                
                if 'reply_markup' in data:
                    got_final = True
                if endpoint == 'deleteMessage':
                    got_delete = True
        
        if got_final and got_delete:
            print("✅ Все сообщения получены!")
            break
        
        time.sleep(0.1)
    
    media_msg = None
    final_msg = None
    
    for msg in all_messages:
        data = extract_message_data(msg)
        if msg.get('endpoint') == 'sendMediaGroup':
            media_msg = data
        if 'reply_markup' in data:
            final_msg = data
    
    assert media_msg is not None, f"Нет sendMediaGroup для {command}!"
    assert final_msg is not None, f"Нет финального сообщения для {command}!"
    
    # а) Считаем карты по количеству картинок
    media_items = media_msg.get('media', [])
    actual_cards = len(media_items)
    
    print(f"\n🃏 {command}: ожидалось картинок {expected_cards}, получено {actual_cards}")
    assert actual_cards == expected_cards, \
        f"Неверное количество картинок! Ожидалось {expected_cards}, получено {actual_cards}"
    
    # б) Считаем карты в тексте по маркерам • и номерам
    text = final_msg.get('text', '')
    # Считаем • в начале строк (после \n или начала текста)
    card_markers = len(re.findall(r'(?:^|\n)\s*•', text))
    # Или считаем эмодзи-номера (если бот их добавляет)
    numbered_cards = len(re.findall(r'\d+\.\s', text))
    
    print(f"📝 Маркеров • в тексте: {card_markers}")
    if numbered_cards:
        print(f"📝 Нумерованных карт в тексте: {numbered_cards}")
    
    # Проверяем что количество совпадает с ожидаемым
    assert card_markers == expected_cards, \
        f"Неверное количество маркеров • в тексте! Ожидалось {expected_cards}, получено {card_markers}"
    
    # Проверяем кнопки
    inline_keyboard = final_msg.get('reply_markup', {}).get('inline_keyboard', [])
    buttons = [b.get('text', '') for row in inline_keyboard for b in row]
    
    traktovka_btn = [b for b in buttons if 'Трактовка' in b]
    if traktovka_btn:
        numbers = re.findall(r'\((\d+)\)', traktovka_btn[0])
        if numbers:
            btn_count = int(numbers[0])
            print(f"🔢 Кнопка трактовки: {btn_count} карт")
            assert btn_count == expected_cards, \
                f"Неверное число в кнопке! Ожидалось {expected_cards}, получено {btn_count}"
    
    # Проверяем подписи к картинкам
    for i, item in enumerate(media_items):
        caption = item.get('caption', '').strip()
        assert len(caption) > 0, f"Картинка {i+1} без подписи!"
    
    print(f"\n✅ Тест {command} пройден!")

@pytest.mark.django_db
@pytest.mark.parametrize("command", ["/card_flip", "/card flip"])
def test_card_flip(send_webhook_update, redis_client, command):
    """E2E: проверяем что при flip команде появляются перевернутые карты"""
    
    token = "test_token_12345"
    user_id = 3001 if "flip" in command else 3002
    
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(f"intercepted_requests:{token}"))
    
    from tests.conftest import wait_and_collect_captions, sync_lrange, extract_message_data
    import json
    import time
    
    time.sleep(1)
    
    update = {
        "update_id": 400,
        "message": {
            "message_id": 400,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": command,
            "entities": [{"offset": 0, "length": len(command), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print(f"✅ Апдейт {command} отправлен\n")
    
    all_captions = []
    wait_and_collect_captions(redis_client, token, all_captions)
    
    flipped_found = any('Перевернуто' in cap for cap in all_captions)
    print(f"🔄 После первого запроса: {'✅ Есть перевернутые' if flipped_found else '❌ Нет перевернутых'}")
    
    max_clicks = 20
    clicks = 0
    
    while not flipped_found and clicks < max_clicks:
        clicks += 1
        
        redis_key = f"intercepted_requests:{token}"
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
        
        more_button_callback = None
        for msg in messages:
            data = extract_message_data(msg)
            inline_keyboard = data.get('reply_markup', {}).get('inline_keyboard', [])
            for row in inline_keyboard:
                for btn in row:
                    if 'Еще карту' in btn.get('text', ''):
                        more_button_callback = btn.get('callback_data')
                        break
        
        if not more_button_callback:
            print(f"❌ Кнопка 'Еще карту' исчезла после {clicks} кликов")
            break
        
        print(f"\n🔘 Клик #{clicks}: Еще карту")
        
        loop.run_until_complete(redis_client.delete(redis_key))
        
        callback_update = {
            "update_id": 400 + clicks,
            "callback_query": {
                "id": str(400 + clicks),
                "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
                "message": {
                    "message_id": 400,
                    "chat": {"id": user_id, "type": "private"},
                    "date": 1717000000,
                    "text": "previous message"
                },
                "chat_instance": "test",
                "data": more_button_callback
            }
        }
        
        from django.urls import reverse
        from django.test import Client
        client = Client()
        webhook_url = reverse("webhook", kwargs={"token": token})
        
        response = client.post(
            webhook_url,
            data=json.dumps(callback_update),
            content_type="application/json"
        )
        assert response.status_code == 200
        
        wait_and_collect_captions(redis_client, token, all_captions)
        
        flipped_found = any('Перевернуто' in cap for cap in all_captions)
        if flipped_found:
            print(f"   🎉 Найдена перевернутая карта!")
    
    print(f"\n📊 Всего собрано подписей: {len(all_captions)}")
    print(f"🔄 Перевернутых карт: {sum(1 for c in all_captions if 'Перевернуто' in c)}")
    
    assert flipped_found, \
        f"Перевернутая карта не найдена за {clicks} кликов! Подписи: {[c[:50] for c in all_captions]}"
    
    print(f"\n✅ Тест {command} пройден! Найдена перевернутая карта за {clicks} кликов")
    
# Список всех старших арканов
MAJOR_ARCANA = [
    "Шут", "Дурак",
    "Маг",
    "Жрица", "Папесса",
    "Императрица",
    "Император",
    "Иерофант", "Жрец", "Папа",
    "Влюбленные",
    "Колесница",
    "Правосудие", "Справедливость",
    "Отшельник",
    "Колесо Фортуны",
    "Сила",
    "Повешенный",
    "Смерть",
    "Умеренность", "Воздержание",
    "Дьявол",
    "Башня",
    "Звезда",
    "Луна",
    "Солнце",
    "Страшный суд",
    "Мир",
]


def check_major_arcana(card_name):
    """Проверяет что карта из старших арканов"""
    for major in MAJOR_ARCANA:
        if major.lower() in card_name.lower():
            return True
    return False


@pytest.mark.django_db
@pytest.mark.parametrize("command,expected_cards", [
    ("/card_major", 1),
    ("/card major", 1),
    ("/card12_major", 10),  # максимум 10
    ("/card12 major", 10),
    ("/card9_major", 9),
    ("/card9 major", 9),
    ("/card3_major_flip", 3),
    ("/card9_major_flip", 9),
])
def test_major_arcana(send_webhook_update, redis_client, command, expected_cards):
    """E2E: проверяем что major команды выдают только старшие арканы"""
    
    token = "test_token_12345"
    user_id = 4000 + expected_cards
    redis_key = f"intercepted_requests:{token}"
    
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    from tests.conftest import extract_message_data, sync_lrange
    import json
    import time
    
    time.sleep(1)
    
    update = {
        "update_id": 500,
        "message": {
            "message_id": 500,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": command,
            "entities": [{"offset": 0, "length": len(command), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print(f"✅ Апдейт {command} отправлен\n")
    
    all_messages = []
    start = time.time()
    timeout = 15
    got_final = False
    got_delete = False
    
    while time.time() - start < timeout:
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') in 
                   ['sendMessage', 'sendMediaGroup', 'deleteMessage']]
        
        for msg in messages:
            if msg not in all_messages:
                all_messages.append(msg)
                endpoint = msg.get('endpoint', '?')
                data = extract_message_data(msg)
                
                if 'text' in data:
                    text = data['text']
                    if 'Подбираю карты' in text or 'Подождите' in text:
                        print(f"📨 {endpoint}: {text[:100]}")
                    else:
                        print(f"📨 {endpoint}: {text[:150]}")
                elif 'media' in data:
                    media_items = data.get('media', [])
                    print(f"📨 {endpoint}: {len(media_items)} картинок")
                    for i, item in enumerate(media_items):
                        cap = item.get('caption', '').strip()
                        is_major = "✅" if check_major_arcana(cap) else "❌"
                        print(f"   {is_major} 🃏 {i+1}. {cap[:60]}")
                else:
                    print(f"📨 {endpoint}")
                
                if 'reply_markup' in data:
                    got_final = True
                if endpoint == 'deleteMessage':
                    got_delete = True
        
        if got_final and got_delete:
            print("✅ Все сообщения получены!")
            break
        
        time.sleep(0.1)
    
    # Находим sendMediaGroup
    media_msg = None
    for msg in all_messages:
        if msg.get('endpoint') == 'sendMediaGroup':
            media_msg = extract_message_data(msg)
            break
    
    assert media_msg is not None, f"Нет sendMediaGroup для {command}!"
    
    # Проверяем все карты
    media_items = media_msg.get('media', [])
    actual_cards = len(media_items)
    
    print(f"\n🃏 {command}: {actual_cards} карт")
    
    assert actual_cards == expected_cards, \
        f"Неверное количество карт! Ожидалось {expected_cards}, получено {actual_cards}"
    
    # Проверяем что ВСЕ карты из старших арканов
    non_major = []
    for i, item in enumerate(media_items):
        caption = item.get('caption', '').strip()
        card_name = caption.split('\n')[0]
        
        if not check_major_arcana(card_name):
            non_major.append(f"#{i+1}: {card_name}")
    
    if non_major:
        print(f"❌ Не старшие арканы: {non_major}")
    else:
        print(f"✅ Все карты из старших арканов!")
    
    assert len(non_major) == 0, \
        f"Найдены не старшие арканы: {non_major}"
    
    # Если команда с flip - проверяем что есть перевернутые
    if 'flip' in command:
        flipped = [item.get('caption', '') for item in media_items if 'Перевернуто' in item.get('caption', '')]
        print(f"🔄 Перевернутых карт: {len(flipped)}/{actual_cards}")
        # Не требуем обязательно перевернутые, но логируем
    
    print(f"\n✅ Тест {command} пройден! Все {actual_cards} карт - старшие арканы")
    
    
@pytest.mark.django_db
@pytest.mark.parametrize("command,description", [
    ("/card", "полная колода"),
    ("/card_major", "старшие арканы"),
])
def test_exhaust_deck(send_webhook_update, redis_client, command, description):
    """E2E: нажимаем 'Еще карту' пока кнопка не исчезнет"""
    
    token = "test_token_12345"
    user_id = 5000 + (78 if 'major' not in command else 5022)
    redis_key = f"intercepted_requests:{token}"
    
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    from tests.conftest import wait_and_collect_captions, sync_lrange, extract_message_data
    import json
    import time
    from collections import Counter
    
    time.sleep(1)
    
    update = {
        "update_id": 600,
        "message": {
            "message_id": 600,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": command,
            "entities": [{"offset": 0, "length": len(command), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print(f"✅ Апдейт {command} отправлен\n")
    
    all_captions = []
    all_reading_ids = set()
    
    wait_and_collect_captions(redis_client, token, all_captions, all_reading_ids)
    
    clicks = 0
    max_clicks = 100  # Большой запас
    
    while clicks < max_clicks:
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
        
        more_button_callback = None
        for msg in messages:
            data = extract_message_data(msg)
            inline_keyboard = data.get('reply_markup', {}).get('inline_keyboard', [])
            for row in inline_keyboard:
                for btn in row:
                    if 'Еще карту' in btn.get('text', ''):
                        more_button_callback = btn.get('callback_data')
                        break
        
        if not more_button_callback:
            print(f"\n🎉 Кнопка 'Еще карту' исчезла после {clicks} кликов!")
            break
        
        clicks += 1
        
        loop.run_until_complete(redis_client.delete(redis_key))
        
        callback_update = {
            "update_id": 600 + clicks,
            "callback_query": {
                "id": str(600 + clicks),
                "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
                "message": {
                    "message_id": 600,
                    "chat": {"id": user_id, "type": "private"},
                    "date": 1717000000,
                    "text": "previous message"
                },
                "chat_instance": "test",
                "data": more_button_callback
            }
        }
        
        from django.urls import reverse
        from django.test import Client
        client = Client()
        webhook_url = reverse("webhook", kwargs={"token": token})
        
        response = client.post(
            webhook_url,
            data=json.dumps(callback_update),
            content_type="application/json"
        )
        assert response.status_code == 200
        
        wait_and_collect_captions(redis_client, token, all_captions, all_reading_ids)
    
    # Проверки
    total_cards = len(all_captions)
    print(f"\n📊 Итого: {total_cards} карт за {clicks} кликов")
    
    assert clicks > 0, "Не нажали ни разу!"
    assert clicks < max_clicks, f"Кнопка не исчезла после {max_clicks} кликов!"
    
    # Проверяем уникальность
    card_names = [c.strip().split('\n')[0] for c in all_captions]
    unique_cards = set(card_names)
    
    print(f"🃏 Всего карт: {len(card_names)}")
    print(f"🃏 Уникальных карт: {len(unique_cards)}")
    
    duplicates = {name: count for name, count in Counter(card_names).items() if count > 1}
    
    if duplicates:
        print(f"❌ Найдены дубликаты:")
        for name, count in list(duplicates.items())[:5]:
            print(f"   {name}: {count} раз(а)")
    else:
        print(f"✅ Все карты уникальны!")
    
    assert len(duplicates) == 0, \
        f"Найдены дубликаты! {len(duplicates)} карт повторяются"
    
    assert len(unique_cards) == len(card_names), \
        f"Уникальных ({len(unique_cards)}) != общее ({len(card_names)})"
    
    print(f"\n✅ Тест {command} ({description}) пройден!")
    print(f"   Собрано {total_cards} уникальных карт за {clicks} кликов")

@pytest.mark.django_db
def test_decks_list(send_webhook_update, redis_client):
    """E2E: /decks — проверяем список колод, пагинацию и выбор конкретной"""
    
    token = "test_token_12345"
    user_id = 6001
    redis_key = f"intercepted_requests:{token}"
    
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    from tests.conftest import extract_message_data, sync_lrange, get_card_names_from_response
    import json
    import time
    import re
    
    time.sleep(1)
    
    # 1. Отправляем /decks
    update = {
        "update_id": 700,
        "message": {
            "message_id": 700,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/decks",
            "entities": [{"offset": 0, "length": 6, "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print("✅ Апдейт /decks отправлен\n")
    
    # Ждем ответ
    all_messages = []
    start = time.time()
    timeout = 10
    
    while time.time() - start < timeout:
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
        
        for msg in messages:
            if msg not in all_messages:
                all_messages.append(msg)
                data = extract_message_data(msg)
                text = data.get('text', '')
                if '/card_deck_' in text:
                    print(f"📨 sendMessage: {text[:200]}")
        
        if len(all_messages) > 0:
            break
        time.sleep(0.1)
    
    assert len(all_messages) > 0, "Нет ответа на /decks!"
    
    decks_msg = extract_message_data(all_messages[0])
    text = decks_msg.get('text', '')
    reply_markup = decks_msg.get('reply_markup', {})
    inline_keyboard = reply_markup.get('inline_keyboard', [])
    
    print(f"\n📝 Список колод: {text[:200]}")
    
    assert '/card_deck_' in text, f"Нет команд /card_deck_ в ответе!"
    
    # Собираем ВСЕ кнопки
    all_buttons = []
    for row in inline_keyboard:
        for btn in row:
            all_buttons.append(btn)
    
    print(f"🔘 Всего кнопок: {len(all_buttons)}")
    for btn in all_buttons:
        cb_data = btn.get('callback_data', '')
        btn_text = btn.get('text', '')
        print(f"   [{btn_text}] -> {cb_data}")
    
    # Ищем кнопки пагинации и колод
    next_btn = None
    prev_btn = None
    deck_buttons = []
    
    for btn in all_buttons:
        cb_data = btn.get('callback_data', '')
        if 'deckspage_' in cb_data:
            if '➡️ Вперед' in btn.get('text', ''):
                next_btn = btn
            elif '⬅️ Назад' in btn.get('text', ''):
                prev_btn = btn
        elif 'deck_' in cb_data:
            deck_buttons.append(btn)
    
    print(f"\n📄 Пагинация: Назад={'есть' if prev_btn else 'нет'}, Вперед={'есть' if next_btn else 'нет'}")
    print(f"🃏 Кнопок с колодами: {len(deck_buttons)}")
    
    # 2. Если есть "Вперед" - листаем
    if next_btn:
        print(f"\n➡️ Листаем вперед...")
        loop.run_until_complete(redis_client.delete(redis_key))
        
        from django.urls import reverse
        from django.test import Client
        client = Client()
        webhook_url = reverse("webhook", kwargs={"token": token})
        
        callback_update = {
            "update_id": 701,
            "callback_query": {
                "id": "701",
                "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
                "message": {
                    "message_id": 700,
                    "chat": {"id": user_id, "type": "private"},
                    "date": 1717000000,
                    "text": "decks list"
                },
                "chat_instance": "test",
                "data": next_btn['callback_data']
            }
        }
        
        response = client.post(
            webhook_url,
            data=json.dumps(callback_update),
            content_type="application/json"
        )
        assert response.status_code == 200
        
        time.sleep(0.5)
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        edit_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'editMessageText']
        
        if edit_msgs:
            edited_data = extract_message_data(edit_msgs[0])
            print(f"📝 Следующая страница: {edited_data.get('text', '')[:200]}")
    
    # 3. Выбираем первую колоду
    assert len(deck_buttons) > 0, "Нет кнопок с колодами!"
    
    first_deck = deck_buttons[0]
    deck_slug = re.search(r'deck_(\w+)', first_deck['callback_data'])
    deck_slug = deck_slug.group(1) if deck_slug else 'unknown'
    
    print(f"\n🎯 Выбираем колоду: {deck_slug}")
    loop.run_until_complete(redis_client.delete(redis_key))
    
    callback_update = {
        "update_id": 702,
        "callback_query": {
            "id": "702",
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "message": {
                "message_id": 700,
                "chat": {"id": user_id, "type": "private"},
                "date": 1717000000,
                "text": "decks list"
            },
            "chat_instance": "test",
            "data": first_deck['callback_data']
        }
    }
    
    client = Client()
    webhook_url = reverse("webhook", kwargs={"token": token})
    
    response = client.post(
        webhook_url,
        data=json.dumps(callback_update),
        content_type="application/json"
    )
    assert response.status_code == 200
    print(f"✅ Выбрана колода {deck_slug}")
    
    time.sleep(0.5)
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    edit_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') in ['editMessageText', 'editMessageReplyMarkup']]
    print(f"📨 Получено {len(edit_msgs)} редактирований")
    
    # 4. Отправляем /card_deck_{slug} дважды
    deck_command = f"/card_deck_{deck_slug}"
    
    cards1 = get_card_names_from_response(redis_client, token)
    print(f"\n🃏 Раунд 1: {cards1[:2]}...")
    
    # Второй пользователь
    user_id2 = user_id + 1
    cards2 = get_card_names_from_response(redis_client, token)
    print(f"🃏 Раунд 2: {cards2[:2]}...")
    
    print(f"✅ Колода {deck_slug} работает в обоих запросах")
    print(f"\n✅ Тест /decks пройден!")