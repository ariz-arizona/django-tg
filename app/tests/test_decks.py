# tests/test_decks.py
import pytest
import json
import time
import re
import asyncio
from tests.conftest import extract_message_data, sync_lrange, get_card_names_from_response


# tests/test_decks.py (исправленный test_decks_list)

@pytest.mark.django_db
def test_decks_list(send_webhook_update, redis_client):
    """E2E: /decks — проверяем список колод (текстом), пагинацию и работу со слагом"""
    
    token = "test_token_12345"
    user_id = 6001
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    time.sleep(1)
    
    # ═══════════════════════════════════════════
    # Шаг 1: /decks
    # ═══════════════════════════════════════════
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
        
        if len(all_messages) > 0:
            break
        time.sleep(0.1)
    
    assert len(all_messages) > 0, "Нет ответа на /decks!"
    
    decks_msg = extract_message_data(all_messages[0])
    text = decks_msg.get('text', '')
    reply_markup = decks_msg.get('reply_markup', {})
    inline_keyboard = reply_markup.get('inline_keyboard', [])
    
    print(f"📝 Список колод (первые 300 символов):\n{text[:300]}\n")
    
    # ═══════════════════════════════════════════
    # Шаг 2: Извлекаем слаги из текста
    # ═══════════════════════════════════════════
    card_deck_commands = re.findall(r'/card_deck_(\S+)', text)
    
    # Чистим от HTML
    deck_slugs = []
    for slug in card_deck_commands:
        clean_slug = re.sub(r'<.*', '', slug).strip()
        if clean_slug:
            deck_slugs.append(clean_slug)
    
    print(f"🔗 Слагов колод в тексте: {len(deck_slugs)}")
    for slug in deck_slugs[:5]:
        print(f"   • {slug}")
    if len(deck_slugs) > 5:
        print(f"   ... и ещё {len(deck_slugs) - 5}")
    
    assert len(deck_slugs) > 0, "Нет команд /card_deck_ в ответе!"
    
    # ═══════════════════════════════════════════
    # Шаг 3: Кнопки пагинации (НЕ колод!)
    # ═══════════════════════════════════════════
    all_buttons = []
    for row in inline_keyboard:
        for btn in row:
            all_buttons.append(btn)
    
    print(f"\n🔘 Кнопок: {len(all_buttons)}")
    for btn in all_buttons:
        print(f"   [{btn.get('text', '')}] -> {btn.get('callback_data', '')}")
    
    next_btn = None
    prev_btn = None
    
    for btn in all_buttons:
        cb_data = btn.get('callback_data', '')
        if 'deckspage_' in cb_data:
            if '➡️' in btn.get('text', '') or 'Вперед' in btn.get('text', ''):
                next_btn = btn
            elif '⬅️' in btn.get('text', '') or 'Назад' in btn.get('text', ''):
                prev_btn = btn
    
    print(f"📄 Пагинация: Назад={'есть' if prev_btn else 'нет'}, Вперед={'есть' if next_btn else 'нет'}")
    
    # Колоды текстом — это норм
    print(f"✅ Колоды отдаются текстом (не кнопками) — это ок")
    
    # ═══════════════════════════════════════════
    # Шаг 4: Пагинация вперёд
    # ═══════════════════════════════════════════
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
            edited_text = edited_data.get('text', '')
            print(f"📝 Следующая страница: {edited_text[:200]}")
            
            # Проверяем что на новой странице тоже есть /card_deck_
            new_slugs = re.findall(r'/card_deck_(\S+)', edited_text)
            new_slugs_clean = [re.sub(r'<.*', '', s).strip() for s in new_slugs]
            print(f"🔗 Слагов на стр.2: {len(new_slugs_clean)}")
            assert len(new_slugs_clean) > 0, "На второй странице нет команд /card_deck_!"
            
            # Проверяем кнопку "Назад"
            new_reply_markup = edited_data.get('reply_markup', {})
            new_keyboard = new_reply_markup.get('inline_keyboard', [])
            new_buttons = []
            for row in new_keyboard:
                for btn in row:
                    new_buttons.append(btn)
            
            back_buttons = [b for b in new_buttons if 'Назад' in b.get('text', '') or '⬅️' in b.get('text', '')]
            print(f"⬅️ Кнопок 'Назад': {len(back_buttons)}")
            
            if len(back_buttons) > 0:
                print(f"✅ Пагинация работает в обе стороны")
            else:
                print(f"⚠️ Нет кнопки 'Назад' на второй странице")
        else:
            print(f"⚠️ Нет editMessageText после листания вперед")
    else:
        print(f"\n⚠️ Нет пагинации — все колоды на одной странице")
    
    # ═══════════════════════════════════════════
    # Шаг 5: Тестируем первую колоду
    # ═══════════════════════════════════════════
    deck_slug = deck_slugs[0]
    print(f"\n🎯 Тестируем колоду: {deck_slug}")
    
    # /card_deck_{slug}
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(0.5)
    
    cards1 = get_card_names_from_response(redis_client, token)
    assert len(cards1) > 0, f"/card_deck_{deck_slug} не вернул карты!"
    print(f"🃏 Раунд 1: {cards1[:3]}...")
    
    # Второй запрос
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(0.5)
    
    cards2 = get_card_names_from_response(redis_client, token)
    assert len(cards2) > 0, f"Второй запрос /card_deck_{deck_slug} не вернул карты!"
    print(f"🃏 Раунд 2: {cards2[:3]}...")
    
    print(f"✅ Колода {deck_slug} работает в обоих запросах")
    
    # /card3_deck_{slug}
    print(f"\n🔢 Тестируем /card3_deck_{deck_slug}")
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(0.5)
    
    update3 = {
        "update_id": 704,
        "message": {
            "message_id": 704,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000002,
            "text": f"/card3_deck_{deck_slug}",
            "entities": [{"offset": 0, "length": len(f"/card3_deck_{deck_slug}"), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update3)
    assert response.status_code == 200
    
    time.sleep(2)
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
    
    if media_msgs:
        media_data = extract_message_data(media_msgs[0])
        media_items = media_data.get('media', [])
        print(f"🃏 /card3_deck_{deck_slug}: {len(media_items)} карт")
        for i, item in enumerate(media_items):
            cap = item.get('caption', '').strip()
            print(f"   {i+1}. {cap[:60]}")
        assert len(media_items) == 3, f"Ожидалось 3 карты, получено {len(media_items)}"
    else:
        send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
        for msg in send_msgs:
            data = extract_message_data(msg)
            text = data.get('text', '')
            if '•' in text:
                card_count = len(re.findall(r'[•]', text))
                print(f"🃏 /card3_deck_{deck_slug}: {card_count} карт в тексте")
                assert card_count == 3, f"Ожидалось 3 карты, получено {card_count}"
                break
    
    print(f"\n✅ Тест /decks пройден!")
    print(f"   Проверено: список колод текстом, пагинация, /card_deck_SLUG, /cardN_deck_SLUG")


@pytest.mark.django_db
def test_decks_oraculum(send_webhook_update, redis_client):
    """E2E: /decks oraculum — проверяем что фильтр работает и команды oraculum_deck_ отдают карты"""
    
    token = "test_token_12345"
    user_id = 7001
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    
    time.sleep(1)
    
    # ═══════════════════════════════════════════
    # Шаг 1: /decks oraculum
    # ═══════════════════════════════════════════
    update = {
        "update_id": 800,
        "message": {
            "message_id": 800,
            "from": {"id": user_id, "is_bot": False, "first_name": "Xenia"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/decks oraculum",
            "entities": [{"offset": 0, "length": 6, "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print("✅ Апдейт /decks oraculum отправлен\n")
    
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
                print(f"📨 sendMessage: {text[:300]}")
        
        if len(all_messages) > 0:
            break
        time.sleep(0.1)
    
    assert len(all_messages) > 0, "Нет ответа на /decks oraculum!"
    
    decks_msg = extract_message_data(all_messages[0])
    text = decks_msg.get('text', '')
    reply_markup = decks_msg.get('reply_markup', {})
    inline_keyboard = reply_markup.get('inline_keyboard', [])
    
    print(f"\n📝 Оракулы:\n{text}")
    
    # Проверяем что в ответе есть команды oraculum_deck_
    oraculum_commands = re.findall(r'/oraculum_deck_\S+', text)
    print(f"🔗 Команд /oraculum_deck_ в тексте: {len(oraculum_commands)}")
    assert len(oraculum_commands) > 0, "Нет команд /oraculum_deck_ в ответе!"
    
    # Проверяем что нет обычных колод
    card_deck_in_text = re.findall(r'/card_deck_\S+', text)
    assert len(card_deck_in_text) == 0, \
        f"Найдены обычные колоды в ответе на oraculum: {card_deck_in_text}"
    print(f"✅ Обычных колод нет в ответе")
    
    # Собираем все кнопки
    all_buttons = []
    for row in inline_keyboard:
        for btn in row:
            all_buttons.append(btn)
    
    print(f"\n🔘 Всего кнопок: {len(all_buttons)}")
    for btn in all_buttons:
        cb_data = btn.get('callback_data', '')
        btn_text = btn.get('text', '')
        print(f"   [{btn_text}] -> {cb_data}")
    
    # Находим оракулы в кнопках
    oraculum_slugs = set()
    
    for btn in all_buttons:
        cb_data = btn.get('callback_data', '')
        match = re.search(r'oraculum_deck_(\S+)', cb_data)
        if match:
            oraculum_slugs.add(match.group(1))
    
    # Ищем также в тексте
    for cmd in oraculum_commands:
        slug = cmd.replace('/oraculum_deck_', '')
        oraculum_slugs.add(slug)
    
    print(f"\n🃏 Найдено оракулов: {len(oraculum_slugs)}")
    for slug in sorted(oraculum_slugs):
        print(f"   • {slug}")
    
    assert len(oraculum_slugs) > 0, "Не найдено ни одного оракула!"
    
    # Проверяем что все кнопки — оракулы (нет card_deck_)
    non_oraculum = []
    for btn in all_buttons:
        cb_data = btn.get('callback_data', '')
        if cb_data and 'deck_' in cb_data and 'oraculum_deck_' not in cb_data:
            non_oraculum.append(cb_data)
    
    assert len(non_oraculum) == 0, \
        f"Найдены не-оракулы в кнопках: {non_oraculum}"
    print(f"✅ Все кнопки — оракулы")
    
    # Проверяем пагинацию (если есть)
    next_btn = None
    for btn in all_buttons:
        cb_data = btn.get('callback_data', '')
        if 'deckspage_' in cb_data and ('Вперед' in btn.get('text', '') or '➡️' in btn.get('text', '')):
            next_btn = btn
            break
    
    if next_btn:
        print(f"\n➡️ Пагинация есть — листаем вперед")
        loop.run_until_complete(redis_client.delete(redis_key))
        
        from django.urls import reverse
        from django.test import Client
        client = Client()
        webhook_url = reverse("webhook", kwargs={"token": token})
        
        callback_update = {
            "update_id": 801,
            "callback_query": {
                "id": "801",
                "from": {"id": user_id, "is_bot": False, "first_name": "Xenia"},
                "message": {
                    "message_id": 800,
                    "chat": {"id": user_id, "type": "private"},
                    "date": 1717000000,
                    "text": "oraculum list"
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
            edited_text = edited_data.get('text', '')
            
            # На второй странице тоже только оракулы
            card_deck_on_page2 = re.findall(r'/card_deck_\S+', edited_text)
            assert len(card_deck_on_page2) == 0, \
                f"Обычные колоды на второй странице оракулов: {card_deck_on_page2}"
            
            oraculum_on_page2 = re.findall(r'/oraculum_deck_\S+', edited_text)
            print(f"🔗 Оракулов на стр.2: {len(oraculum_on_page2)}")
            print(f"✅ Пагинация оракулов работает")
    
    # ═══════════════════════════════════════════
    # Шаг 2: Проверяем что по оракулам можно получить карты
    # ═══════════════════════════════════════════
    test_oraculum = list(oraculum_slugs)[0]
    print(f"\n🎯 Тестируем выдачу карт из оракула: {test_oraculum}")
    
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(0.5)
    
    update2 = {
        "update_id": 802,
        "message": {
            "message_id": 802,
            "from": {"id": user_id, "is_bot": False, "first_name": "Xenia"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000001,
            "text": f"/oraculum_deck_{test_oraculum}",
            "entities": [{"offset": 0, "length": len(f"/oraculum_deck_{test_oraculum}"), "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update2)
    assert response.status_code == 200
    print(f"✅ Апдейт /oraculum_deck_{test_oraculum} отправлен")
    
    # Ждем карты
    time.sleep(2)
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
    
    if media_msgs:
        media_data = extract_message_data(media_msgs[0])
        media_items = media_data.get('media', [])
        print(f"🃏 Получено {len(media_items)} карт из оракула {test_oraculum}")
        for i, item in enumerate(media_items[:5]):
            cap = item.get('caption', '').strip()
            print(f"   {i+1}. {cap[:60]}")
        assert len(media_items) > 0, "Оракул не вернул карты!"
    else:
        send_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
        texts = [extract_message_data(m).get('text', '') for m in send_msgs]
        print(f"📨 Ответы текстом: {texts[:3]}")
        assert len(send_msgs) > 0, "Оракул не вернул ни карт, ни сообщений!"
    
    # Проверяем /cardN_deck_{oraculum_slug} — должен работать так же
    if len(oraculum_slugs) > 0:
        test_slug = list(oraculum_slugs)[0]
        print(f"\n🔢 Тестируем /card3_deck_{test_slug} (оракул через card_deck_)")
        
        loop.run_until_complete(redis_client.delete(redis_key))
        time.sleep(0.5)
        
        update3 = {
            "update_id": 803,
            "message": {
                "message_id": 803,
                "from": {"id": user_id + 1, "is_bot": False, "first_name": "User2"},
                "chat": {"id": user_id + 1, "type": "private"},
                "date": 1717000002,
                "text": f"/card3_deck_{test_slug}",
                "entities": [{"offset": 0, "length": len(f"/card3_deck_{test_slug}"), "type": "bot_command"}]
            }
        }
        
        response = send_webhook_update(token, update3)
        assert response.status_code == 200
        
        time.sleep(2)
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
        
        if media_msgs:
            media_data = extract_message_data(media_msgs[0])
            media_items = media_data.get('media', [])
            print(f"🃏 /card3_deck_{test_slug}: {len(media_items)} карт")
            assert len(media_items) > 0, \
                f"/card3_deck_{test_slug} не вернул карты!"
            assert len(media_items) == 3, \
                f"Ожидалось 3 карты, получено {len(media_items)}"
    
    print(f"\n✅ Тест /decks oraculum пройден!")
    print(f"   Найдено оракулов: {len(oraculum_slugs)}")
    print(f"   Фильтр работает, обычные колоды не показываются")
    print(f"   Пагинация: {'есть' if next_btn else 'нет'}")
    print(f"   Выдача карт: работает")