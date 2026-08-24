# tests/test_onehand.py
import pytest
import re
import json
import time
import asyncio
from django.test import Client
from django.urls import reverse
from tests.conftest import extract_message_data, sync_lrange, _collect_messages, _find_button, _print_message, _get_message_id_from_reply_markup

# ═══════════════════════════════════════════════════════════════
# ХЕЛПЕРЫ
# ═══════════════════════════════════════════════════════════════

def _send_callback(client, webhook_url, user_id, message_id, callback_data, update_id, reply_markup=None, text=None):
    """Отправляет callback_query."""
    message = {
        "message_id": message_id,
        "chat": {"id": user_id, "type": "private"},
        "date": 1717000000,
        "text": text or "previous message"
    }
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


def _get_message_text_from_reply_markup(messages):
    """Находит text последнего сообщения с reply_markup."""
    for msg in reversed(messages):
        data = extract_message_data(msg)
        if 'reply_markup' in data:
            return data.get('text', '')
    return None


def _extract_preview_command(text):
    """Извлекает команду из предпросмотра: '📋 <b>Предпросмотр:</b> <code>/card3_deck_waite_flip</code>'."""
    match = re.search(r'Предпросмотр:</b>\s*<code>([^<]+)</code>', text)
    if match:
        return match.group(1).strip()
    return None


# ═══════════════════════════════════════════════════════════════
# ТЕСТЫ
# ═══════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_onehand_main_menu(send_webhook_update, redis_client):
    """E2E: /onehand открывает главное меню с 6 кнопками"""

    token = "test_token_12345"
    user_id = 8001
    redis_key = f"intercepted_requests:{token}"
    loop = asyncio.get_event_loop()

    print("\n" + "═" * 50)
    print("ШАГ 1: /onehand → главное меню")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    update = {
        "update_id": 800,
        "message": {
            "message_id": 800,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/onehand",
            "entities": [{"offset": 0, "length": 8, "type": "bot_command"}]
        }
    }

    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print("✅ /onehand отправлен\n")

    all_messages = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage', 'sendMediaGroup', 'deleteMessage'],
    )

    for msg in all_messages:
        _print_message(msg)

    assert len(all_messages) >= 1, "Нет сообщений!"

    final_msg = extract_message_data(all_messages[0])
    text = final_msg.get('text', '')
    assert "Выберите тип гадания" in text, f"Ожидался текст меню, получено: {text}"
    print(f"✅ Текст меню: {text[:60]}")

    keyboard = final_msg.get('reply_markup', {}).get('inline_keyboard', [])
    assert len(keyboard) == 3, f"Ожидалось 3 ряда кнопок, получено: {len(keyboard)}"

    all_buttons = [b.get('text', '') for row in keyboard for b in row]
    print(f"🔘 Кнопки: {all_buttons}")

    expected_buttons = ['Одна карта', 'Таро (стикер)', 'Таро', 'Оракул', 'Руны', 'Холст']
    for expected in expected_buttons:
        assert any(expected in btn for btn in all_buttons), f"Нет кнопки '{expected}'!"

    print("✅ Все 6 кнопок присутствуют!")

    for row in keyboard:
        for btn in row:
            cb = btn.get('callback_data', '')
            assert cb.startswith('oh_'), f"Callback должен начинаться с 'oh_', а не: {cb}"

    print("✅ Все callback'ы начинаются с 'oh_'")


