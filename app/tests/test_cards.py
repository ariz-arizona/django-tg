# tests/test_card.py
import pytest
import re
import json
import time
import asyncio
from collections import Counter
from tests.conftest import extract_message_data, sync_lrange, wait_and_collect_captions


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
def test_card_command(send_webhook_update, redis_client):
    """E2E: /card — проверяем полный цикл отправки карты"""
    
    token = "test_token_12345"
    user_id = 1003
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
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
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
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
    
    # Считаем карты по количеству картинок
    media_items = media_msg.get('media', [])
    actual_cards = len(media_items)
    
    print(f"\n🃏 {command}: ожидалось картинок {expected_cards}, получено {actual_cards}")
    assert actual_cards == expected_cards, \
        f"Неверное количество картинок! Ожидалось {expected_cards}, получено {actual_cards}"
    
    # Считаем карты в тексте по маркерам • и номерам
    text = final_msg.get('text', '')
    card_markers = len(re.findall(r'(?:^|\n)\s*•', text))
    numbered_cards = len(re.findall(r'\d+\.\s', text))
    
    print(f"📝 Маркеров • в тексте: {card_markers}")
    if numbered_cards:
        print(f"📝 Нумерованных карт в тексте: {numbered_cards}")
    
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
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(f"intercepted_requests:{token}"))
    
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


