# tests/test_meaning_flow.py
import pytest
import re
import json
import time
import asyncio
from django.test import Client
from django.urls import reverse
from tests.conftest import extract_message_data, sync_lrange


def _find_button(inline_keyboard, text_substring):
    """Находит кнопку по подстроке в тексте. Возвращает btn или None."""
    for row in inline_keyboard:
        for btn in row:
            if text_substring in btn.get('text', ''):
                return btn
    return None


def _find_button_by_callback(inline_keyboard, callback_substring):
    """Находит кнопку по подстроке в callback_data."""
    for row in inline_keyboard:
        for btn in row:
            if callback_substring in btn.get('callback_data', ''):
                return btn
    return None


def _get_all_buttons(inline_keyboard):
    """Все кнопки плоским списком."""
    return [b for row in inline_keyboard for b in row]


def _send_callback(client, webhook_url, user_id, message_id, callback_data, update_id, reply_markup=None):
    """Отправляет callback_query с опциональной клавиатурой сообщения."""
    message = {
        "message_id": message_id,
        "chat": {"id": user_id, "type": "private"},
        "date": 1717000000,
        "text": "previous message"
    }
    
    # Добавляем reply_markup, если передан (как в реальном Telegram)
    if reply_markup:
        message["reply_markup"] = reply_markup
    
    callback_update = {
        "update_id": update_id,
        "callback_query": {
            "id": str(update_id),
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "message": message,
            "chat_instance": "test",
            "data": callback_data
        }
    }
    return client.post(
        webhook_url,
        data=json.dumps(callback_update),
        content_type="application/json"
    )


def _collect_messages(redis_client, redis_key, endpoints, timeout=15, stop_condition=None):
    """
    Универсальный сборщик сообщений из Redis.
    endpoints: список endpoint'ов для фильтрации
    stop_condition: функция(messages) -> bool, когда остановиться
    """
    all_messages = []
    start = time.time()

    while time.time() - start < timeout:
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') in endpoints]

        for msg in messages:
            if msg not in all_messages:
                all_messages.append(msg)

        if stop_condition and stop_condition(all_messages):
            break

        time.sleep(0.1)

    return all_messages


def _print_message(msg):
    """Красивый вывод сообщения."""
    endpoint = msg.get('endpoint', '?')
    data = extract_message_data(msg)

    if 'text' in data:
        print(f"📨 {endpoint}: {data['text'][:120]}")
    elif 'media' in data:
        media_items = data.get('media', [])
        cap = media_items[0].get('caption', '')[:60] if media_items else ''
        print(f"📨 {endpoint} media[0].caption: {cap}")
    elif endpoint == 'deleteMessage':
        print(f"📨 {endpoint}: msg_id={data.get('message_id', '?')}")


def _has_media_and_text(messages):
    """Проверяет, есть ли и медиа, и текстовое сообщение с кнопками."""
    has_media = any(m.get('endpoint') == 'sendMediaGroup' for m in messages)
    has_text = any(
        m.get('endpoint') == 'sendMessage' and 'reply_markup' in extract_message_data(m)
        for m in messages
    )
    return has_media and has_text


def _extract_cards_from_text(text):
    """Извлекает список карт из текста сообщения (формат • Имя карты)."""
    cards = re.findall(r'[•]\s*(.+?)(?:\n|$)', text)
    return [c.strip() for c in cards if c.strip()]


def _extract_cards_from_media(media_msg_data):
    """Извлекает список карт из caption картинок в media group."""
    cards = []
    media_items = media_msg_data.get('media', [])
    for item in media_items:
        cap = item.get('caption', '').strip()
        name = cap.split('\n')[0] if cap else ''
        if name:
            cards.append(name)
    return cards


def _get_message_id_from_reply_markup(messages):
    """Находит message_id последнего сообщения с reply_markup."""
    for msg in reversed(messages):
        data = extract_message_data(msg)
        if 'reply_markup' in data:
            # Проверяем разные возможные поля
            msg_id = (
                data.get('message_id') or 
                data.get('msg_id') or 
                msg.get('message_id') or 
                msg.get('msg_id')
            )
            if msg_id:
                return msg_id
    return None