@pytest.mark.django_db
def test_onehand_quick_one_card(send_webhook_update, redis_client):
    """E2E: Быстрая кнопка 'Одна карта' → /one"""

    token = "test_token_12345"
    user_id = 8002
    redis_key = f"intercepted_requests:{token}"
    loop = asyncio.get_event_loop()
    client = Client()
    webhook_url = reverse("webhook", kwargs={"token": token})

    print("\n" + "═" * 50)
    print("ШАГ 1: /onehand → 'Одна карта'")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    update = {
        "update_id": 810,
        "message": {
            "message_id": 810,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/onehand",
            "entities": [{"offset": 0, "length": 8, "type": "bot_command"}]
        }
    }

    response = send_webhook_update(token, update)
    assert response.status_code == 200

    all_messages = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage'],
    )

    final_msg = extract_message_data(all_messages[0])
    keyboard = final_msg.get('reply_markup', {}).get('inline_keyboard', [])

    one_card_btn = _find_button(keyboard, 'Одна карта')
    assert one_card_btn is not None, "Нет кнопки 'Одна карта'!"

    callback = one_card_btn.get('callback_data')
    print(f"✅ Кнопка 'Одна карта' найдена, callback: {callback}")
    assert 'oh_cmd_one' == callback, f"Ожидался callback 'oh_cmd_one', получен: {callback}"

    # Жмём кнопку
    print("\n" + "═" * 50)
    print("ШАГ 2: Жмём 'Одна карта' → проверяем /one")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    msg_id = _get_message_id_from_reply_markup(all_messages) or 810
    msg_text = _get_message_text_from_reply_markup(all_messages) or "previous message"

    response = _send_callback(
        client, webhook_url, user_id, msg_id, callback, 811,
        reply_markup=final_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200
    print(f"✅ Callback отправлен (msg_id={msg_id})\n")

    all_messages_2 = _collect_messages(
        redis_client, redis_key,
        endpoints=['deleteMessage', 'sendMessage'],
        stop_condition=lambda msgs: (
            any(m.get('endpoint') == 'deleteMessage' for m in msgs) and
            any(m.get('endpoint') == 'sendMessage' for m in msgs)
        )
    )

    for msg in all_messages_2:
        _print_message(msg)

    got_delete = any(m.get('endpoint') == 'deleteMessage' for m in all_messages_2)
    got_send = any(m.get('endpoint') == 'sendMessage' for m in all_messages_2)

    assert got_delete, "Старое сообщение не удалено!"
    print(f"✅ Старое сообщение удалено")

    assert got_send, "Нет нового сообщения!"
    send_msgs = [m for m in all_messages_2 if m.get('endpoint') == 'sendMessage']
    msg_data = extract_message_data(send_msgs[0])
    text = msg_data.get('text', '')
    assert '/one' in text, f"Ожидалась команда /one в тексте, получено: {text}"
    print(f"✅ Команда /one в тексте: {text[:80]}")

    reply_keyboard = msg_data.get('reply_markup', {}).get('keyboard', [])
    assert len(reply_keyboard) == 1, f"Ожидалась 1 кнопка ReplyKeyboard, получено рядов: {len(reply_keyboard)}"
    # Кнопка ReplyKeyboard — это словарь {'text': '/one'}, а не строка
    btn_text = reply_keyboard[0][0].get('text') if isinstance(reply_keyboard[0][0], dict) else reply_keyboard[0][0]
    assert btn_text == '/one', f"Ожидалась кнопка '/one', получена: {reply_keyboard[0][0]}"
    print(f"✅ ReplyKeyboard с кнопкой '/one' присутствует!")


@pytest.mark.django_db
def test_onehand_tarot_config_flow(send_webhook_update, redis_client):
    """E2E: Флоу Таро — выбор параметров и формирование команды"""

    token = "test_token_12345"
    user_id = 8003
    redis_key = f"intercepted_requests:{token}"
    loop = asyncio.get_event_loop()
    client = Client()
    webhook_url = reverse("webhook", kwargs={"token": token})

    print("\n" + "═" * 50)
    print("ШАГ 1: /onehand → 'Таро'")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    update = {
        "update_id": 820,
        "message": {
            "message_id": 820,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/onehand",
            "entities": [{"offset": 0, "length": 8, "type": "bot_command"}]
        }
    }

    response = send_webhook_update(token, update)
    assert response.status_code == 200

    all_messages = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage'],
    )

    final_msg = extract_message_data(all_messages[0])
    keyboard = final_msg.get('reply_markup', {}).get('inline_keyboard', [])

    # Ищем кнопку 'Таро' точным совпадением, а не подстрокой
    # чтобы не поймать 'Таро (стикер)'
    taro_btn = None
    for row in keyboard:
        for btn in row:
            if btn.get('text', '') == '🔮 Таро':
                taro_btn = btn
                break
        if taro_btn:
            break

    assert taro_btn is not None, "Нет кнопки 'Таро'!"
    taro_callback = taro_btn.get('callback_data')
    print(f"✅ Кнопка 'Таро' найдена: {taro_callback}")
    assert 'oh_type_card' == taro_callback

    # Жмём "Таро"
    print("\n" + "═" * 50)
    print("ШАГ 2: Жмём 'Таро' → конфигурация")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    msg_id = _get_message_id_from_reply_markup(all_messages) or 820
    msg_text = _get_message_text_from_reply_markup(all_messages) or "previous message"

    response = _send_callback(
        client, webhook_url, user_id, msg_id, taro_callback, 821,
        reply_markup=final_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_2 = _collect_messages(
        redis_client, redis_key,
        endpoints=['editMessageText'],
    )

    for msg in all_messages_2:
        _print_message(msg)

    assert len(all_messages_2) > 0, "Нет editMessageText после выбора Таро!"

    config_msg = extract_message_data(all_messages_2[0])
    text = config_msg.get('text', '')

    assert "Настройте расклад" in text, f"Ожидался текст конфигурации, получено: {text}"
    print(f"✅ Конфигурация открыта")

    assert "Предпросмотр" in text, f"Нет предпросмотра команды в тексте: {text}"
    assert "<code>" in text, f"Нет HTML-тега <code> в предпросмотре"
    print(f"✅ Предпросмотр команды присутствует")

    # Проверяем предпросмотр по умолчанию — /card
    preview = _extract_preview_command(text)
    assert preview == '/card', f"Предпросмотр по умолчанию должен быть /card, а не: {preview}"
    print(f"✅ Предпросмотр по умолчанию: {preview}")

    config_kb = config_msg.get('reply_markup', {}).get('inline_keyboard', [])
    assert len(config_kb) >= 4, f"Ожидалось минимум 4 ряда кнопок, получено: {len(config_kb)}"

    row1 = [b.get('text', '') for b in config_kb[0]]
    assert any('1' in t for t in row1), "Нет кнопки '1'"
    assert any('3' in t for t in row1), "Нет кнопки '3'"
    print(f"✅ Ряд количества: {row1}")

    row2 = [b.get('text', '') for b in config_kb[1]]
    assert any('Случайно' in t for t in row2), "Нет кнопки 'Случайно'"
    assert any('Райдер-Уэйт' in t for t in row2), "Нет кнопки 'Райдер-Уэйт'"
    print(f"✅ Ряд колод: {row2}")

    row3 = [b.get('text', '') for b in config_kb[2]]
    assert any('Старшие' in t for t in row3), "Нет кнопки 'Только Старшие'"
    assert any('Все' in t for t in row3), "Нет кнопки 'Все арканы'"
    print(f"✅ Ряд аркан: {row3}")

    row4 = [b.get('text', '') for b in config_kb[3]]
    assert any('перевернутыми' in t for t in row4), "Нет кнопки 'С перевернутыми'"
    assert any('Прямые' in t for t in row4), "Нет кнопки 'Прямые'"
    print(f"✅ Ряд перевернутых: {row4}")

    last_row = [b.get('text', '') for b in config_kb[-1]]
    assert any('Получить' in t for t in last_row), "Нет кнопки 'Получить команду'"
    print(f"✅ Последний ряд: {last_row}")

    # ШАГ 3: Меняем количество на 3
    print("\n" + "═" * 50)
    print("ШАГ 3: Меняем количество на 3")
    print("═" * 50)

    count_3_btn = _find_button(config_kb, '3')
    assert count_3_btn is not None, "Нет кнопки '3'!"
    count_callback = count_3_btn.get('callback_data')

    msg_id = _get_message_id_from_reply_markup(all_messages_2) or msg_id
    msg_text = _get_message_text_from_reply_markup(all_messages_2) or text

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, msg_id, count_callback, 822,
        reply_markup=config_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_3 = _collect_messages(
        redis_client, redis_key,
        endpoints=['editMessageText'],
    )

    for msg in all_messages_3:
        _print_message(msg)

    updated_msg = extract_message_data(all_messages_3[0])
    updated_text = updated_msg.get('text', '')

    preview = _extract_preview_command(updated_text)
    assert preview == '/card3', f"Предпросмотр должен быть /card3, а не: {preview}"
    print(f"✅ Предпросмотр обновлён: {preview}")

    # ШАГ 4: Выбираем Райдер-Уэйт
    print("\n" + "═" * 50)
    print("ШАГ 4: Выбираем 'Райдер-Уэйт'")
    print("═" * 50)

    updated_kb = updated_msg.get('reply_markup', {}).get('inline_keyboard', [])
    rider_btn = _find_button(updated_kb, 'Райдер-Уэйт')
    assert rider_btn is not None, "Нет кнопки 'Райдер-Уэйт'!"
    rider_callback = rider_btn.get('callback_data')

    msg_id = _get_message_id_from_reply_markup(all_messages_3) or msg_id
    msg_text = _get_message_text_from_reply_markup(all_messages_3) or updated_text

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, msg_id, rider_callback, 823,
        reply_markup=updated_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_4 = _collect_messages(
        redis_client, redis_key,
        endpoints=['editMessageText'],
    )

    for msg in all_messages_4:
        _print_message(msg)

    rider_msg = extract_message_data(all_messages_4[0])
    rider_text = rider_msg.get('text', '')

    assert "Райдер-Уэйт" in rider_text, f"Колода не обновилась: {rider_text}"

    preview = _extract_preview_command(rider_text)
    assert preview == '/card3_deck_waite', f"Предпросмотр должен быть /card3_deck_waite, а не: {preview}"
    print(f"✅ Предпросмотр: {preview}")

    # ШАГ 5: Включаем перевернутые
    print("\n" + "═" * 50)
    print("ШАГ 5: Включаем 'С перевернутыми'")
    print("═" * 50)

    rider_kb = rider_msg.get('reply_markup', {}).get('inline_keyboard', [])
    flip_btn = _find_button(rider_kb, 'перевернутыми')
    assert flip_btn is not None, "Нет кнопки 'С перевернутыми'!"
    flip_callback = flip_btn.get('callback_data')

    msg_id = _get_message_id_from_reply_markup(all_messages_4) or msg_id
    msg_text = _get_message_text_from_reply_markup(all_messages_4) or rider_text

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, msg_id, flip_callback, 824,
        reply_markup=rider_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_5 = _collect_messages(
        redis_client, redis_key,
        endpoints=['editMessageText'],
    )

    for msg in all_messages_5:
        _print_message(msg)

    flip_msg = extract_message_data(all_messages_5[0])
    flip_text = flip_msg.get('text', '')

    assert "Да" in flip_text, f"Перевернутые не включены: {flip_text}"

    preview = _extract_preview_command(flip_text)
    assert preview == '/card3_deck_waite_flip', f"Предпросмотр должен быть /card3_deck_waite_flip, а не: {preview}"
    print(f"✅ Предпросмотр: {preview}")

    # ШАГ 6: Жмём "Получить команду"
    print("\n" + "═" * 50)
    print("ШАГ 6: Жмём 'Получить команду'")
    print("═" * 50)

    flip_kb = flip_msg.get('reply_markup', {}).get('inline_keyboard', [])
    go_btn = _find_button(flip_kb, 'Получить')
    assert go_btn is not None, "Нет кнопки 'Получить команду'!"
    go_callback = go_btn.get('callback_data')
    assert 'oh_cfg_go' == go_callback, f"Ожидался callback 'oh_cfg_go', получен: {go_callback}"

    msg_id = _get_message_id_from_reply_markup(all_messages_5) or msg_id
    msg_text = _get_message_text_from_reply_markup(all_messages_5) or flip_text

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, msg_id, go_callback, 825,
        reply_markup=flip_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_6 = _collect_messages(
        redis_client, redis_key,
        endpoints=['deleteMessage', 'sendMessage'],
        stop_condition=lambda msgs: (
            any(m.get('endpoint') == 'deleteMessage' for m in msgs) and
            any(m.get('endpoint') == 'sendMessage' for m in msgs)
        )
    )

    for msg in all_messages_6:
        _print_message(msg)

    got_delete = any(m.get('endpoint') == 'deleteMessage' for m in all_messages_6)
    got_send = any(m.get('endpoint') == 'sendMessage' for m in all_messages_6)

    assert got_delete, "Старое сообщение не удалено!"
    print(f"✅ Старое сообщение удалено")

    assert got_send, "Нет итогового сообщения!"
    send_msgs = [m for m in all_messages_6 if m.get('endpoint') == 'sendMessage']
    final_data = extract_message_data(send_msgs[0])
    final_text = final_data.get('text', '')

    assert "/card3_deck_waite_flip" in final_text, f"Итоговая команда не совпадает: {final_text}"
    print(f"✅ Итоговая команда: /card3_deck_waite_flip")

    reply_keyboard = final_data.get('reply_markup', {}).get('keyboard', [])
    assert len(reply_keyboard) == 1, "Ожидалась 1 кнопка ReplyKeyboard"
    btn_text = reply_keyboard[0][0].get('text') if isinstance(reply_keyboard[0][0], dict) else reply_keyboard[0][0]
    assert btn_text == '/card3_deck_waite_flip', f"Неверная кнопка: {reply_keyboard[0][0]}"
    print(f"✅ ReplyKeyboard с командой присутствует!")


@pytest.mark.django_db
def test_onehand_oraculum_flow(send_webhook_update, redis_client):
    """E2E: Флоу Оракула — проверяем только Ленорман"""

    token = "test_token_12345"
    user_id = 8004
    redis_key = f"intercepted_requests:{token}"
    loop = asyncio.get_event_loop()
    client = Client()
    webhook_url = reverse("webhook", kwargs={"token": token})

    print("\n" + "═" * 50)
    print("ШАГ 1: /onehand → 'Оракул'")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    update = {
        "update_id": 830,
        "message": {
            "message_id": 830,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/onehand",
            "entities": [{"offset": 0, "length": 8, "type": "bot_command"}]
        }
    }

    response = send_webhook_update(token, update)
    assert response.status_code == 200

    all_messages = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage'],
    )

    final_msg = extract_message_data(all_messages[0])
    keyboard = final_msg.get('reply_markup', {}).get('inline_keyboard', [])

    oraculum_btn = _find_button(keyboard, 'Оракул')
    assert oraculum_btn is not None, "Нет кнопки 'Оракул'!"
    oraculum_callback = oraculum_btn.get('callback_data')
    print(f"✅ Кнопка 'Оракул' найдена: {oraculum_callback}")

    msg_id = _get_message_id_from_reply_markup(all_messages)
    # Если message_id не найден в reply_markup, используем message_id из исходного update
    if msg_id is None:
        msg_id = 830
    assert msg_id is not None, "message_id не найден!"
    msg_text = _get_message_text_from_reply_markup(all_messages) or "previous message"

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, msg_id, oraculum_callback, 831,
        reply_markup=final_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_2 = _collect_messages(
        redis_client, redis_key,
        endpoints=['editMessageText'],
    )

    for msg in all_messages_2:
        _print_message(msg)

    assert len(all_messages_2) > 0, "Нет editMessageText после выбора Оракула!"

    config_msg = extract_message_data(all_messages_2[0])
    config_text = config_msg.get('text', '')
    config_kb = config_msg.get('reply_markup', {}).get('inline_keyboard', [])

    all_texts = [b.get('text', '') for row in config_kb for b in row]
    assert any('Ленорман' in t for t in all_texts), "Нет кнопки 'Ленорман'!"
    assert not any('Райдер-Уэйт' in t for t in all_texts), "В Оракуле не должно быть 'Райдер-Уэйт'!"
    print(f"✅ В Оракуле только Ленорман, без Райдер-Уэйт")

    has_major = any('Старшие' in t for t in all_texts)
    assert not has_major, "В Оракуле не должно быть кнопок 'Только Старшие'!"
    print(f"✅ Ряд 'Только Старшие' отсутствует")

    preview = _extract_preview_command(config_text)
    assert preview == '/oraculum', f"Предпросмотр должен быть /oraculum, а не: {preview}"
    print(f"✅ Предпросмотр Оракула: {preview}")


