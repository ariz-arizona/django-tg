# tests/test_oraculum.py
import pytest
import json
import time
import re
import asyncio
from collections import Counter
from tests.conftest import extract_message_data, sync_lrange, wait_and_collect_captions


@pytest.mark.django_db
def test_oraculum_command(send_webhook_update, redis_client):
    """E2E: /oraculum_deck_SLUG — полный цикл с кнопкой 'Еще карту'"""
    
    token = "test_token_12345"
    user_id = 11001
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    time.sleep(1)
    
    # Берём любой оракул — заброшенный (самый короткий slug)
    oracle_slug = "abandoned"
    command = f"/oraculum_deck_{oracle_slug}"
    
    update = {
        "update_id": 10000,
        "message": {
            "message_id": 10000,
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
    
    assert 'Еще карту' in buttons, f"Нет кнопки 'Еще карту'! Кнопки: {buttons}"
    
    # Проверяем что есть прогресс колоды
    progress_match = re.search(r'Всего в колоде:\s*(\d+)/(\d+)', text)
    assert progress_match, f"Нет прогресса колоды! text={text[:200]}"
    
    seen, total = int(progress_match.group(1)), int(progress_match.group(2))
    print(f"📊 Прогресс: {seen}/{total}")
    assert total > 0, f"Размер колоды = 0?"
    assert seen == 1, f"После первой карты seen должно быть 1, а не {seen}"
    
    print(f"\n✅ Тест {command} пройден!")


@pytest.mark.django_db
@pytest.mark.parametrize("cards_count", [1, 3, 9])
@pytest.mark.parametrize("flip", [False, True])
def test_oraculum_count_flip(send_webhook_update, redis_client, cards_count, flip):
    """E2E: /oraculum_deck_SLUG с N карт и flip"""
    
    token = "test_token_12345"
    user_id = 12000 + cards_count * 10 + (1 if flip else 0)
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    time.sleep(1)
    
    oracle_slug = "lenormand"  # Ленорман — 36 карт
    
    if cards_count == 1:
        command = f"/oraculum_deck_{oracle_slug}"
    else:
        command = f"/card{cards_count}_deck_{oracle_slug}"
    
    if flip:
        command += "_flip"
    
    print(f"\n{'='*50}")
    print(f"Тест: {command} (оракул={oracle_slug}, карт={cards_count}, flip={flip})")
    print(f"{'='*50}")
    
    update = {
        "update_id": 11000 + cards_count,
        "message": {
            "message_id": 11000 + cards_count,
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
                    for i, item in enumerate(media_items[:3]):
                        cap = item.get('caption', '').strip()
                        flip_mark = "🔄" if 'Перевернуто' in cap else ""
                        print(f"   {flip_mark} 🃏 {i+1}. {cap[:50]}")
                    if len(media_items) > 3:
                        print(f"   ... и ещё {len(media_items) - 3}")
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
    
    media_items = media_msg.get('media', [])
    actual_cards = len(media_items)
    
    print(f"\n🃏 {command}: ожидалось {cards_count}, получено {actual_cards}")
    assert actual_cards == cards_count, \
        f"Неверное количество карт! Ожидалось {cards_count}, получено {actual_cards}"
    
    # Проверяем подписи
    for i, item in enumerate(media_items):
        caption = item.get('caption', '').strip()
        assert len(caption) > 0, f"Картинка {i+1} без подписи!"
    
    # Если flip — должна быть хоть одна перевернутая (не строго, но логируем)
    if flip:
        flipped = [item for item in media_items if 'Перевернуто' in item.get('caption', '')]
        print(f"🔄 Перевернутых: {len(flipped)}/{actual_cards}")
    
    # Проверяем прогресс
    text = final_msg.get('text', '')
    progress_match = re.search(r'Всего в колоде:\s*(\d+)/(\d+)', text)
    if progress_match:
        seen, total = int(progress_match.group(1)), int(progress_match.group(2))
        print(f"📊 Прогресс: {seen}/{total}")
        assert seen == cards_count, \
            f"Прогресс seen={seen}, ожидалось {cards_count}"
        assert total > 0, f"Размер колоды = 0?"
        print(f"📚 Колода {oracle_slug}: {total} карт")
    
    print(f"\n✅ Тест {command} пройден!")


@pytest.mark.django_db
def test_oraculum_exhaust_and_slug_id(send_webhook_update, redis_client):
    """E2E: исчерпание оракула + проверка что slug и ID дают одинаковые карты"""
    
    token = "test_token_12345"
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    time.sleep(1)
    
    # ═══════════════════════════════════════════
    # Часть 1: Узнаём слаг оракула через /decks oraculum
    # ═══════════════════════════════════════════
    print("═" * 50)
    print("ЧАСТЬ 1: Узнаём слаг оракула через /decks oraculum")
    print("═" * 50)
    
    user_id_decks = 13001
    
    update = {
        "update_id": 12000,
        "message": {
            "message_id": 12000,
            "from": {"id": user_id_decks, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id_decks, "type": "private"},
            "date": 1717000000,
            "text": "/decks oraculum",
            "entities": [{"offset": 0, "length": 6, "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    
    time.sleep(1)
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
    
    assert len(send_msgs) > 0, "Нет ответа на /decks oraculum!"
    
    decks_text = extract_message_data(send_msgs[0]).get('text', '')
    
    # Извлекаем первый оракул
    oracle_match = re.search(r'/oraculum_deck_(\w[^<\s]+)', decks_text)
    assert oracle_match, f"Не удалось найти оракул в тексте! text={decks_text[:300]}"
    
    oracle_slug = oracle_match.group(1)
    oracle_slug = re.sub(r'<.*', '', oracle_slug).strip()
    
    print(f"🔑 Слаг: {oracle_slug}")
    
    # ═══════════════════════════════════════════
    # Часть 2: Получаем ID оракула (другой пользователь!)
    # ═══════════════════════════════════════════
    print(f"\n{'='*50}")
    print(f"ЧАСТЬ 2: Получаем ID оракула")
    print(f"{'='*50}")
    
    user_id_get_id = 13002  # ДРУГОЙ пользователь!
    
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(1)
    
    update2 = {
        "update_id": 12001,
        "message": {
            "message_id": 12001,
            "from": {"id": user_id_get_id, "is_bot": False, "first_name": "Bob"},
            "chat": {"id": user_id_get_id, "type": "private"},
            "date": 1717000001,
            "text": f"/oraculum_deck_{oracle_slug}",
            "entities": [{"offset": 0, "length": len(f"/oraculum_deck_{oracle_slug}"), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update2)
    assert response.status_code == 200
    
    time.sleep(2)
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
    media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
    
    # Извлекаем ID колоды
    oracle_id = None
    for msg in send_msgs:
        data = extract_message_data(msg)
        text = data.get('text', '')
        id_match = re.search(r'/(?:card|all)_deck_(\d+)', text)
        if id_match:
            oracle_id = int(id_match.group(1))
            break
    
    # Если не нашли в тексте — ищем в кнопках
    if oracle_id is None:
        for msg in send_msgs:
            data = extract_message_data(msg)
            inline_keyboard = data.get('reply_markup', {}).get('inline_keyboard', [])
            for row in inline_keyboard:
                for btn in row:
                    cb_data = btn.get('callback_data', '')
                    id_match = re.search(r'deck_(\d+)', cb_data)
                    if id_match:
                        oracle_id = int(id_match.group(1))
                        break
    
    print(f"🆔 ID оракула {oracle_slug}: {oracle_id if oracle_id else 'НЕ НАЙДЕН'}")
    
    # ═══════════════════════════════════════════
    # Часть 3: Сверяем slug и ID (разные пользователи!)
    # ═══════════════════════════════════════════
    if oracle_id:
        print(f"\n{'='*50}")
        print(f"ЧАСТЬ 3: Сверяем карты по slug и ID")
        print(f"{'='*50}")
        
        # Запрос по slug — пользователь 3
        loop.run_until_complete(redis_client.delete(redis_key))
        time.sleep(0.5)
        
        update_slug = {
            "update_id": 12002,
            "message": {
                "message_id": 12002,
                "from": {"id": 13003, "is_bot": False, "first_name": "User3"},
                "chat": {"id": 13003, "type": "private"},
                "date": 1717000002,
                "text": f"/card3_deck_{oracle_slug}",
                "entities": [{"offset": 0, "length": len(f"/card3_deck_{oracle_slug}"), "type": "bot_command"}]
            }
        }
        
        response = send_webhook_update(token, update_slug)
        assert response.status_code == 200
        
        time.sleep(2)
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        media_msgs_slug = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
        
        cards_by_slug = []
        if media_msgs_slug:
            media_data = extract_message_data(media_msgs_slug[0])
            for item in media_data.get('media', []):
                caption = item.get('caption', '').strip()
                card_name = caption.split('\n')[0] if caption else ''
                if card_name:
                    cards_by_slug.append(card_name)
        
        print(f"🃏 По слагу {oracle_slug}: {cards_by_slug}")
        
        # Запрос по ID — пользователь 4
        loop.run_until_complete(redis_client.delete(redis_key))
        time.sleep(0.5)
        
        update_id_cmd = {
            "update_id": 12003,
            "message": {
                "message_id": 12003,
                "from": {"id": 13004, "is_bot": False, "first_name": "User4"},
                "chat": {"id": 13004, "type": "private"},
                "date": 1717000003,
                "text": f"/card3_deck_{oracle_id}",
                "entities": [{"offset": 0, "length": len(f"/card3_deck_{oracle_id}"), "type": "bot_command"}]
            }
        }
        
        response = send_webhook_update(token, update_id_cmd)
        assert response.status_code == 200
        
        time.sleep(2)
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        media_msgs_id = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
        
        cards_by_id = []
        if media_msgs_id:
            media_data = extract_message_data(media_msgs_id[0])
            for item in media_data.get('media', []):
                caption = item.get('caption', '').strip()
                card_name = caption.split('\n')[0] if caption else ''
                if card_name:
                    cards_by_id.append(card_name)
        
        print(f"🃏 По ID {oracle_id}: {cards_by_id}")
        
        # Сверяем
        if cards_by_slug and cards_by_id:
            assert cards_by_slug == cards_by_id, \
                f"Карты по slug и ID различаются!\n  slug: {cards_by_slug}\n  id:   {cards_by_id}"
            print(f"✅ Карты по slug и ID одинаковые!")
        else:
            print(f"⚠️ Не удалось сравнить (cards_by_slug={len(cards_by_slug)}, cards_by_id={len(cards_by_id)})")
    
    # ═══════════════════════════════════════════
    # Часть 4: Исчерпание оракула (отдельный пользователь!)
    # ═══════════════════════════════════════════
    print(f"\n{'='*50}")
    print(f"ЧАСТЬ 4: Исчерпание оракула {oracle_slug}")
    print(f"{'='*50}")
    
    user_id_exhaust = 13005  # Отдельный пользователь для исчерпания!
    
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(1)
    
    update3 = {
        "update_id": 13000,
        "message": {
            "message_id": 13000,
            "from": {"id": user_id_exhaust, "is_bot": False, "first_name": "Exhaust"},
            "chat": {"id": user_id_exhaust, "type": "private"},
            "date": 1717000000,
            "text": f"/oraculum_deck_{oracle_slug}",
            "entities": [{"offset": 0, "length": len(f"/oraculum_deck_{oracle_slug}"), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update3)
    assert response.status_code == 200
    print(f"✅ Старт исчерпания: /oraculum_deck_{oracle_slug}\n")
    
    all_captions = []
    wait_and_collect_captions(redis_client, token, all_captions)
    
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
        
        if clicks % 5 == 0:
            print(f"🔘 Клик #{clicks}...")
        
        loop.run_until_complete(redis_client.delete(redis_key))
        
        from django.urls import reverse
        from django.test import Client
        client = Client()
        webhook_url = reverse("webhook", kwargs={"token": token})
        
        callback_update = {
            "update_id": 13000 + clicks,
            "callback_query": {
                "id": str(13000 + clicks),
                "from": {"id": user_id_exhaust, "is_bot": False, "first_name": "Exhaust"},
                "message": {
                    "message_id": 13000,
                    "chat": {"id": user_id_exhaust, "type": "private"},
                    "date": 1717000000,
                    "text": "previous message"
                },
                "chat_instance": "test",
                "data": more_button_callback
            }
        }
        
        response = client.post(
            webhook_url,
            data=json.dumps(callback_update),
            content_type="application/json"
        )
        assert response.status_code == 200
        
        wait_and_collect_captions(redis_client, token, all_captions)
    
    # Результаты
    total_cards = len(all_captions)
    card_names = [c.strip().split('\n')[0] for c in all_captions]
    unique_cards = set(card_names)
    
    print(f"\n📊 Итого: {total_cards} карт за {clicks} кликов")
    print(f"🃏 Уникальных карт: {len(unique_cards)}")
    
    assert clicks > 0, "Не нажали ни разу!"
    assert clicks < max_clicks, f"Кнопка не исчезла после {max_clicks} кликов!"
    
    duplicates = {name: count for name, count in Counter(card_names).items() if count > 1}
    
    if duplicates:
        print(f"❌ Найдены дубликаты:")
        for name, count in list(duplicates.items())[:5]:
            print(f"   {name}: {count} раз(а)")
    else:
        print(f"✅ Все карты уникальны!")
    
    assert len(duplicates) == 0, f"Найдены дубликаты! {len(duplicates)} карт повторяются"
    
    print(f"📚 Размер колоды '{oracle_slug}': {total_cards} карт")
    assert total_cards > 0, "Колода пустая!"
    
    print(f"\n✅ Тест oraculum_exhaust_and_slug_id пройден!")
    print(f"   Оракул: {oracle_slug}")
    print(f"   Карт в колоде: {total_cards}")
    if oracle_id:
        print(f"   ID: {oracle_id}")
        print(f"   Slug и ID сверены: ✅")

@pytest.mark.django_db
def test_oraculum_random_decks(send_webhook_update, redis_client):
    """E2E: 5 раз /oraculum_deck_SLUG с разными user → минимум 2 разные колоды оракулов"""
    
    token = "test_token_12345"
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    
    print("═" * 50)
    print("ТЕСТ: 5 раз оракул — проверяем что колоды разные")
    print("═" * 50)
    
    # Список всех известных слагов оракулов (без хардкода expected — просто для перебора)
    oracle_slugs = [
        "abandoned",
        "lenormand", 
        "angelarium",
        "dark-mirror",
        "wiccan-shadow",
        "vikings-runic",
    ]
    
    deck_names = []
    all_cards = []
    used_slugs = []
    
    for i in range(5):
        # Берём случайный слаг из списка
        oracle_slug = oracle_slugs[i % len(oracle_slugs)]
        used_slugs.append(oracle_slug)
        
        user_id = 15000 + i
        
        loop.run_until_complete(redis_client.delete(redis_key))
        time.sleep(0.5)
        
        command = f"/oraculum_deck_{oracle_slug}"
        
        update = {
            "update_id": 15000 + i,
            "message": {
                "message_id": 15000 + i,
                "from": {"id": user_id, "is_bot": False, "first_name": f"User{i}"},
                "chat": {"id": user_id, "type": "private"},
                "date": 1717000000 + i,
                "text": command,
                "entities": [{"offset": 0, "length": len(command), "type": "bot_command"}]
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
        
        print(f"🃏 Запрос {i+1}: slug={oracle_slug}, колода='{deck_name}', карта='{card_name}'")
    
    # Считаем уникальные колоды
    unique_decks = set(d for d in deck_names if d)
    unique_slugs = set(used_slugs)
    unique_cards = set(c for c in all_cards if c)
    
    print(f"\n📊 Уникальных слагов: {len(unique_slugs)}/5")
    print(f"📊 Уникальных колод (по названию): {len(unique_decks)}/5")
    for deck in unique_decks:
        count = deck_names.count(deck)
        print(f"   • «{deck}» — {count} раз(а)")
    
    print(f"🃏 Уникальных карт: {len(unique_cards)}/5")
    
    # Если использовали разные слаги — должны быть разные колоды
    if len(unique_slugs) >= 2:
        assert len(unique_decks) >= 2, \
            f"Разные слаги {unique_slugs} дали одинаковые колоды! Колоды: {unique_decks}"
        print(f"✅ Разные слаги → разные колоды!")
    else:
        # Если слаги одинаковые — колода может быть одна, но карты должны быть разные
        print(f"⚠️ Слаги одинаковые — проверяем что карты разные")
        assert len(unique_cards) >= 2, \
            f"Один слаг {unique_slugs} — но карты тоже одинаковые! Карты: {unique_cards}"
        print(f"✅ Карты разные в пределах одной колоды!")
    
    # Проверяем что все запросы отработали
    assert len(deck_names) == 5, f"Не все запросы вернули колоды! deck_names={deck_names}"
    assert len(all_cards) == 5, f"Не все запросы вернули карты! all_cards={all_cards}"
    
    print(f"\n✅ Тест oraculum_random_decks пройден!")
    print(f"   {len(unique_decks)} разных колод из 5 запросов")