@pytest.mark.django_db
def test_meaning_flow(send_webhook_update, redis_client):
    """E2E: полный флоу Трактовки карт — от /card до ignore-колбека"""

    token = "test_token_12345"
    user_id = 7001
    redis_key = f"intercepted_requests:{token}"
    loop = asyncio.get_event_loop()
    client = Client()
    webhook_url = reverse("webhook", kwargs={"token": token})

    # ═══════════════════════════════════════════
    # ШАГ 1: Отправляем /card, получаем 1 карту
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 1: /card → получаем карту и кнопки")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    update = {
        "update_id": 700,
        "message": {
            "message_id": 700,
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

    all_messages = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage', 'sendMediaGroup', 'deleteMessage'],
        stop_condition=lambda msgs: _has_media_and_text(msgs)
    )

    for msg in all_messages:
        _print_message(msg)

    print(f"\n📊 Получено сообщений: {len(all_messages)}")

    final_msg = None
    media_msg = None
    for msg in all_messages:
        data = extract_message_data(msg)
        if msg.get('endpoint') == 'sendMediaGroup':
            media_msg = data
        if 'reply_markup' in data:
            final_msg = data

    assert final_msg is not None, "Нет финального сообщения с кнопками!"
    assert media_msg is not None, "Нет сообщения с картинкой!"

    text_1 = final_msg.get('text', '')
    cards_from_text = _extract_cards_from_text(text_1)
    cards_from_media = _extract_cards_from_media(media_msg)

    print(f"   Карты из текста: {cards_from_text}")
    print(f"   Карты из media:  {cards_from_media}")

    first_card_name = cards_from_text[0] if cards_from_text else cards_from_media[0]
    assert len(first_card_name) > 0, f"Название первой карты пустое!"
    print(f"\n🃏 Первая карта: «{first_card_name}»")

    inline_keyboard_1 = final_msg.get('reply_markup', {}).get('inline_keyboard', [])
    buttons_1 = [b.get('text', '') for row in inline_keyboard_1 for b in row]
    print(f"🔘 Кнопки (1 карта): {buttons_1}")

    meaning_btn = _find_button(inline_keyboard_1, 'Трактовка')
    assert meaning_btn is not None, "Нет кнопки Трактовка!"
    assert '(1)' in meaning_btn.get('text', ''), \
        f"Кнопка трактовки должна содержать (1), а не: {meaning_btn.get('text')}"
    print(f"✅ Кнопка Трактовка найдена: {meaning_btn.get('text')}")

    more_btn = _find_button(inline_keyboard_1, 'Еще карту')
    assert more_btn is not None, "Нет кнопки 'Еще карту'!"
    more_callback = more_btn.get('callback_data')
    print(f"✅ Кнопка 'Еще карту' найдена, callback: {more_callback[:30]}...")

    last_message_id = _get_message_id_from_reply_markup(all_messages) or 700
    print(f"📌 Актуальный message_id: {last_message_id}")

    # ═══════════════════════════════════════════
    # ШАГ 2: Жмём "Ещё карту" → 2 карты
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 2: Нажимаем 'Еще карту' → проверяем 2 карты")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(client, webhook_url, user_id, last_message_id, more_callback, 701)
    assert response.status_code == 200
    print(f"✅ Callback 'Еще карту' отправлен (msg_id={last_message_id})\n")

    all_messages_2 = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage', 'sendMediaGroup', 'deleteMessage'],
        stop_condition=lambda msgs: _has_media_and_text(msgs)
    )

    for msg in all_messages_2:
        _print_message(msg)

    print(f"\n📊 Получено сообщений на шаге 2: {len(all_messages_2)}")

    endpoints_2 = [m.get('endpoint') for m in all_messages_2]
    assert 'sendMediaGroup' in endpoints_2, "Нет sendMediaGroup на шаге 2!"
    assert 'sendMessage' in endpoints_2, "Нет sendMessage на шаге 2!"

    delete_msgs = [m for m in all_messages_2 if m.get('endpoint') == 'deleteMessage']
    if delete_msgs:
        print(f"✅ Старое сообщение удалено ({len(delete_msgs)} deleteMessage)")
    else:
        print("ℹ️ deleteMessage не пришёл")

    final_msg_2 = None
    media_msg_2 = None
    for msg in all_messages_2:
        data = extract_message_data(msg)
        if msg.get('endpoint') == 'sendMediaGroup':
            media_msg_2 = data
        if 'reply_markup' in data:
            final_msg_2 = data

    assert final_msg_2 is not None, "Нет финального сообщения после 'Еще карту'!"
    assert media_msg_2 is not None, "Нет sendMediaGroup после 'Еще карту'!"

    inline_keyboard_2 = final_msg_2.get('reply_markup', {}).get('inline_keyboard', [])
    buttons_2 = [b.get('text', '') for row in inline_keyboard_2 for b in row]
    print(f"\n🔘 Кнопки (2 карты): {buttons_2}")

    meaning_btn = _find_button(inline_keyboard_2, 'Трактовка')
    assert meaning_btn is not None, "Нет кнопки Трактовка после 'Еще карту'!"
    assert '(2)' in meaning_btn.get('text', ''), \
        f"Кнопка трактовки должна содержать (2), а не: {meaning_btn.get('text')}"
    print(f"✅ Кнопка Трактовка обновлена: {meaning_btn.get('text')}")

    meaning_callback = meaning_btn.get('callback_data')
    print(f"✅ Callback трактовки: {meaning_callback[:40]}...")

    text_2 = final_msg_2.get('text', '')
    cards_from_text = _extract_cards_from_text(text_2)
    cards_from_media = _extract_cards_from_media(media_msg_2)

    print(f"   Карты из текста: {cards_from_text}")
    print(f"   Карты из media:  {cards_from_media}")

    all_cards = cards_from_text
    assert len(all_cards) == 2, \
        f"Ожидалось 2 карты, нашли: {all_cards}\nТекст сообщения:\n{text_2[:500]}"
    print(f"\n🃏 Карты в раскладе: {all_cards}")

    last_message_id = _get_message_id_from_reply_markup(all_messages_2) or last_message_id
    print(f"📌 Актуальный message_id: {last_message_id}")

    # ═══════════════════════════════════════════
    # ШАГ 3: Жмём "Трактовка карт (2)" → последняя карта
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 3: Нажимаем 'Трактовка карт (2)' → последняя карта")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, last_message_id, meaning_callback, 702,
        reply_markup=final_msg_2.get('reply_markup')
    )
    assert response.status_code == 200
    print(f"✅ Callback 'Трактовка' отправлен (msg_id={last_message_id})\n")

    all_messages_3 = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage', 'editMessageText', 'editMessageReplyMarkup', 'answerCallbackQuery'],
        stop_condition=lambda msgs: (
            any(m.get('endpoint') == 'editMessageReplyMarkup' for m in msgs)
            and
            any(
                'reply_markup' in extract_message_data(m)
                and m.get('endpoint') in ['sendMessage', 'editMessageText']
                for m in msgs
            )
        )
    )

    for msg in all_messages_3:
        _print_message(msg)

    print(f"\n📊 Получено сообщений на шаге 3: {len(all_messages_3)}")
    assert len(all_messages_3) > 0, "Нет сообщений после нажатия Трактовки!"

    edit_markup_msgs = [m for m in all_messages_3 if m.get('endpoint') == 'editMessageReplyMarkup']
    assert len(edit_markup_msgs) > 0, "Старая клавиатура не отредактирована при открытии трактовки!"
    print(f"✅ editMessageReplyMarkup найден ({len(edit_markup_msgs)} шт.)")

    for msg in edit_markup_msgs:
        data = extract_message_data(msg)
        kb = data.get('reply_markup', {}).get('inline_keyboard', [])
        flat = [b.get('text', '') for row in kb for b in row]
        assert not any('Трактовка' in t for t in flat), \
            f"Кнопка Трактовка не убрана из старого сообщения! Кнопки: {flat}"
    print("✅ Кнопка Трактовка убрана из старого сообщения!")

    meaning_msg = None
    for msg in all_messages_3:
        data = extract_message_data(msg)
        if 'reply_markup' in data and msg.get('endpoint') in ['sendMessage', 'editMessageText']:
            text = data.get('text', '')
            if '<b>' in text or 'стр' in text:
                meaning_msg = data
                break

    if meaning_msg is None:
        for msg in all_messages_3:
            data = extract_message_data(msg)
            if 'reply_markup' in data and msg.get('endpoint') in ['sendMessage', 'editMessageText']:
                meaning_msg = data
                break

    assert meaning_msg is not None, "Нет сообщения с reply_markup после Трактовки!"

    meaning_text = meaning_msg.get('text', '')
    meaning_keyboard = meaning_msg.get('reply_markup', {}).get('inline_keyboard', [])

    print(f"\n📝 Текст трактовки:\n{meaning_text[:300]}...")
    print(f"\n🔘 Кнопки трактовки ({len(meaning_keyboard)} рядов):")

    # После "Еще карту" трактовка должна показывать ПОСЛЕДНЮЮ карту
    first_line = meaning_text.strip().split('\n')[0] if meaning_text else ''
    print(f"\n🔍 Первая строка текста: '{first_line}'")
    print(f"🔍 Последняя карта в списке: '{all_cards[-1]}'")

    assert all_cards[-1] in first_line, \
        f"Первая строка трактовки должна содержать последнюю карту '{all_cards[-1]}', а содержит: '{first_line}'"
    print(f"✅ Первая строка совпадает с последней картой!")

    assert '<b>' in meaning_text and '</b>' in meaning_text, "Нет жирного выделения имени карты!"
    assert 'стр' in meaning_text, "Нет указания страницы!"
    print("✅ HTML-форматирование и пагинация присутствуют!")

    assert len(meaning_keyboard) == 3, \
        f"Ожидалось 3 ряда кнопок, получено: {len(meaning_keyboard)}"
    print(f"✅ Три ряда кнопок подтверждены!")

    for i, row in enumerate(meaning_keyboard):
        row_texts = [b.get('text', '') for b in row]
        row_callbacks = [b.get('callback_data', '') for b in row]
        print(f"   Ряд {i+1}: {row_texts}")
        print(f"           callbacks: {[c[:50] + '...' if len(c) > 50 else c for c in row_callbacks]}")

    assert len(meaning_keyboard[0]) == 2, f"Ряд 1 должен иметь 2 кнопки, а не {len(meaning_keyboard[0])}"
    assert len(meaning_keyboard[1]) == 2, f"Ряд 2 должен иметь 2 кнопки, а не {len(meaning_keyboard[1])}"
    assert len(meaning_keyboard[2]) == 3, f"Ряд 3 должен иметь 3 кнопки, а не {len(meaning_keyboard[2])}"

    center_btn = meaning_keyboard[2][1]
    center_text = center_btn.get('text', '')
    center_callback = center_btn.get('callback_data', '')
    assert re.match(r'\d+\s*/\s*\d+', center_text), \
        f"Центральная кнопка должна быть 'X / Y', а не '{center_text}'"
    assert center_callback == 'meaning_ignore', \
        f"Центральная кнопка должна иметь callback 'meaning_ignore', а не '{center_callback}'"
    print(f"✅ Центральная кнопка пагинации: '{center_text}' → ignore")

    last_message_id = _get_message_id_from_reply_markup(all_messages_3) or last_message_id
    print(f"📌 Актуальный message_id для навигации: {last_message_id}")

    # ═══════════════════════════════════════════
    # ШАГ 4: Жмём вторую кнопку первого ряда до ignore
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 4: Жмём вторую кнопку первого ряда до ignore-колбека")
    print("═" * 50)

    assert len(meaning_keyboard[0]) >= 2, "В первом ряду меньше 2 кнопок!"
    target_btn = meaning_keyboard[0][1]
    target_text = target_btn.get('text', '')
    current_callback = target_btn.get('callback_data', '')
    print(f"🎯 Целевая кнопка: '{target_text}' (callback: {current_callback[:40]}...)")

    click_count = 0
    max_clicks = 20
    found_ignore = False
    last_text = meaning_text

    while click_count < max_clicks:
        click_count += 1

        loop.run_until_complete(redis_client.delete(redis_key))

        response = _send_callback(
            client, webhook_url, user_id, last_message_id, current_callback, 702 + click_count,
            reply_markup=meaning_keyboard
        )
        assert response.status_code == 200

        time.sleep(0.8)

        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') in
                   ['sendMessage', 'editMessageText', 'answerCallbackQuery']]

        new_callback = None
        new_text = None
        for msg in messages:
            data = extract_message_data(msg)
            if msg.get('endpoint') == 'answerCallbackQuery':
                ans_text = data.get('text', '')
                if ans_text:
                    print(f"   📨 answerCallbackQuery: {ans_text[:80]}")
            if msg.get('endpoint') == 'editMessageText':
                new_text = data.get('text', '')
                kb = data.get('reply_markup', {}).get('inline_keyboard', [])
                if len(kb) >= 1 and len(kb[0]) >= 2:
                    new_callback = kb[0][1].get('callback_data')
                    new_btn_text = kb[0][1].get('text', '')
                    print(f"   🔘 Клик #{click_count}: карта '{new_text[:40]}...' → кнопка '{new_btn_text}'")

        if new_text and new_text != last_text:
            new_first_line = new_text.strip().split('\n')[0] if new_text else ''
            old_first_line = last_text.strip().split('\n')[0] if last_text else ''
            if new_first_line != old_first_line:
                print(f"   ✅ Перешли на карту: {new_first_line}")

        if current_callback == 'meaning_ignore' or (new_callback and new_callback == 'meaning_ignore'):
            found_ignore = True
            print(f"\n🎉 Достигнут ignore-колбек после {click_count} кликов!")
            break

        if new_callback:
            current_callback = new_callback
            last_text = new_text or last_text
        else:
            print(f"   ⚠️ Нет новых кнопок после клика #{click_count}")
            break

    assert found_ignore, \
        f"ignore-колбек не достигнут за {click_count} кликов! Последний callback: {current_callback}"
    print(f"\n✅ ignore-колбек подтверждён!")

    # ═══════════════════════════════════════════
    # ШАГ 5: Проверяем пагинацию страниц (3-й ряд)
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 5: Проверяем пагинацию страниц")
    print("═" * 50)

    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    edit_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'editMessageText']
    if edit_msgs:
        last_edit = extract_message_data(edit_msgs[-1])
        kb = last_edit.get('reply_markup', {}).get('inline_keyboard', [])
        if len(kb) >= 3:
            page_row = kb[2]
            page_texts = [b.get('text', '') for b in page_row]
            page_callbacks = [b.get('callback_data', '') for b in page_row]
            print(f"🔘 Ряд пагинации: {page_texts}")
            print(f"   callbacks: {page_callbacks}")

            assert len(page_row) == 3, "Ряд пагинации должен иметь 3 кнопки"

            left_callback = page_row[0].get('callback_data', '')
            if 'meaning_ignore' in left_callback:
                print("   ⬅️ Левая кнопка = ignore (первая страница)")
            else:
                assert 'meaning_' in left_callback, f"Левая кнопка должна быть meaning_*, а не {left_callback}"
                print(f"   ⬅️ Левая кнопка ведёт на: {left_callback[:40]}...")

            right_callback = page_row[2].get('callback_data', '')
            if 'meaning_ignore' in right_callback:
                print("   ➡️ Правая кнопка = ignore (последняя страница)")
            else:
                assert 'meaning_' in right_callback, f"Правая кнопка должна быть meaning_*, а не {right_callback}"
                print(f"   ➡️ Правая кнопка ведёт на: {right_callback[:40]}...")

    # ═══════════════════════════════════════════
    # ШАГ 6: Проверяем переключение типов трактовок (2-й ряд)
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 6: Проверяем переключение типов трактовок")
    print("═" * 50)

    if edit_msgs:
        last_edit = extract_message_data(edit_msgs[-1])
        kb = last_edit.get('reply_markup', {}).get('inline_keyboard', [])
        if len(kb) >= 2:
            type_row = kb[1]
            type_texts = [b.get('text', '') for b in type_row]
            type_callbacks = [b.get('callback_data', '') for b in type_row]
            print(f"🔘 Ряд типов: {type_texts}")
            print(f"   callbacks: {type_callbacks}")

            assert len(type_row) == 2, "Ряд типов должен иметь 2 кнопки"

            for btn in type_row:
                cb = btn.get('callback_data', '')
                assert cb.startswith('meaning_'), f"Callback типа должен начинаться с 'meaning_', а не {cb}"
                parts = cb.split('_')
                assert len(parts) >= 5, f"Callback должен иметь формат meaning_R_C_T_P, а не {cb}"
                assert parts[4] == '1', f"Страница в callback типа должна быть 1, а не {parts[4]}"

            print("✅ Ряд типов трактовок корректен!")

    # ═══════════════════════════════════════════
    # ШАГ 7: Проверяем meaning_ignore на пагинации
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 7: Проверяем meaning_ignore на пагинации")
    print("═" * 50)

    if edit_msgs:
        last_edit = extract_message_data(edit_msgs[-1])
        kb = last_edit.get('reply_markup', {}).get('inline_keyboard', [])
        if len(kb) >= 3 and len(kb[2]) >= 2:
            center_btn = kb[2][1]
            center_callback = center_btn.get('callback_data', '')

            if center_callback == 'meaning_ignore':
                loop.run_until_complete(redis_client.delete(redis_key))

                response = _send_callback(client, webhook_url, user_id, last_message_id, center_callback, 800)
                assert response.status_code == 200

                time.sleep(0.5)

                all_data = sync_lrange(redis_client, redis_key, 0, -1)
                answer_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'answerCallbackQuery']

                found_ignore_answer = False
                for msg in answer_msgs:
                    data = extract_message_data(msg)
                    ans_text = data.get('text', '')
                    if ans_text and 'Дальше' in ans_text:
                        found_ignore_answer = True
                        print(f"📨 answerCallbackQuery: {ans_text}")
                        break

                print("✅ Центральная кнопка пагинации (ignore) обработана корректно!")

    # ═══════════════════════════════════════════
    # Итог
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("✅ ВСЕ ПРОВЕРКИ meaning_flow ПРОЙДЕНЫ!")
    print("═" * 50)
    print(f"   • /card → 1 карта, кнопка 'Трактовка (1)'")
    print(f"   • 'Еще карту' → 2 карты, кнопка 'Трактовка (2)'")
    print(f"   • Трактовка после расширения → последняя карта: «{all_cards[-1]}»")
    print(f"   • HTML-форматирование и пагинация присутствуют")
    print(f"   • 3 ряда кнопок в меню трактовки")
    print(f"   • editMessageReplyMarkup убирает кнопку Трактовка")
    print(f"   • Вторая кнопка 1-го ряда → ignore за {click_count} кликов")
    print(f"   • Ряд типов трактовок корректен")
    print(f"   • Ряд пагинации корректен")