@pytest.mark.django_db
def test_onehand_runes_flow(send_webhook_update, redis_client):
    """E2E: Флоу Рун — 1 и 3 руны"""

    token = "test_token_12345"
    user_id = 8005
    redis_key = f"intercepted_requests:{token}"
    loop = asyncio.get_event_loop()
    client = Client()
    webhook_url = reverse("webhook", kwargs={"token": token})

    print("\n" + "═" * 50)
    print("ШАГ 1: /onehand → 'Руны'")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    update = {
        "update_id": 840,
        "message": {
            "message_id": 840,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/onehand",
            "entities": [{"offset": 0, "length": 8, "type": "bot_command"}]
        }
    }

    response = send_webhook_update(token, update)
    assert response.status_code == 200

    all_messages = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage'],
    )

    final_msg = extract_message_data(all_messages[0])
    keyboard = final_msg.get('reply_markup', {}).get('inline_keyboard', [])

    runes_btn = _find_button(keyboard, 'Руны')
    assert runes_btn is not None, "Нет кнопки 'Руны'!"
    runes_callback = runes_btn.get('callback_data')
    print(f"✅ Кнопка 'Руны' найдена: {runes_callback}")

    msg_id = _get_message_id_from_reply_markup(all_messages) or 840
    msg_text = _get_message_text_from_reply_markup(all_messages) or "previous message"

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, msg_id, runes_callback, 841,
        reply_markup=final_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_2 = _collect_messages(
        redis_client, redis_key,
        endpoints=['editMessageText'],
    )

    for msg in all_messages_2:
        _print_message(msg)

    assert len(all_messages_2) > 0, "Нет editMessageText после выбора Рун!"

    runes_msg = extract_message_data(all_messages_2[0])
    runes_text = runes_msg.get('text', '')
    runes_kb = runes_msg.get('reply_markup', {}).get('inline_keyboard', [])

    assert "Выберите расклад рун" in runes_text, f"Ожидался текст выбора рун: {runes_text}"
    print(f"✅ Меню рун открыто")

    row1 = [b.get('text', '') for b in runes_kb[0]]
    assert any('1 руна' in t for t in row1), "Нет кнопки '1 руна'!"
    assert any('3 руны' in t for t in row1), "Нет кнопки '3 руны'!"
    print(f"✅ Кнопки рун: {row1}")

    preview = _extract_preview_command(runes_text)
    assert preview == '/futhark', f"Предпросмотр должен быть /futhark, а не: {preview}"
    print(f"✅ Предпросмотр по умолчанию: {preview}")

    # ШАГ 2: Меняем на 3 руны
    print("\n" + "═" * 50)
    print("ШАГ 2: Меняем на '3 руны'")
    print("═" * 50)

    rune3_btn = _find_button(runes_kb, '3 руны')
    assert rune3_btn is not None, "Нет кнопки '3 руны'!"
    rune3_callback = rune3_btn.get('callback_data')

    msg_id = _get_message_id_from_reply_markup(all_messages_2) or msg_id
    msg_text = _get_message_text_from_reply_markup(all_messages_2) or runes_text

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, msg_id, rune3_callback, 842,
        reply_markup=runes_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_3 = _collect_messages(
        redis_client, redis_key,
        endpoints=['editMessageText'],
    )

    for msg in all_messages_3:
        _print_message(msg)

    assert len(all_messages_3) > 0, "Нет editMessageText после выбора 3 рун!"

    updated_msg = extract_message_data(all_messages_3[0])
    updated_text = updated_msg.get('text', '')

    preview = _extract_preview_command(updated_text)
    assert preview == '/futhark_triplet', f"Предпросмотр должен быть /futhark_triplet, а не: {preview}"
    print(f"✅ Предпросмотр обновлён: {preview}")

    # ШАГ 3: Жмём "Получить команду"
    print("\n" + "═" * 50)
    print("ШАГ 3: Жмём 'Получить команду'")
    print("═" * 50)

    updated_kb = updated_msg.get('reply_markup', {}).get('inline_keyboard', [])
    go_btn = _find_button(updated_kb, 'Получить')
    assert go_btn is not None, "Нет кнопки 'Получить команду'!"
    go_callback = go_btn.get('callback_data')

    msg_id = _get_message_id_from_reply_markup(all_messages_3) or msg_id
    msg_text = _get_message_text_from_reply_markup(all_messages_3) or updated_text

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, msg_id, go_callback, 843,
        reply_markup=updated_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_4 = _collect_messages(
        redis_client, redis_key,
        endpoints=['deleteMessage', 'sendMessage'],
        stop_condition=lambda msgs: (
            any(m.get('endpoint') == 'deleteMessage' for m in msgs) and
            any(m.get('endpoint') == 'sendMessage' for m in msgs)
        )
    )

    for msg in all_messages_4:
        _print_message(msg)

    got_send = any(m.get('endpoint') == 'sendMessage' for m in all_messages_4)
    assert got_send, "Нет итогового сообщения!"
    send_msgs = [m for m in all_messages_4 if m.get('endpoint') == 'sendMessage']
    final_data = extract_message_data(send_msgs[0])
    final_text = final_data.get('text', '')

    assert "/futhark_triplet" in final_text, f"Итоговая команда не совпадает: {final_text}"
    print(f"✅ Итоговая команда: /futhark_triplet")


