from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import BotViewSet, webhook

# Создаём роутер и регистрируем наш ViewSet
router = DefaultRouter()
router.register(r"bots", BotViewSet)

urlpatterns = [
    path("", include(router.urls)),  
    path("webhook/<str:token>/", webhook, name="webhook"),  # Маршрут для вебхука с токеном
]
