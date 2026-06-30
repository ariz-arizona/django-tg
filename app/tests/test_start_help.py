# tests/test_start_help.py
import pytest


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