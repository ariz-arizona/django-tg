# tests/conftest.py
import pytest
import os
import json
import asyncio
from telegram.request import HTTPXRequest
import redis.asyncio as redis
import logging

logger = logging.getLogger(__name__)


class RedisLoggingHTTPXRequest(HTTPXRequest):
    """Логирует все запросы в Redis и мокает ответы Telegram API"""
    
    def __init__(self, redis_client, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._redis_client = redis_client
    
    async def do_request(self, url, method='POST', **kwargs):
        endpoint = url.split('/')[-1] if '/' in url else url
        token = url.split('/bot')[1].split('/')[0] if '/bot' in url else 'unknown'
        
        # Извлекаем данные из RequestData
        json_data = {}
        request_data = kwargs.get('request_data')
        
        if request_data is not None:
            # RequestData имеет parameters
            if hasattr(request_data, 'parameters'):
                json_data = request_data.parameters or {}
            # Или json_data
            elif hasattr(request_data, 'json_data'):
                json_data = request_data.json_data or {}
            # Или __dict__
            elif hasattr(request_data, '__dict__'):
                json_data = {k: v for k, v in request_data.__dict__.items() 
                            if not k.startswith('_') and not callable(v)}
        
        # Сохраняем в Redis
        request_info = {
            'method': method,
            'endpoint': endpoint,
            'data': json_data,
        }
        redis_key = f"intercepted_requests:{token}"
        await self._redis_client.lpush(
            redis_key, 
            json.dumps(request_info, ensure_ascii=False)
        )
        
        logger.info(f"🔍 [Patched] {endpoint}: {json.dumps(json_data, ensure_ascii=False)[:300]}")
        
        # Мок-ответ
        mock_response = self._get_mock_response(endpoint, json_data)
        if mock_response:
            return 200, json.dumps(mock_response).encode('utf-8')
        
        return await super().do_request(url, method=method, **kwargs)
    
    def _get_mock_response(self, endpoint, request_data=None):
        """Мок-ответы для Telegram API"""
        request_data = request_data or {}
        logger.info(f"🔍 [Mock] Запрошен endpoint: {endpoint}")
        mocks = {
            'getMe': {
                "ok": True,
                "result": {
                    "id": 123456789,
                    "is_bot": True,
                    "first_name": "Test Bot",
                    "username": "test_bot"
                }
            },
            'setWebhook': {
                "ok": True, 
                "result": True, 
                "description": "Webhook was set"
            },
            'deleteWebhook': {
                "ok": True, 
                "result": True, 
                "description": "Webhook was deleted"
            },
            'getWebhookInfo': {
                "ok": True,
                "result": {
                    "url": "",
                    "has_custom_certificate": False,
                    "pending_update_count": 0
                }
            },
            'sendMessage': {
                "ok": True,
                "result": {
                    "message_id": request_data.get('message_id', 1),
                    "from": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "Test Bot",
                        "username": "test_bot"
                    },
                    "chat": {
                        "id": request_data.get('chat_id', 123456789),
                        "first_name": "Test",
                        "type": "private"
                    },
                    "date": 1717000000,
                    "text": request_data.get('text', 'ok')
                }
            },
            'sendMediaGroup': {
                "ok": True,
                "result": [
                    {
                        "message_id": 100,
                        "from": {
                            "id": 123456789,
                            "is_bot": True,
                            "first_name": "Test Bot"
                        },
                        "chat": {
                            "id": request_data.get('chat_id', 123456789),
                            "first_name": "Test",
                            "type": "private"
                        },
                        "date": 1717000000,
                        "photo": [
                            {
                                "file_id": "test_file_123",
                                "file_unique_id": "unique_123",
                                "width": 960,
                                "height": 960
                            }
                        ],
                        "caption": request_data.get('caption', '')
                    }
                ]
            },
            'sendPhoto': {
                "ok": True,
                "result": {
                    "message_id": 100,
                    "from": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "Test Bot"
                    },
                    "chat": {
                        "id": request_data.get('chat_id', 123456789),
                        "first_name": "Test",
                        "type": "private"
                    },
                    "date": 1717000000,
                    "photo": [
                        {
                            "file_id": "test_file_123",
                            "file_unique_id": "unique_123",
                            "width": 960,
                            "height": 960
                        }
                    ],
                    "caption": request_data.get('caption', '')
                }
            },
            'answerCallbackQuery': {
                "ok": True,
                "result": True
            },
            'editMessageText': {
                "ok": True,
                "result": {
                    "message_id": request_data.get('message_id', 1),
                    "from": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "Test Bot",
                        "username": "test_bot"
                    },
                    "chat": {
                        "id": request_data.get('chat_id', 123456789),
                        "first_name": "Test",
                        "type": "private"
                    },
                    "date": 1717000000,
                    "text": request_data.get('text', 'edited')
                }
            },
            'editMessageCaption': {
                "ok": True,
                "result": {
                    "message_id": request_data.get('message_id', 1),
                    "from": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "Test Bot",
                        "username": "test_bot"
                    },
                    "chat": {
                        "id": request_data.get('chat_id', 123456789),
                        "first_name": "Test",
                        "type": "private"
                    },
                    "date": 1717000000,
                    "caption": request_data.get('caption', 'edited')
                }
            },
            'editMessageMedia': {
                "ok": True,
                "result": {
                    "message_id": request_data.get('message_id', 1),
                    "from": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "Test Bot",
                        "username": "test_bot"
                    },
                    "chat": {
                        "id": request_data.get('chat_id', 123456789),
                        "first_name": "Test",
                        "type": "private"
                    },
                    "date": 1717000000,
                    "photo": [
                        {
                            "file_id": "test_file_123",
                            "file_unique_id": "unique_123",
                            "width": 960,
                            "height": 960
                        }
                    ]
                }
            },
            'editMessageReplyMarkup': {
                "ok": True,
                "result": {
                    "message_id": request_data.get('message_id', 1),
                    "from": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "Test Bot",
                        "username": "test_bot"
                    },
                    "chat": {
                        "id": request_data.get('chat_id', 123456789),
                        "first_name": "Test",
                        "type": "private"
                    },
                    "date": 1717000000,
                    "text": "message with removed markup"
                }
            },
            'deleteMessage': {
                "ok": True,
                "result": True
            },
            'getUpdates': {
                "ok": True,
                "result": []
            },
            'logOut': {
                "ok": True,
                "result": True
            },
            'close': {
                "ok": True,
                "result": True
            },
        }
        return mocks.get(endpoint)