@pytest.mark.django_db
def test_onehand_data_lost(send_webhook_update, redis_client):
    """E2E: Проверка поведения при потере данных (перезапуск бота)"""

    token = "test_token_12345"
    user_id = 8006
    redis_key = f"intercepted_requests:{token}"
    loop = asyncio.get_event_loop()
    client = Client()
    webhook_url = reverse("webhook", kwargs={"token": token})

    print("\n" + "═" * 50)
    print("ШАГ 1: Отправляем callback без контекста (потеря данных)")
    print("═" * 50)

    # Отправляем callback напрямую, без предварительного /onehand
    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, 999, "oh_cfg_count_3", 900,
        reply_markup=None,
        text="some old message"
    )
    assert response.status_code == 200

    all_messages = _collect_messages(
        redis_client, redis_key,
        endpoints=['editMessageText'],
    )

    for msg in all_messages:
        _print_message(msg)

    assert len(all_messages) > 0, "Нет ответа при потере данных!"

    lost_msg = extract_message_data(all_messages[0])
    lost_text = lost_msg.get('text', '')

    assert "Данные исчезли" in lost_text or "жизнь закончена" in lost_text, \
        f"Ожидалось сообщение о потере данных, получено: {lost_text}"
    print(f"✅ Сообщение о потере данных: {lost_text[:80]}")

    assert lost_msg.get('reply_markup') is None, "Клавиатура должна быть убрана!"
    print(f"✅ Клавиатура убрана")

    assert "/onehand" in lost_text, f"Должен быть призыв нажать /onehand: {lost_text}"
    print(f"✅ Призыв /onehand присутствует")


