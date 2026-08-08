# tests/test_tarot_sticker.py
import pytest
import json
import time
import asyncio

from tests.conftest import extract_message_data, sync_lrange, wait_and_collect_captions

@pytest.mark.django_db
def test_tarot_sticker(send_webhook_update, redis_client):
    """E2E: /tarot — получаем стикер"""
    
    token = "test_token_12345"
    user_id = 20001
    redis_key = f"intercepted_requests:{token}"
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(redis_client.delete(redis_key))
    time.sleep(0.5)
    
    update = {
        "update_id": 20000,
        "message": {
            "message_id": 20000,
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": user_id, "type": "private"},
            "date": 1717000000,
            "text": "/tarot",
            "entities": [{"offset": 0, "length": 6, "type": "bot_command"}]
        }
    }
    
    response = send_webhook_update(token, update)
    assert response.status_code == 200
    print("✅ /tarot отправлен")
    
    time.sleep(2)
    
    all_data = sync_lrange(redis_client, redis_key, 0, -1)
    messages = [json.loads(r) for r in all_data]
    
    sticker_msgs = [m for m in messages if m.get('endpoint') == 'sendSticker']
    assert len(sticker_msgs) > 0, "Нет sendSticker!"
    
    sticker_data = extract_message_data(sticker_msgs[0])
    sticker_id = sticker_data.get('sticker', '')
    
    assert len(sticker_id) > 0, "Пустой sticker_id!"
    print(f"🃏 Стикер получен: {sticker_id[:40]}...")
    
    print("✅ Тест /tarot пройден!")