@pytest.fixture(scope="session")
def event_loop():
    """Создаем event loop для всей сессии"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def redis_client():
    """Синхронная фикстура для Redis клиента"""
    async def _get_client():
        client = redis.StrictRedis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=3,
            decode_responses=True
        )
        await client.flushdb()
        return client
    
    loop = asyncio.get_event_loop()
    client = loop.run_until_complete(_get_client())
    
    yield client
    
    async def _close():
        await client.flushdb()
        await client.aclose()
    
    loop.run_until_complete(_close())


@pytest.fixture
def patched_request(redis_client):
    """Патченный HTTP клиент с логированием в Redis и моками"""
    return RedisLoggingHTTPXRequest(redis_client=redis_client)


# === НЕ СОЗДАВАТЬ ТЕСТОВУЮ БД, ИСПОЛЬЗОВАТЬ ИМЕЮЩУЮСЯ ===
@pytest.fixture(scope="session")
def django_db_setup():
    """Не создаем тестовую БД, используем основную"""
    pass


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    """Не модифицируем настройки БД"""
    pass


@pytest.fixture
def django_db_use_existing_database():
    """Использовать существующую БД"""
    from django.test.utils import setup_test_environment
    setup_test_environment()
    return True

def extract_message_data(message):
    """Извлекает данные сообщения из сохраненного запроса"""
    data = message.get('data', {})
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except:
            return {'text': str(data)}
    return data


def sync_lrange(redis_client, key, start, end):
    """Синхронная обертка для lrange"""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(redis_client.lrange(key, start, end))


@pytest.fixture
def get_messages(redis_client):
    """Фикстура для получения и очистки сообщений из Redis"""
    def _get(token, expected_count=1, timeout=5, clear_after=True):
        import time
        redis_key = f"intercepted_requests:{token}"
        start = time.time()
        
        while time.time() - start < timeout:
            all_data = sync_lrange(redis_client, redis_key, 0, -1)
            messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMessage']
            
            if len(messages) >= expected_count:
                # Очищаем Redis после получения
                if clear_after:
                    loop = asyncio.get_event_loop()
                    loop.run_until_complete(redis_client.delete(redis_key))
                
                return messages
            
            time.sleep(0.1)
        
        return []
    
    return _get

@pytest.fixture
def send_webhook_update():
    """Фикстура для отправки апдейта на вебхук"""
    def _send(token, update_data):
        from django.urls import reverse
        from django.test import Client
        import json
        import logging
        
        logger = logging.getLogger(__name__)
        
        client = Client()
        webhook_url = reverse("webhook", kwargs={"token": token})
        
        # Логируем что отправляем
        text = update_data.get('message', {}).get('text', 'unknown')
        user = update_data.get('message', {}).get('from', {}).get('first_name', 'unknown')
        user_id = update_data.get('message', {}).get('from', {}).get('id', 'unknown')
        
        logger.info(f"📤 Отправка '{text}' от {user} ({user_id}) на {webhook_url}")
        
        response = client.post(
            webhook_url,
            data=json.dumps(update_data),
            content_type="application/json"
        )
        
        logger.info(f"📥 Ответ вебхука: {response.status_code}")
        
        return response
    
    return _send

@pytest.fixture
def subscribe_messages(redis_client):
    """Подписка на новые сообщения в Redis через Pub/Sub"""
    def _subscribe(token, callback, timeout=15):
        import time
        import json
        import asyncio
        
        redis_key = f"intercepted_requests:{token}"
        last_count = 0
        
        start = time.time()
        while time.time() - start < timeout:
            all_data = sync_lrange(redis_client, redis_key, 0, -1)
            messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') in 
                       ['sendMessage', 'sendMediaGroup', 'deleteMessage', 'sendPhoto']]
            
            # Если есть новые сообщения
            if len(messages) > last_count:
                new_msgs = messages[:len(messages) - last_count]
                for msg in new_msgs:
                    should_stop = callback(msg)
                    if should_stop:
                        return messages
                last_count = len(messages)
            
            time.sleep(0.2)
        
        return messages
    
    return _subscribe

def wait_and_collect_captions(redis_client, token, all_captions, all_reading_ids=None, timeout=15):
    """Ждет сообщения и собирает подписи из sendMediaGroup + ID ридингов"""
    import json
    import time
    import re
    
    redis_key = f"intercepted_requests:{token}"
    start = time.time()
    got_media = False  # Меняем на got_media вместо got_delete
    
    while time.time() - start < timeout:
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        messages = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') in 
                   ['sendMessage', 'sendMediaGroup', 'deleteMessage']]
        
        for msg in messages:
            endpoint = msg.get('endpoint', '?')
            data = extract_message_data(msg)
            
            if endpoint == 'sendMediaGroup':
                media_items = data.get('media', [])
                for item in media_items:
                    caption = item.get('caption', '').strip()
                    if caption and caption not in all_captions:
                        all_captions.append(caption)
                        print(f"   🃏 {caption[:80]}")
                got_media = True  # Получили картинки
                break  # Выходим сразу
            
            if endpoint == 'sendMessage' and all_reading_ids is not None:
                inline_keyboard = data.get('reply_markup', {}).get('inline_keyboard', [])
                for row in inline_keyboard:
                    for btn in row:
                        cb_data = btn.get('callback_data', '')
                        match = re.search(r'(more|desc)_(\d+)', cb_data)
                        if match:
                            all_reading_ids.add(match.group(2))
        
        if got_media:
            break
        
        time.sleep(0.1)
        
def get_card_names_from_response(redis_client, token, timeout=10):
    """Получает названия карт из sendMediaGroup ответа"""
    import json
    import time
    
    redis_key = f"intercepted_requests:{token}"
    start = time.time()
    
    while time.time() - start < timeout:
        all_data = sync_lrange(redis_client, redis_key, 0, -1)
        media_msgs = [json.loads(r) for r in all_data if json.loads(r).get('endpoint') == 'sendMediaGroup']
        
        if media_msgs:
            data = extract_message_data(media_msgs[0])
            media_items = data.get('media', [])
            return [item.get('caption', '').strip().split('\n')[0] for item in media_items]
        
        time.sleep(0.1)
    
    return []