from abc import ABC, abstractmethod
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext

# Абстрактный класс с массивом обработчиков
class AbstractBot(ABC):
    handlers = []

    @abstractmethod
    def get_handlers(self):
        pass