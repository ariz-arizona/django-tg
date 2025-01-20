from telegram import Update
from telegram.ext import CommandHandler, CallbackContext

from tg_bot.bot.abstract import AbstractBot

from server.logger import logger
# Класс для парсера бота, который наследует AbstractBot


class ParserBot(AbstractBot):
    def __init__(self):
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            CommandHandler("wbParse", self.wb_parse),
            CommandHandler("start", self.start),
        ]

    async def wb_parse(self, update: Update, context: CallbackContext):
        logger.info('parse')
        await update.message.reply_text("Запуск парсинга...")
    async def start(self, update: Update, context: CallbackContext):
        logger.info('start')
        await update.message.reply_html('<b>123</b>')