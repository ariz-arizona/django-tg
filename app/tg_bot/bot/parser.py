import re
import aiohttp
from telegram import Update, InputMediaPhoto
from telegram.ext import CommandHandler, MessageHandler, CallbackContext, filters

from tg_bot.bot.abstract import AbstractBot
from tg_bot.bot.wb_image_url import image_url

from server.logger import logger

# Класс для парсера бота, который наследует AbstractBot

wb_regexp = r"wildberries\.ru\/(catalog\/(\d*)|product\?card=(\d*))"
picture_chat = -1001890980411


class ParserBot(AbstractBot):
    def __init__(self):
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(wb_regexp),
                self.handle_wildberries_links,
            ),
            CommandHandler("start", self.start),
        ]

    async def wb(self, card_id, context: CallbackContext):
        card_url = f"https://card.wb.ru/cards/detail?dest=-1059500,-72639,-3826860,-5551776&nm={card_id}"
        max_size = 51000  # Максимальный размер изображения
        txt = []

        async with aiohttp.ClientSession() as session:
            # Загружаем данные карточки
            async with session.get(card_url) as response:
                try:
                    card = await response.json()
                    product = card["data"]["products"][0]

                    # Работа с изображением
                    image = f"{image_url(card_id)}1.webp"
                    async with session.head(image) as response:
                        image_size = int(response.headers.get("content-length", 0))

                    if image_size > max_size:
                        async with session.get(image) as img_response:
                            image_data = await img_response.read()
                            sent_photo = await context.bot.send_photo(
                                picture_chat, image_data
                            )
                            image = sent_photo.photo[-1].file_id

                    # Парсинг данных
                    sku = card_id
                    brand = product["brand"]
                    link = f"https://wildberries.ru/catalog/{card_id}/detail.aspx"
                    name = product["name"]
                    price = {"price": product["priceU"] / 100}

                    if "salePriceU" in product:
                        price["discount"] = product["salePriceU"] / 100

                    sizes = [
                        {"name": size["name"], "available": len(size["stocks"]) > 0}
                        for size in product["sizes"]
                    ]

                    # Формируем текст
                    txt.append(f"Разбор карточки WB <code>{sku}</code>")
                    txt.append(f'{brand} <a href="{link}">{name}</a>')
                    txt.append(
                        "Цена: "
                        + (
                            f"{price['discount']} <s>{price['price']}</s>"
                            if "discount" in price
                            else f"{price['price']}"
                        )
                    )
                    txt.append(
                        "Размеры: \n"
                        + ", ".join(
                            f"{'✅' if size['available'] else '❌'} {size['name']}"
                            for size in sizes
                        )
                    )

                    # Возвращаем сообщение
                    return {
                        "media": image,
                        "caption": "\n".join(txt),
                        "parse_mode": "HTML",
                    }
                except Exception as e:
                    logger.error(e)
                    return None
                

    async def handle_wildberries_links(self, update: Update, context: CallbackContext):
        message_text = update.message.text
        matches = re.findall(wb_regexp, message_text)
        items = [match[1] or match[2] for match in matches]

        pictures = []
        for i in items:
            pictures.append(await self.wb(i, context))

        for i in range(0, len(pictures), 10):
            group = pictures[i : i + 10]
            media_group = [InputMediaPhoto(**photo) for photo in group if photo is not None]
            await update.message.reply_media_group(media=media_group, reply_to_message_id=update.message.message_id)

    async def start(self, update: Update, context: CallbackContext):
        logger.info("start")
        await update.message.reply_html("<b>123</b>")
