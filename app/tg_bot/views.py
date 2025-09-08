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
            message = json.loads(json_str)
            logger.info(f"Сообщение добавлено в очередь: {message}")
            return JsonResponse({"status": "ok"})
        except Exception as e:
            logger.error(
                f"Ошибка при добавлении сообщения в очередь: {e}", exc_info=True
            )
            return JsonResponse({"status": "error"}, status=400)

    return JsonResponse({"status": "error"}, status=400)