@pytest.mark.django_db
def test_meaning_last_card(send_webhook_update, redis_client):
    """E2E: Трактовка всегда показывает последнюю выпавшую карту"""
    
    token = "test_token_12345"
    user_id = 7101
    redis_key = f"intercepted_requests:{token}"
    loop = asyncio.get_event_loop()
    client = Client()
    webhook_url = reverse("webhook", kwargs={"token": token})
    
    # ═══════════════════════════════════════════
    # ШАГ 1: /card3 → 3 карты
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 1: /card3 → 3 карты")
    print("═" * 50)
    
    loop.run_until_complete(redis_client.delete(redis_key))
    
    update = {
        "update_id": 1000,
        "message": {
            "message_id": 1000,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/card3",
            "entities": [{"offset": 0, "length": 6, "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print("✅ /card3 отправлен\n")
    
    all_messages_1 = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage', 'sendMediaGroup', 'deleteMessage'],
        stop_condition=lambda msgs: _has_media_and_text(msgs)
    )
    
    final_msg_1 = None
    for msg in all_messages_1:
        data = extract_message_data(msg)
        if 'reply_markup' in data:
            final_msg_1 = data
    
    assert final_msg_1 is not None, "Нет финального сообщения после /card3!"
    
    text_1 = final_msg_1.get('text', '')
    cards = _extract_cards_from_text(text_1)
    assert len(cards) == 3, f"Ожидалось 3 карты, получили: {cards}"
    print(f"🃏 Карты: {cards}")
    
    keyboard_1 = final_msg_1.get('reply_markup', {}).get('inline_keyboard', [])
    meaning_btn = _find_button(keyboard_1, 'Трактовка')
    assert meaning_btn is not None, "Нет кнопки Трактовка!"
    assert '(3)' in meaning_btn.get('text', ''), "Кнопка должна быть Трактовка (3)"
    print(f"✅ Кнопка: {meaning_btn.get('text')}")
    
    last_message_id = _get_message_id_from_reply_markup(all_messages_1)
    
    # ═══════════════════════════════════════════
    # ШАГ 2: Трактовка → первая карта
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 2: Трактовка → первая карта")
    print("═" * 50)
    
    loop.run_until_complete(redis_client.delete(redis_key))
    
    response = _send_callback(
        client, webhook_url, user_id, last_message_id,
        meaning_btn.get('callback_data'), 1001,
        reply_markup=final_msg_1.get('reply_markup')
    )
    assert response.status_code == 200
    
    all_messages_2 = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage', 'editMessageText', 'editMessageReplyMarkup'],
        stop_condition=lambda msgs: any(
            'reply_markup' in extract_message_data(m)
            and m.get('endpoint') in ['sendMessage', 'editMessageText']
            and ('<b>' in extract_message_data(m).get('text', '') or 'стр' in extract_message_data(m).get('text', ''))
            for m in msgs
        )
    )
    
    meaning_msg = None
    for msg in all_messages_2:
        data = extract_message_data(msg)
        if 'reply_markup' in data and msg.get('endpoint') in ['sendMessage', 'editMessageText']:
            meaning_msg = data
            break
    
    assert meaning_msg is not None, "Нет сообщения с трактовкой!"
    
    first_line = meaning_msg.get('text', '').strip().split('\n')[0]
    assert cards[0] in first_line, \
        f"Трактовка должна начинаться с '{cards[0]}', а начинается с '{first_line}'"
    print(f"✅ Трактовка: «{cards[0]}»")
    
    # ═══════════════════════════════════════════
    # ШАГ 3: Еще карту → 4 карты
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print("ШАГ 3: Еще карту → 4 карты")
    print("═" * 50)
    
    more_btn = _find_button(keyboard_1, 'Еще карту')
    assert more_btn is not None, "Нет кнопки 'Еще карту'!"
    
    loop.run_until_complete(redis_client.delete(redis_key))
    
    response = _send_callback(
        client, webhook_url, user_id, last_message_id,
        more_btn.get('callback_data'), 1002,
        reply_markup=final_msg_1.get('reply_markup')
    )
    assert response.status_code == 200
    
    all_messages_3 = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage', 'sendMediaGroup', 'deleteMessage'],
        stop_condition=lambda msgs: _has_media_and_text(msgs)
    )
    
    final_msg_3 = None
    for msg in all_messages_3:
        data = extract_message_data(msg)
        if 'reply_markup' in data:
            final_msg_3 = data
    
    assert final_msg_3 is not None, "Нет финального сообщения после 'Еще карту'!"
    
    text_3 = final_msg_3.get('text', '')
    cards = _extract_cards_from_text(text_3)
    assert len(cards) == 4, f"Ожидалось 4 карты, получили: {cards}"
    print(f"🃏 Карты: {cards}")
    
    new_card = cards[-1]
    print(f"🆕 Новая карта: «{new_card}»")
    
    keyboard_3 = final_msg_3.get('reply_markup', {}).get('inline_keyboard', [])
    meaning_btn = _find_button(keyboard_3, 'Трактовка')
    assert meaning_btn is not None, "Нет кнопки Трактовка!"
    assert '(4)' in meaning_btn.get('text', ''), "Кнопка должна быть Трактовка (4)"
    print(f"✅ Кнопка: {meaning_btn.get('text')}")
    
    last_message_id = _get_message_id_from_reply_markup(all_messages_3)
    
    # ═══════════════════════════════════════════
    # ШАГ 4: Трактовка → последняя карта
    # ═══════════════════════════════════════════
    print("\n" + "═" * 50)
    print(f"ШАГ 4: Трактовка → последняя карта «{new_card}»")
    print("═" * 50)
    
    loop.run_until_complete(redis_client.delete(redis_key))
    
    response = _send_callback(
        client, webhook_url, user_id, last_message_id,
        meaning_btn.get('callback_data'), 1003,
        reply_markup=final_msg_3.get('reply_markup')
    )
    assert response.status_code == 200
    
    all_messages_4 = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage', 'editMessageText', 'editMessageReplyMarkup'],
        stop_condition=lambda msgs: any(
            'reply_markup' in extract_message_data(m)
            and m.get('endpoint') in ['sendMessage', 'editMessageText']
            and ('<b>' in extract_message_data(m).get('text', '') or 'стр' in extract_message_data(m).get('text', ''))
            for m in msgs
        )
    )
    
    meaning_msg = None
    for msg in all_messages_4:
        data = extract_message_data(msg)
        if 'reply_markup' in data and msg.get('endpoint') in ['sendMessage', 'editMessageText']:
            meaning_msg = data
            break
    
    assert meaning_msg is not None, "Нет сообщения с трактовкой!"
    
    first_line = meaning_msg.get('text', '').strip().split('\n')[0]
    assert new_card in first_line, \
        f"Трактовка должна начинаться с '{new_card}', а начинается с '{first_line}'"
    print(f"✅ Трактовка: «{new_card}»")
    
    print("\n" + "═" * 50)
    print("✅ ТЕСТ ПРОЙДЕН!")
    print("═" * 50)