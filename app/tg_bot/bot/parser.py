import __future__
import re
import aiohttp
import json
import requests
from telegram import Update, InputMediaPhoto
from telegram.ext import CommandHandler, MessageHandler, CallbackContext, filters

from django.utils.timezone import now
from django.db.models import Q
from django.conf import settings

from tg_bot.bot.abstract import AbstractBot
from tg_bot.bot.wb_image_url import Se
from tg_bot.models import TgUser, ParseProduct, TgUserProduct

from server.logger import logger

PICTURE_CHAT = settings.PICTURE_CHAT
PARSER_URL = settings.PARSER_URL

# Класс для парсера бота, который наследует AbstractBot

wb_regexp = r"wildberries\.ru\/(catalog\/(\d*)|product\?card=(\d*))"
ozon_regexp = r"ozon\.ru\/(t\/[^\s]*)\/?"
combined_regexp = f"({wb_regexp}|{ozon_regexp})"


class ParserBot(AbstractBot):
    def __init__(self):
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(combined_regexp),
                self.handle_links_based_on_message,
            ),
            CommandHandler(
                "last", self.handle_last_products
            ),  # Обработчик для команды /last
            CommandHandler("start", self.start),
            CommandHandler("search", self.handle_search_command, has_args=True),
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

                    for image in ["1.webp", "1.jpg"]:
                        try:
                            image_url = f"{Se.construct_host_v2(card_id, 'nm')}/images/big/{image}"
                            logger.info(f"Проверка URL: {image_url}")

                            async with session.head(image_url) as response:
                                logger.info(f"Ответ сервера: {response.status}")

                                if response.status == 200:
                                    image_size = int(
                                        response.headers.get("content-length", 0)
                                    )
                                    logger.info(
                                        f"Размер изображения: {image_size} байт"
                                    )
                                    break

                        except aiohttp.ClientError as e:
                            logger.error(f"Ошибка при запросе к {image_url}: {e}")
                        except Exception as e:
                            logger.error(f"Неожиданная ошибка: {e}")

                    if image_size > max_size:
                        async with session.get(image_url) as img_response:
                            image_data = await img_response.read()
                            sent_photo = await context.bot.send_photo(
                                PICTURE_CHAT, image_data
                            )
                            image_url = sent_photo.photo[-1].file_id

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
                        "media": image_url,
                        "caption": "\n".join(txt),
                        "parse_mode": "HTML",
                    }
                except Exception as e:
                    logger.error(e)
                    return None

    async def handle_links(
        self, items, product_type, parse_func, update: Update, context: CallbackContext
    ):
        # Получаем или создаем пользователя
        user, created = await TgUser.objects.aget_or_create(
            tg_id=update.message.from_user.id,
            defaults={
                "username": update.message.from_user.username,
                "first_name": update.message.from_user.first_name,
                "last_name": update.message.from_user.last_name,
                "language_code": update.message.from_user.language_code,
                "is_bot": update.message.from_user.is_bot,
            },
        )

        pictures = []
        for i in items:
            p = await parse_func(i, context)
            if p and p["media"]:
                pictures.append(p)
                # Проверяем, есть ли уже записи с таким product_id
                existing_products = ParseProduct.objects.filter(product_id=i).order_by('-created_at')

                if (await existing_products.acount()) > 1:
                    # Если найдено более одной записи, удаляем все, кроме самой новой
                    async for product in existing_products[1:]:
                        await ParseProduct.objects.filter(id=product.id).adelete()
                        
                product, product_created = await ParseProduct.objects.aupdate_or_create(
                    product_id=i,
                    defaults={
                        "photo_id": p["media"],
                        "caption": p["caption"],
                        "product_type": product_type,
                    },
                )
                await TgUserProduct.objects.aupdate_or_create(
                    tg_user=user, product=product, defaults={"sent_at": now()}
                )

        for i in range(0, len(pictures), 10):
            group = pictures[i : i + 10]
            media_group = [
                InputMediaPhoto(**photo) for photo in group if photo is not None
            ]
            await update.message.reply_media_group(
                media=media_group, reply_to_message_id=update.message.message_id
            )

    async def handle_links_based_on_message(
        self, update: Update, context: CallbackContext
    ):
        if not update.effective_message:
            return
        message_text = update.effective_message.text

        # Ищем ссылки Wildberries
        wb_matches = re.findall(wb_regexp, message_text)
        wb_items = [match[1] or match[2] for match in wb_matches]

        # Ищем ссылки Ozon
        ozon_matches = re.findall(ozon_regexp, message_text)

        # Если нашли ссылки на Wildberries, обрабатываем их
        if wb_items:
            await self.handle_links(wb_items, "wb", self.wb, update, context)

        # Если нашли ссылки на Ozon, обрабатываем их
        if ozon_matches:
            await self.handle_links(
                ozon_matches, "ozon", self.parse_ozon, update, context
            )

    def get_ozon_widget(self, widget_states, key):
        try:
            widgets = [k for k in widget_states.keys() if key in k]
            res = {}
            for widget_key in widgets:
                res.update(json.loads(widget_states[widget_key]))
            return res
        except Exception as err:
            return {}

    async def parse_ozon(self, ozon_id, context):
        url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/{ozon_id}"
        parser_url = f"{PARSER_URL}/v1"

        # Отправляем запрос на парсер
        payload = {"cmd": "request.get", "maxTimeout": 60000, "url": url}
        response = requests.post(
            parser_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
        )
        ozon_api = response.json()
        try:
            # Проверяем статус ответа
            if ozon_api.get("status") != "ok":
                raise Exception("Parse error")

            if "seller" in ozon_api["solution"]["url"]:
                raise Exception("Not an Ozon product")

            # Извлекаем и обрабатываем ответ
            r = ozon_api["solution"]["response"]
            r = r.replace(
                '<html><head><meta name="color-scheme" content="light dark"></head><body><pre style="word-wrap: break-word; white-space: pre-wrap;">',
                "",
            ).replace("</pre></body></html>", "")
            r = json.loads(r)

            widget_states = r.get("widgetStates", {})
            page_info = r.get("pageInfo", {})
            txt = []
            img = None

            # Обработка виджетов
            error = self.get_ozon_widget(widget_states, "error")
            out_of_stock = self.get_ozon_widget(widget_states, "webOutOfStock")
            price = self.get_ozon_widget(widget_states, "webPrice")
            sale = self.get_ozon_widget(widget_states, "webSale")
            gallery = self.get_ozon_widget(widget_states, "webGallery")
            brand = self.get_ozon_widget(widget_states, "webBrand")
            heading = self.get_ozon_widget(widget_states, "webProductHeading")
            add_to_cart = self.get_ozon_widget(widget_states, "webAddToCart")
            fulltext_results_header = self.get_ozon_widget(
                widget_states, "fulltextResultsHeader"
            )
            search_results = self.get_ozon_widget(widget_states, "searchResults")
            user_adult_modal = self.get_ozon_widget(widget_states, "userAdultModal")

            if error or user_adult_modal:
                txt.append(r["seo"]["title"])
                txt.append(r["seo"]["link"][0]["href"].replace("api.ozon", "ozon"))
                txt.append("")
                txt.append(
                    f"<strong>{error.get('title', user_adult_modal.get('subtitle', {}).get('text', ''))}</strong>"
                )
                seo_img = next(
                    (
                        meta["content"]
                        for meta in r["seo"].get("meta", [])
                        if meta.get("property") == "og:image"
                    ),
                    None,
                )
                img = seo_img
            elif out_of_stock:
                txt.append(f"Разбор карточки OZON <code>{out_of_stock['sku']}</code>")
                txt.append(
                    f"\n{out_of_stock['sellerName']} <a href='https://ozon.ru/{out_of_stock['productLink']}'>{out_of_stock['skuName']}</a>"
                )
                txt.append(f"\nЦена: {out_of_stock['price']}")
                txt.append(f"\nНаличие: ❌")
                img = out_of_stock.get("coverImage")
            elif price or sale:
                txt.append(
                    f"Разбор карточки OZON <code>{gallery.get('sku', '') or heading.get('id', '')}</code>"
                )
                brand_name = brand.get("name", "")
                txt.append(
                    f"{brand_name}<a href='https://ozon.ru{page_info.get('url', ozon_id)}'>{heading.get('title', '')}</a>"
                )
                if price.get("price"):
                    txt.append(
                        f"Цена: {price['price']} {f'''<s>{price.get('originalPrice')}</s>''' if price.get('originalPrice') else ''}"
                    )
                elif add_to_cart.get("price"):
                    txt.append(f"Цена: {add_to_cart['price']}")
                if price.get("isAvailable"):
                    txt.append(f"Наличие: {'✅' if price['isAvailable'] else '❌'}")
                elif sale.get("offer", {}).get("isAvailable"):
                    txt.append(
                        f"Наличие: {'✅' if sale['offer']['isAvailable'] else '❌'}"
                    )
                img = gallery.get("coverImage") or heading.get("coverImage")
            elif fulltext_results_header:
                txt.append(
                    fulltext_results_header["header"]["text"]
                    .replace("**", "<strong>")
                    .replace("[", "<a href='https://www.ozon.ru")
                    .replace("]", "'>")
                    .replace(")", "</a>")
                )
                img = next(
                    (
                        item["image"]["link"]
                        for item in search_results.get("items", [])
                        if item["type"] == "image"
                    ),
                    None,
                )

            if not img:
                raise Exception("no image")

            return {
                "media": img,
                "caption": "\n".join(txt),
                "parse_mode": "HTML",
            }
        except Exception as e:
            logger.error(e)
            return None

    async def start(self, update: Update, context: CallbackContext):
        logger.info("start")
        await update.message.reply_html(
            "<b>Привет!</b> Это бот для получения картинки товара вайлдберрис. И озона. Наверное."
        )

    async def handle_last_products(self, update: Update, context: CallbackContext):
        # Получаем пользователя по tg_id
        try:
            user = await TgUser.objects.aget(tg_id=update.message.from_user.id)

            # Извлекаем последние 10 товаров, отсортированных по дате (sent_at)
            user_products = []
            async for item in (
                TgUserProduct.objects.filter(tg_user=user)
                .select_related("product")
                .order_by("-sent_at")[:10]
            ):
                user_products.append(item)

            if not user_products:
                await update.message.reply_text("Вы не отправляли товары.")
                return

            # Формируем список товаров для отправки в media_group
            media_group = []
            for user_product in user_products:
                product = user_product.product
                logger.info(product)
                # Создаем объект InputMediaPhoto для каждого товара
                media_group.append(
                    InputMediaPhoto(
                        media=product.photo_id,  # Используем photo_id
                        caption=product.caption,  # Подпись к фото
                        parse_mode="HTML",  # Форматирование с HTML, если нужно
                    )
                )

            # Отправляем media_group с изображениями и подписями
            await update.message.reply_media_group(
                media=media_group, reply_to_message_id=update.message.message_id
            )
        except Exception as e:
            logger.error(e)

    async def handle_search_command(self, update: Update, context: CallbackContext):
        try:
            # Получаем текст запроса из команды
            query = update.message.text.split(maxsplit=1)[
                1
            ].strip()  # /search ЗАПРОС -> ЗАПРОС
            query = query[:50]
            if not query:
                await update.message.reply_text(
                    "Пожалуйста, укажите запрос для поиска."
                )
                return

            # Ищем в базе данных записи, где caption содержит запрос
            results = ParseProduct.objects.filter(
                Q(caption__icontains=query)  # Поиск по подстроке (без учета регистра)
            ).order_by("-created_at")[
                :10
            ]  # Берем последние 10 записей

            user_products = []
            async for item in results:
                user_products.append(item)

            if not user_products:
                await update.message.reply_text(
                    f"По запросу '{(query)}' ничего не найдено."
                )
                return

            # Формируем сообщение с результатами
            media_group = []
            for user_product in user_products:
                product = user_product

                # Создаем объект InputMediaPhoto для каждого товара
                media_group.append(
                    InputMediaPhoto(
                        media=product.photo_id,  # Используем photo_id
                        caption=product.caption,  # Подпись к фото
                        parse_mode="HTML",  # Форматирование с HTML, если нужно
                    )
                )

            # Отправляем media_group с изображениями и подписями
            await update.message.reply_media_group(
                media=media_group, reply_to_message_id=update.message.message_id
            )

        except IndexError:
            # Если запрос не указан
            await update.message.reply_text("Пожалуйста, укажите запрос для поиска.")
        except Exception as e:
            # Обработка ошибок
            logger.error(f"Ошибка при выполнении команды /search: {e}", exc_info=True)
            await update.message.reply_text(
                "Произошла ошибка при выполнении поиска. Пожалуйста, попробуйте снова."
            )
