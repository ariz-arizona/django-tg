import redis
import os
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework import viewsets

from .models import Bot
from .serializers import BotSerializer
from server.logger import logger

redis_client = redis.StrictRedis(
    host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT"), db=2
)


class BotViewSet(viewsets.ModelViewSet):
    queryset = Bot.objects.all()
    serializer_class = BotSerializer


# Декоратор для обработки webhook без CSRF-проверки
@csrf_exempt
def webhook(request, token):
    if request.method == "POST":
        # Получаем JSON строку из тела запроса
        json_str = request.body.decode("UTF-8")

        # Проверка, что это сообщение (опционально)
        try:
            # Вы можете просто поместить строку JSON в очередь
            redis_client.rpush(f"bot_messages_queue_{token}", json_str)
            try:
                message = json.loads(json_str)
                
                # Определяем тип обновления и извлекаем данные
                if "callback_query" in message:
                    callback = message["callback_query"]
                    from_user = callback.get("from", {})
                    text_or_caption = callback.get("data", "")
                    update_id = message.get("update_id", "unknown")
                else:
                    message_content = message.get("message") or message.get("edited_message") or {}
                    from_user = message_content.get("from", {})
                    text_or_caption = (
                        message_content.get("text") 
                        or message_content.get("caption") 
                        or ""
                    )
                    update_id = message.get("update_id", "unknown")
                
                logger.info(
                    f"Сообщение добавлено в очередь: update_id: {update_id}"
                    f" от {from_user.get('username') or from_user.get('first_name', 'unknown')} ({from_user.get('id', 'unknown')})"
                    f" с текстом {text_or_caption[:20] if text_or_caption else 'empty'}"
                )
                
            except KeyError as e:
                logger.error(f"Ошибка при обработке обновления: отсутствует ключ {e}")
                logger.info(f"Сообщение добавлено в очередь (сырое): {json_str[:200]}")
            except Exception as e:
                logger.error(f"Неожиданная ошибка при обработке обновления: {e}")
                logger.info(f"Сообщение добавлено в очередь (с ошибкой), update_id: {message.get('update_id', 'unknown') if 'message' in locals() else 'unknown'}")
            return JsonResponse({"status": "ok"})
        except Exception as e:
            logger.error(
                f"Ошибка при добавлении сообщения в очередь: {e}", exc_info=True
            )
            return JsonResponse({"status": "error"}, status=400)

    return JsonResponse({"status": "error"}, status=400)