@pytest.mark.django_db
def test_onehand_canvas_flow(send_webhook_update, redis_client):
    """E2E: Флоу Холста — проверяем наличие всех параметров как у Таро"""

    token = "test_token_12345"
    user_id = 8007
    redis_key = f"intercepted_requests:{token}"
    loop = asyncio.get_event_loop()
    client = Client()
    webhook_url = reverse("webhook", kwargs={"token": token})

    print("\n" + "═" * 50)
    print("ШАГ 1: /onehand → 'Холст'")
    print("═" * 50)

    loop.run_until_complete(redis_client.delete(redis_key))

    update = {
        "update_id": 860,
        "message": {
            "message_id": 860,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/onehand",
            "entities": [{"offset": 0, "length": 8, "type": "bot_command"}]
        }
    }

    response = send_webhook_update(token, update)
    assert response.status_code == 200

    all_messages = _collect_messages(
        redis_client, redis_key,
        endpoints=['sendMessage'],
    )

    final_msg = extract_message_data(all_messages[0])
    keyboard = final_msg.get('reply_markup', {}).get('inline_keyboard', [])

    canvas_btn = _find_button(keyboard, 'Холст')
    assert canvas_btn is not None, "Нет кнопки 'Холст'!"
    canvas_callback = canvas_btn.get('callback_data')
    print(f"✅ Кнопка 'Холст' найдена: {canvas_callback}")

    msg_id = _get_message_id_from_reply_markup(all_messages) or 860
    msg_text = _get_message_text_from_reply_markup(all_messages) or "previous message"

    loop.run_until_complete(redis_client.delete(redis_key))

    response = _send_callback(
        client, webhook_url, user_id, msg_id, canvas_callback, 861,
        reply_markup=final_msg.get('reply_markup'),
        text=msg_text
    )
    assert response.status_code == 200

    all_messages_2 = _collect_messages(
        redis_client, redis_key,
        endpoints=['editMessageText'],
    )

    for msg in all_messages_2:
        _print_message(msg)

    assert len(all_messages_2) > 0, "Нет editMessageText после выбора Холста!"

    config_msg = extract_message_data(all_messages_2[0])
    config_text = config_msg.get('text', '')
    config_kb = config_msg.get('reply_markup', {}).get('inline_keyboard', [])

    all_texts = [b.get('text', '') for row in config_kb for b in row]

    assert any('Райдер-Уэйт' in t for t in all_texts), "Нет кнопки 'Райдер-Уэйт'!"
    assert any('Старшие' in t for t in all_texts), "Нет кнопки 'Только Старшие'!"
    assert any('перевернутыми' in t for t in all_texts), "Нет кнопки 'С перевернутыми'!"
    print(f"✅ Холст имеет все параметры как Таро")

    preview = _extract_preview_command(config_text)
    assert preview == '/canvas', f"Предпросмотр должен быть /canvas, а не: {preview}"
    print(f"✅ Предпросмотр Холста: {preview}")