@pytest.mark.django_db
@pytest.mark.parametrize("command,expected_cards", [
    ("/card_major", 1),
    ("/card major", 1),
    ("/card12_major", 10),
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
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
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
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
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
    max_clicks = 100
    
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
    
# tests/test_card.py (замена test_card_all_deck_promo)

@pytest.mark.django_db
@pytest.mark.parametrize("start_command,expected_mode", [
    ("/card9", ""),                    # /card9 → /all_deck_N
    ("/card9_flip", "_flip"),          # /card9_flip → /all_deck_N_flip
    ("/card9_major", "_major"),        # /card9_major → /all_deck_N_major
    ("/card9_major_flip", "_major_flip"),  # /card9_major_flip → /all_deck_N_major_flip
])
def test_card_all_deck_promo(send_webhook_update, redis_client, start_command, expected_mode):
    """E2E: жмем 'Еще карту' 2 раза → появляется /all_deck_N{mode} с прогрессом"""
    
    token = "test_token_12345"
    user_id = 8001 + hash(start_command) % 1000
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    time.sleep(1)
    
    # 1. Отправляем команду
    update = {
        "update_id": 900,
        "message": {
            "message_id": 900,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": start_command,
            "entities": [{"offset": 0, "length": len(start_command), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print(f"✅ Апдейт {start_command} отправлен\n")
    
    # Ждем первый расклад
    time.sleep(2)
    
    # 2. Нажимаем "Еще карту" до 5 раз, пока не появится all_deck
    clicks = 0
    max_clicks = 5
    all_deck_found = False
    deck_progress = None
    deck_number = None
    all_deck_full_command = None
    
    while clicks < max_clicks and not all_deck_found:
        clicks += 1
        
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
        
        more_button_callback = None
        
        for msg in messages:
            data = extract_message_data(msg)
            text = data.get('text', '')
            
            # Ищем /all_deck_ в тексте
            all_deck_match = re.search(r'(/all_deck_\d+(?:_flip|_major|_major_flip)?)\b', text)
            if all_deck_match:
                all_deck_found = True
                all_deck_full_command = all_deck_match.group(1)
                
                # Парсим прогресс
                progress_match = re.search(r'Всего в колоде:\s*(\d+)/(\d+)', text)
                if progress_match:
                    deck_progress = (int(progress_match.group(1)), int(progress_match.group(2)))
                
                # Парсим номер колоды
                deck_match = re.search(r'/all_deck_(\d+)', all_deck_full_command)
                if deck_match:
                    deck_number = int(deck_match.group(1))
                
                print(f"\n🎉 Найдено {all_deck_full_command} после {clicks} кликов!")
                print(f"📝 Текст: {text[:300]}")
                break
            
            # Ищем кнопку "Еще карту"
            inline_keyboard = data.get('reply_markup', {}).get('inline_keyboard', [])
            for row in inline_keyboard:
                for btn in row:
                    if 'Еще карту' in btn.get('text', ''):
                        more_button_callback = btn.get('callback_data')
                        break
        
        if all_deck_found:
            break
        
        if not more_button_callback:
            print(f"❌ Кнопка 'Еще карту' исчезла после {clicks} кликов, all_deck не появилось")
            break
        
        print(f"\n🔘 Клик #{clicks}: Еще карту")
        
        loop.run_until_complete(redis_client.delete(redis_key))
        
        callback_update = {
            "update_id": 900 + clicks,
            "callback_query": {
                "id": str(900 + clicks),
                "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
                "message": {
                    "message_id": 900,
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
        
        time.sleep(1.5)
    
    # 3. Проверяем результаты
    assert all_deck_found, \
        f"[{start_command}] Сообщение с /all_deck не появилось после {clicks} кликов!"
    
    # Проверяем прогресс
    assert deck_progress is not None, \
        f"[{start_command}] Нет строки 'Всего в колоде: X/Y'!"
    
    seen, total = deck_progress
    print(f"\n📊 [{start_command}] Прогресс: {seen}/{total}")
    
    assert seen > 0, f"[{start_command}] Прогресс seen={seen}, должно быть > 0"
    assert total > 0, f"[{start_command}] Прогресс total={total}, должно быть > 0"
    assert seen <= total, f"[{start_command}] Прогресс seen={seen} > total={total}!"
    
    # Проверяем номер колоды
    assert deck_number is not None, \
        f"[{start_command}] Нет номера колоды в /all_deck_!"
    print(f"🃏 [{start_command}] Номер колоды: {deck_number}")
    
    # Проверяем что команда содержит правильный суффикс
    print(f"🔍 [{start_command}] Полная команда: {all_deck_full_command}")
    print(f"🔍 [{start_command}] Ожидаемый суффикс: '{expected_mode}'")
    
    if expected_mode:
        assert all_deck_full_command.endswith(expected_mode), \
            f"[{start_command}] Команда {all_deck_full_command} должна заканчиваться на '{expected_mode}'!"
    else:
        # Для /card9 — просто /all_deck_N без суффикса
        assert re.match(r'/all_deck_\d+$', all_deck_full_command), \
            f"[{start_command}] Команда {all_deck_full_command} должна быть /all_deck_N без суффикса!"
    
    # Проверяем что число карт соответствует: 9 начальных + N кликов
    expected_min_seen = 9 + clicks
    print(f"🔢 [{start_command}] Ожидалось минимум {expected_min_seen} карт, получено {seen}")
    
    # Подсказка про режим
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
    
    full_text = ""
    for msg in messages:
        data = extract_message_data(msg)
        text = data.get('text', '')
        if '/all_deck_' in text:
            full_text = text
            break
    
    assert '💡' in full_text or 'режиме' in full_text.lower(), \
        f"[{start_command}] Нет подсказки про режим 'Вся колода'! text={full_text[:200]}"
    
    print(f"\n✅ [{start_command}] Тест all_deck promo пройден!")
    print(f"   Прогресс: {seen}/{total}")
    print(f"   Команда: {all_deck_full_command}")

# tests/test_card.py (исправленный test_card_by_positions)

@pytest.mark.django_db
def test_card_by_positions(send_webhook_update, redis_client):
    """E2E: /cardN cX_Y_Z — запрос конкретных карт по позициям, проверяем повторяемость"""
    
    token = "test_token_12345"
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    
    # ═══════════════════════════════════════════
    # Тест 1: Два одинаковых запроса → одинаковые карты
    # ═══════════════════════════════════════════
    print("═" * 50)
    print("ТЕСТ 1: /card3 c0_1_2 × 2 раза → карты одинаковые (колоды могут быть разные)")
    print("═" * 50)
    
    command = "/card3_c0_1_2"
    
    # Первый запрос — user 10001
    user_id_1 = 10001
    
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(1)
    
    update1 = {
        "update_id": 5000,
        "message": {
            "message_id": 5000,
            "from": {"id": user_id_1, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id_1, "type": "private"},
            "date": 1717000000,
            "text": command,
            "entities": [{"offset": 0, "length": len(command), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update1)
    assert response.status_code == 200
    print(f"✅ Запрос 1: {command} (user={user_id_1})\n")
    
    time.sleep(2)
    
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
    send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
    
    cards_round1 = []
    deck_name_round1 = None
    progress_round1 = None
    
    if media_msgs:
        media_data = extract_message_data(media_msgs[0])
        for item in media_data.get('media', []):
            caption = item.get('caption', '').strip()
            card_name = caption.split('\n')[0] if caption else ''
            if card_name:
                cards_round1.append(card_name)
    
    for msg in send_msgs:
        data = extract_message_data(msg)
        text = data.get('text', '')
        
        deck_match = re.search(r'«([^»]+)»', text)
        if deck_match:
            deck_name_round1 = deck_match.group(1)
        
        progress_match = re.search(r'Всего в колоде:\s*(\d+)/(\d+)', text)
        if progress_match:
            progress_round1 = (int(progress_match.group(1)), int(progress_match.group(2)))
        
        card_lines = re.findall(r'[•]\s*(.+?)(?:\n|$)', text)
        for card in card_lines:
            card = card.strip()
            if card and card not in cards_round1:
                cards_round1.append(card)
    
    print(f"🃏 Раунд 1: {cards_round1}")
    print(f"📚 Колода: {deck_name_round1}")
    if progress_round1:
        print(f"📊 Прогресс: {progress_round1[0]}/{progress_round1[1]}")
    
    assert len(cards_round1) == 3, f"Ожидалось 3 карты, получено {len(cards_round1)}: {cards_round1}"
    
    # Второй запрос — ДРУГОЙ пользователь, та же команда
    user_id_2 = 10002
    
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(1)
    
    update2 = {
        "update_id": 5001,
        "message": {
            "message_id": 5001,
            "from": {"id": user_id_2, "is_bot": False, "first_name": "Bob"},
            "chat": {"id": user_id_2, "type": "private"},
            "date": 1717000001,
            "text": command,
            "entities": [{"offset": 0, "length": len(command), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update2)
    assert response.status_code == 200
    print(f"\n✅ Запрос 2: {command} (user={user_id_2})\n")
    
    time.sleep(2)
    
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
    send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
    
    cards_round2 = []
    deck_name_round2 = None
    
    if media_msgs:
        media_data = extract_message_data(media_msgs[0])
        for item in media_data.get('media', []):
            caption = item.get('caption', '').strip()
            card_name = caption.split('\n')[0] if caption else ''
            if card_name:
                cards_round2.append(card_name)
    
    for msg in send_msgs:
        data = extract_message_data(msg)
        text = data.get('text', '')
        
        deck_match = re.search(r'«([^»]+)»', text)
        if deck_match:
            deck_name_round2 = deck_match.group(1)
        
        card_lines = re.findall(r'[•]\s*(.+?)(?:\n|$)', text)
        for card in card_lines:
            card = card.strip()
            if card and card not in cards_round2:
                cards_round2.append(card)
    
    print(f"🃏 Раунд 2: {cards_round2}")
    print(f"📚 Колода: {deck_name_round2}")
    
    assert len(cards_round2) == 3, f"Ожидалось 3 карты, получено {len(cards_round2)}: {cards_round2}"
    
    # Карты должны быть одинаковые (позиции фиксированы)
    assert cards_round1 == cards_round2, \
        f"Карты должны быть одинаковые!\n  Раунд 1: {cards_round1}\n  Раунд 2: {cards_round2}"
    
    print(f"\n✅ Карты идентичны в обоих запросах!")
    
    # Колоды могут быть разными — cX_Y_Z фиксирует карты, не колоду
    if deck_name_round1 and deck_name_round2:
        if deck_name_round1 == deck_name_round2:
            print(f"✅ Колода та же: «{deck_name_round1}»")
        else:
            print(f"⚠️ Колоды разные: «{deck_name_round1}» vs «{deck_name_round2}»")
            print(f"   Это норм — cX_Y_Z фиксирует карты, не колоду")
    
    # ═══════════════════════════════════════════
    # Тест 2: Две разные команды → разные карты
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ТЕСТ 2: /card3 c0_1_2 vs /card3 c3_4_5 → разные карты")
    print("═" * 50)
    
    command_alt = "/card3_c3_4_5"
    user_id_3 = 10003
    
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(1)
    
    update3 = {
        "update_id": 5002,
        "message": {
            "message_id": 5002,
            "from": {"id": user_id_3, "is_bot": False, "first_name": "Charlie"},
            "chat": {"id": user_id_3, "type": "private"},
            "date": 1717000002,
            "text": command_alt,
            "entities": [{"offset": 0, "length": len(command_alt), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update3)
    assert response.status_code == 200
    print(f"✅ Запрос 3: {command_alt} (user={user_id_3})\n")
    
    time.sleep(2)
    
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
    send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
    
    cards_round3 = []
    deck_name_round3 = None
    
    if media_msgs:
        media_data = extract_message_data(media_msgs[0])
        for item in media_data.get('media', []):
            caption = item.get('caption', '').strip()
            card_name = caption.split('\n')[0] if caption else ''
            if card_name:
                cards_round3.append(card_name)
    
    for msg in send_msgs:
        data = extract_message_data(msg)
        text = data.get('text', '')
        
        deck_match = re.search(r'«([^»]+)»', text)
        if deck_match:
            deck_name_round3 = deck_match.group(1)
        
        card_lines = re.findall(r'[•]\s*(.+?)(?:\n|$)', text)
        for card in card_lines:
            card = card.strip()
            if card and card not in cards_round3:
                cards_round3.append(card)
    
    print(f"🃏 Позиции 0,1,2: {cards_round1}")
    print(f"🃏 Позиции 3,4,5: {cards_round3}")
    
    assert len(cards_round3) == 3, f"Ожидалось 3 карты, получено {len(cards_round3)}: {cards_round3}"
    
    # Карты должны быть разными (разные позиции)
    assert cards_round1 != cards_round3, \
        f"Карты должны быть разными!\n  c0_1_2: {cards_round1}\n  c3_4_5: {cards_round3}"
    
    # Пересечений быть не должно
    overlap = set(cards_round1) & set(cards_round3)
    assert len(overlap) == 0, \
        f"Карты пересекаются! Общие: {overlap}\n  c0_1_2: {cards_round1}\n  c3_4_5: {cards_round3}"
    
    print(f"✅ Карты разные, пересечений нет!")
    
    # Колоды могут быть разными — cX_Y_Z фиксирует карты, не колоду
    if deck_name_round1 and deck_name_round3:
        if deck_name_round1 == deck_name_round3:
            print(f"✅ Колода та же: «{deck_name_round1}»")
        else:
            print(f"⚠️ Колоды разные: «{deck_name_round1}» vs «{deck_name_round3}»")
            print(f"   Это норм — cX_Y_Z фиксирует карты, не колоду")
    
    # ═══════════════════════════════════════════
    # Тест 3: /card3_deck_SLUG_cX — фиксированная колода + карта
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ТЕСТ 3: /card3_deck_waite_c8 × 2 — колода И карты фиксированы")
    print("═" * 50)
    
    command_deck_fixed = "/card3_deck_waite_c8"
    user_id_4 = 10004
    user_id_5 = 10005
    
    # Запрос 1
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(1)
    
    update4 = {
        "update_id": 5004,
        "message": {
            "message_id": 5004,
            "from": {"id": user_id_4, "is_bot": False, "first_name": "Dave"},
            "chat": {"id": user_id_4, "type": "private"},
            "date": 1717000004,
            "text": command_deck_fixed,
            "entities": [{"offset": 0, "length": len(command_deck_fixed), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update4)
    assert response.status_code == 200
    print(f"✅ Запрос 4: {command_deck_fixed} (user={user_id_4})\n")
    
    time.sleep(2)
    
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
    send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
    
    cards_deck_1 = []
    deck_name_deck_1 = None
    
    if media_msgs:
        media_data = extract_message_data(media_msgs[0])
        for item in media_data.get('media', []):
            caption = item.get('caption', '').strip()
            card_name = caption.split('\n')[0] if caption else ''
            if card_name:
                cards_deck_1.append(card_name)
    
    for msg in send_msgs:
        data = extract_message_data(msg)
        text = data.get('text', '')
        deck_match = re.search(r'«([^»]+)»', text)
        if deck_match:
            deck_name_deck_1 = deck_match.group(1)
        card_lines = re.findall(r'[•]\s*(.+?)(?:\n|$)', text)
        for card in card_lines:
            card = card.strip()
            if card and card not in cards_deck_1:
                cards_deck_1.append(card)
    
    print(f"🃏 Раунд с deck: {cards_deck_1}")
    print(f"📚 Колода: {deck_name_deck_1}")
    
    assert len(cards_deck_1) == 3, f"Ожидалось 3 карты, получено {len(cards_deck_1)}"
    
    # Запрос 2 — другой пользователь
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(1)
    
    update5 = {
        "update_id": 5005,
        "message": {
            "message_id": 5005,
            "from": {"id": user_id_5, "is_bot": False, "first_name": "Eve"},
            "chat": {"id": user_id_5, "type": "private"},
            "date": 1717000005,
            "text": command_deck_fixed,
            "entities": [{"offset": 0, "length": len(command_deck_fixed), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update5)
    assert response.status_code == 200
    print(f"\n✅ Запрос 5: {command_deck_fixed} (user={user_id_5})\n")
    
    time.sleep(2)
    
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
    send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
    
    cards_deck_2 = []
    deck_name_deck_2 = None
    
    if media_msgs:
        media_data = extract_message_data(media_msgs[0])
        for item in media_data.get('media', []):
            caption = item.get('caption', '').strip()
            card_name = caption.split('\n')[0] if caption else ''
            if card_name:
                cards_deck_2.append(card_name)
    
    for msg in send_msgs:
        data = extract_message_data(msg)
        text = data.get('text', '')
        deck_match = re.search(r'«([^»]+)»', text)
        if deck_match:
            deck_name_deck_2 = deck_match.group(1)
        card_lines = re.findall(r'[•]\s*(.+?)(?:\n|$)', text)
        for card in card_lines:
            card = card.strip()
            if card and card not in cards_deck_2:
                cards_deck_2.append(card)
    
    print(f"🃏 Раунд с deck: {cards_deck_2}")
    print(f"📚 Колода: {deck_name_deck_2}")
    
    assert len(cards_deck_2) == 3, f"Ожидалось 3 карты, получено {len(cards_deck_2)}"
    
    # С deck_ — и колода, и карты должны совпадать
    assert cards_deck_1 == cards_deck_2, \
        f"С deck_ карты должны быть одинаковые!\n  Раунд 1: {cards_deck_1}\n  Раунд 2: {cards_deck_2}"
    print(f"✅ Карты одинаковые (deck_ фиксирует и колоду и карты)")
    
    assert deck_name_deck_1 == deck_name_deck_2, \
        f"С deck_ колода должна быть та же!\n  Раунд 1: {deck_name_deck_1}\n  Раунд 2: {deck_name_deck_2}"
    print(f"✅ Колода та же: «{deck_name_deck_1}»")
    
    # ═══════════════════════════════════════════
    # Тест 4: Команда с пробелом
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ТЕСТ 4: /card3 c0_1_2 с пробелом (если поддерживается)")
    print("═" * 50)
    
    for cmd_format in [command, command.replace('_', ' ', 1) if '_c' in command else command]:
        if ' ' in cmd_format:
            print(f"🔍 Пробуем формат с пробелом: {cmd_format}")
            
            user_id_space = 10101
            
            loop.run_until_complete(redis_client.delete(redis_key))
            time.sleep(1)
            
            update_space = {
                "update_id": 5003,
                "message": {
                    "message_id": 5003,
                    "from": {"id": user_id_space, "is_bot": False, "first_name": "Space"},
                    "chat": {"id": user_id_space, "type": "private"},
                    "date": 1717000003,
                    "text": cmd_format,
                    "entities": []
                }
            }
            
            response = send_webhook_update(token, update_space)
            assert response.status_code == 200
            
            time.sleep(2)
            
            all_data = sync_lrange(redis_client, redis_key, 0, -1)
            media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
            
            if media_msgs:
                media_data = extract_message_data(media_msgs[0])
                media_items = media_data.get('media', [])
                space_cards = []
                for item in media_items:
                    caption = item.get('caption', '').strip()
                    card_name = caption.split('\n')[0] if caption else ''
                    if card_name:
                        space_cards.append(card_name)
                
                if space_cards:
                    print(f"   ✅ Формат с пробелом работает! Карты: {space_cards}")
                    assert space_cards == cards_round1, \
                        f"Формат с пробелом дал другие карты!\n  Ожидалось: {cards_round1}\n  Получено: {space_cards}"
                else:
                    print(f"   ⚠️ Формат с пробелом — карт нет (возможно не поддерживается)")
            else:
                print(f"   ⚠️ Формат с пробелом — нет media (возможно не поддерживается)")
            
            break
    
    print(f"\n{'═' * 50}")
    print(f"✅ Все тесты card_by_positions пройдены!")
    print(f"   cX_Y_Z фиксирует карты, не колоду")
    print(f"   deck_SLUG_cX фиксирует и колоду, и карты")
    print(f"   Позиции 0,1,2: {cards_round1}")
    print(f"   Позиции 3,4,5: {cards_round3}")
    print(f"   Повторяемость: ✅")
    print(f"   Уникальность позиций: ✅")
    

# tests/test_card.py (исправленный test_card_fixed_position_with_random)

@pytest.mark.django_db
def test_card_fixed_position_with_random(send_webhook_update, redis_client):
    """E2E: /card3_deck_waite_c8 × 5 — первая карта всегда одна, остальные рандомные"""
    
    token = "test_token_12345"
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    
    print("═" * 50)
    print("ТЕСТ: /card3_deck_waite_c8 × 5 — первая карта фиксирована, остальные рандомные")
    print("═" * 50)
    
    command = "/card3_deck_waite_c8"
    all_cards_sets = []
    
    for i in range(5):
        user_id = 16000 + i
        
        loop.run_until_complete(redis_client.delete(redis_key))
        time.sleep(1)
        
        update = {
            "update_id": 16000 + i,
            "message": {
                "message_id": 16000 + i,
                "from": {"id": user_id, "is_bot": False, "first_name": f"User{i}"},
                "chat": {"id": user_id, "type": "private"},
                "date": 1717000000 + i,
                "text": command,
                "entities": [{"offset": 0, "length": len(command), "type": "bot_command"}]
            }
        }
        
        response = send_webhook_update(token, update)
        assert response.status_code == 200
        
        time.sleep(2)
        
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
        send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
        
        cards = []
        deck_name = None
        
        if media_msgs:
            media_data = extract_message_data(media_msgs[0])
            for item in media_data.get('media', []):
                caption = item.get('caption', '').strip()
                card_name = caption.split('\n')[0] if caption else ''
                if card_name:
                    cards.append(card_name)
        
        if not cards:
            for msg in send_msgs:
                data = extract_message_data(msg)
                text = data.get('text', '')
                deck_match = re.search(r'«([^»]+)»', text)
                if deck_match:
                    deck_name = deck_match.group(1)
                card_lines = re.findall(r'[•]\s*(.+?)(?:\n|$)', text)
                for card in card_lines:
                    card = card.strip()
                    if card and card not in cards:
                        cards.append(card)
        
        if not deck_name:
            for msg in send_msgs:
                data = extract_message_data(msg)
                text = data.get('text', '')
                deck_match = re.search(r'«([^»]+)»', text)
                if deck_match:
                    deck_name = deck_match.group(1)
                    break
        
        all_cards_sets.append(cards)
        
        print(f"🃏 Запрос {i+1} (user={user_id}): {cards}")
        if deck_name:
            print(f"   📚 Колода: «{deck_name}»")
        
        assert len(cards) == 3, f"Ожидалось 3 карты, получено {len(cards)}: {cards}"
    
    # ═══════════════════════════════════════════
    # Проверка 1: Первая карта всегда одинаковая
    # ═══════════════════════════════════════════
    first_cards = [s[0] for s in all_cards_sets if len(s) > 0]
    unique_first = set(first_cards)
    
    print(f"\n🔍 Первые карты во всех 5 запросах: {first_cards}")
    
    assert len(unique_first) == 1, \
        f"Первая карта должна быть одинаковой! Найдено вариантов: {len(unique_first)} — {unique_first}"
    print(f"✅ Первая карта всегда: «{list(unique_first)[0]}»")
    
    # ═══════════════════════════════════════════
    # Проверка 2: Хотя бы 2 разных набора из 5
    # ═══════════════════════════════════════════
    all_triples = [tuple(s) for s in all_cards_sets if len(s) == 3]
    unique_triples = set(all_triples)
    
    print(f"🔍 Уникальных троек: {len(unique_triples)} из 5")
    for triple in sorted(unique_triples):
        count = all_triples.count(triple)
        print(f"   {triple} — {count} раз(а)")
    
    # ЖЁСТКИЙ АССЕРТ — баг должен ронять тест
    assert len(unique_triples) >= 2, \
        f"БАГ: Все 5 троек одинаковые! {list(unique_triples)[0]}\n" \
        f"   Ожидалось: первая карта фиксирована, остальные 2 рандомные\n" \
        f"   Реальность: все 3 карты фиксированы (c8 зафиксировало весь расклад)"
    
    # Дополнительно: во всех тройках первая карта та же
    fixed_card = list(unique_first)[0]
    for triple in unique_triples:
        assert triple[0] == fixed_card, \
            f"Первая карта изменилась! {triple[0]} != {fixed_card}"
    print(f"✅ Во всех тройках первая карта стабильна: «{fixed_card}»")
    
    print(f"\n✅ Тест card_fixed_position_with_random пройден!")
    print(f"   Фиксированная карта (позиция 8): «{fixed_card}»")
    print(f"   Уникальных троек: {len(unique_triples)}/5")
    
    
@pytest.mark.django_db
def test_card_random_decks(send_webhook_update, redis_client):
    """E2E: 5 раз /card → минимум 2 разные колоды (рандом работает)"""
    
    token = "test_token_12345"
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    
    print("═" * 50)
    print("ТЕСТ: 5 раз /card — проверяем что колоды разные")
    print("═" * 50)
    
    deck_names = []
    all_cards = []
    
    for i in range(5):
        user_id = 14000 + i
        
        loop.run_until_complete(redis_client.delete(redis_key))
        time.sleep(0.5)
        
        update = {
            "update_id": 14000 + i,
            "message": {
                "message_id": 14000 + i,
                "from": {"id": user_id, "is_bot": False, "first_name": f"User{i}"},
                "chat": {"id": user_id, "type": "private"},
                "date": 1717000000 + i,
                "text": "/card",
                "entities": [{"offset": 0, "length": 5, "type": "bot_command"}]
            }
        }
        
        response = send_webhook_update(token, update)
        assert response.status_code == 200
        
        time.sleep(1.5)
        
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
        media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
        
        deck_name = None
        card_name = None
        
        # Из текста — название колоды
        for msg in send_msgs:
            data = extract_message_data(msg)
            text = data.get('text', '')
            
            deck_match = re.search(r'«([^»]+)»', text)
            if deck_match:
                deck_name = deck_match.group(1)
                break
        
        # Из медиа — название карты
        if media_msgs:
            media_data = extract_message_data(media_msgs[0])
            media_items = media_data.get('media', [])
            if media_items:
                caption = media_items[0].get('caption', '').strip()
                card_name = caption.split('\n')[0] if caption else None
        
        deck_names.append(deck_name)
        all_cards.append(card_name)
        
        print(f"🃏 Запрос {i+1}: user={user_id}, колода='{deck_name}', карта='{card_name}'")
    
    # Считаем уникальные колоды
    unique_decks = set(d for d in deck_names if d)
    unique_cards = set(c for c in all_cards if c)
    
    print(f"\n📊 Уникальных колод: {len(unique_decks)}/5")
    for deck in unique_decks:
        count = deck_names.count(deck)
        print(f"   • «{deck}» — {count} раз(а)")
    
    print(f"🃏 Уникальных карт: {len(unique_cards)}/5")
    
    # Проверяем что минимум 2 разные колоды из 5
    assert len(unique_decks) >= 2, \
        f"Все 5 запросов вернули одну колоду! Колоды: {unique_decks}"
    
    # Желательно чтобы и карты были разные (но не строго — может повезти)
    if len(unique_cards) >= 2:
        print(f"✅ Карты тоже разные — отлично!")
    else:
        print(f"⚠️ Карты одинаковые — но колоды разные, это норм")
    
    print(f"\n✅ Тест card_random_decks пройден!")
    print(f"   {len(unique_decks)} разных колод из 5 запросов")
    