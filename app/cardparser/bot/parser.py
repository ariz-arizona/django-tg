import __future__
import re
import aiohttp
import json
import requests
from asgiref.sync import sync_to_async
from telegram import Update, InputMediaPhoto, Chat
from telegram.constants import ChatType
from telegram.ext import CommandHandler, MessageHandler, CallbackContext, filters

from django.utils.timezone import now
from django.db.models import Q
from django.conf import settings

from server.logger import logger

from tg_bot.bot.abstract import AbstractBot
from tg_bot.models import (
    TgUser,
)
from cardparser.utils import render_template
from cardparser.services.wb_link_builder import Se
from cardparser.services.marketing_queryset import (
    get_popular_products,
    get_brand_and_its_top_products,
    get_category_and_its_top_products,
)
from cardparser.models import (
    ParseProduct,
    TgUserProduct,
    Brand,
    Category,
    ProductImage,
    BotSettings,
    EventCaption,
    ProductTemplate,
)

# Класс для парсера бота, который наследует AbstractBot

wb_regexp = r"wildberries\.ru\/(catalog\/(\d*)|product\?card=(\d*))"
ozon_regexp = r"ozon\.ru\/(t\/[^\s]*)\/?"
combined_regexp = f"({wb_regexp}|{ozon_regexp})"


def format_sizes_for_template(sizes: list, show_common_price: bool = False) -> str:
    if not sizes:
        return "—"

    parts = []
    for size in sizes:
        emoji = "✅" if size.get("available") else "❌"
        name = f"<b>{size.get('name', '?')}</b>"  # ← ДОБАВЛЕНО <b>...</b>
        price_part = ""

        if not show_common_price and "price" in size and size["price"] > 0:
            # Форматируем цену с пробелами: 3268 → "3 268"
            formatted_price = f"{size['price']:,.0f}".replace(",", " ")
            price_part = f" — {formatted_price} ₽"

        parts.append(f"{emoji} {name}{price_part}")

    return ", ".join(parts)


def parse_price_string(price_str: str) -> float:
    """
    Преобразует строку вида "3 021 ₽" или "2 900 ₽" в число 3021.0
    Удаляет все нецифровые символы, кроме точки (для копеек, если понадобится).
    """
    if not isinstance(price_str, str):
        return 0.0
    # Удаляем всё, кроме цифр и точки
    cleaned = "".join(c for c in price_str if c.isdigit() or c == ".")
    return float(cleaned) if cleaned else 0.0


class ParserBot(AbstractBot):
    def __init__(self):
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            MessageHandler(
                (
                    (filters.TEXT & filters.Regex(combined_regexp))
                    | (filters.CAPTION & filters.CaptionRegex(combined_regexp))
                )
                & ~filters.COMMAND,
                self.handle_links_based_on_message,
            ),
            CommandHandler(
                "last", self.handle_last_products
            ),  # Обработчик для команды /last
            CommandHandler("start", self.start),
            CommandHandler("search", self.handle_search_command, has_args=True),
            CommandHandler("popular", self.handle_popular_command),
            CommandHandler("top_brand", self.handle_topbrand_command),
            CommandHandler("top_category", self.handle_topcategory_command),
        ]

    async def wb_image_url_get(self, context, card_id, session):
        max_size = 51000  # Максимальный размер изображения
        image_size = None
        image_url = None

        for image in ["1.webp", "1.jpg"]:
            try:
                image_url = f"{Se.construct_host_v2(card_id, 'nm')}/images/big/{image}"
                logger.info(f"Проверка URL: {image_url}")

                async with session.head(image_url) as response:
                    logger.debug(f"Ответ сервера: {response.status}")

                    if response.status == 200:
                        image_size = int(response.headers.get("content-length", 0))
                        logger.info(f"Размер изображения: {image_size} байт")
                        break

            except aiohttp.ClientError as e:
                logger.error(f"Ошибка при запросе к {image_url}: {e}")
            except Exception as e:
                logger.error(f"Неожиданная ошибка: {e}")

        if image_size and image_size > max_size:
            async with session.get(image_url) as img_response:
                picture_chat_id = (await BotSettings.get_active()).picture_chat_id
                image_data = await img_response.read()
                sent_photo = await context.bot.send_photo(picture_chat_id, image_data)
                image_url = sent_photo.photo[-1].file_id

        return image_url

    async def wb(self, card_id, context: CallbackContext):
        card_url = f"https://card.wb.ru/cards/v4/detail?curr=rub&dest=-1059500,-72639,-3826860,-5551776&nm={card_id}"

        txt = []

        async with aiohttp.ClientSession() as session:
            # Загружаем данные карточки
            async with session.get(card_url) as response:
                try:
                    card = await response.json()
                    product = card["products"][0]
                    image_url = await self.wb_image_url_get(context, card_id, session)
                    if not image_url:
                        return None

                    # Парсинг данных
                    sku = card_id
                    brand = product["brand"]
                    link = f"https://wildberries.ru/catalog/{card_id}/detail.aspx"
                    name = product["name"]

                    sizes = []
                    for size in product["sizes"]:
                        size_name = size["name"]
                        available = len(size["stocks"]) > 0
                        obj = {
                            "name": size_name,
                            "available": available,
                        }

                        # Защита от отсутствия поля price
                        price_data = size.get("price", None)
                        if price_data:
                            current_price = price_data.get("product")
                            if current_price is None:
                                current_price = price_data.get(
                                    "basic", 0
                                )  # fallback на basic

                            price_rub = current_price / 100
                            obj["price"] = price_rub

                        sizes.append(obj)

                    # Формируем текст
                    txt.append(f"Разбор карточки WB <code>{sku}</code>")
                    txt.append(f'{brand} <a href="{link}">{name}</a>')
                    show_common_price = True
                    txt.append("Размеры и цена:")
                    txt.append(
                        ", ".join(
                            (
                                f"{'✅' if size['available'] else '❌'} "
                                f"<b>{size['name']}</b>"
                                f"{' '.join(['', '—',str(size['price']),'₽']) if not show_common_price and hasattr(size, 'price') else ''}"
                            )
                            for size in sizes
                        )
                    )

                    caption_data = {
                        "sku": sku,
                        "name": name,
                        "link": link,
                        "sizes": sizes,
                        "availability": any(size["available"] for size in sizes),
                    }
                    if brand:
                        caption_data["brand"] = brand

                    return {
                        "media": image_url,
                        "caption": "\n".join(txt),
                        "parse_mode": "HTML",
                        "name": product.get("name"),
                        "caption_data": caption_data,
                        "brand": {
                            "id": product.get("brandId"),
                            "name": product.get("brand"),
                        },
                        "category": {
                            "id": product.get("subjectId"),
                            "name": product.get("entity"),
                        },
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
        default_template = await ProductTemplate.aget_default_template()
        if not default_template:
            default_template = """\
Разбор карточки {sku}
{brand} <a href="{link}">{name}</a>
Цена: {price_display}
Размеры: {sizes_display}
Наличие: {availability_display}
"""

        for i in items:
            p = await parse_func(i, context)
            if p and p["media"]:
                hash_prefix = "bot_"
                caption_data = p["caption_data"]
                template_context = {
                    "sku": caption_data.get("sku", "N/A"),
                    "sizes": caption_data.get("sizes", "-"),
                    "brand": caption_data.get("brand", "Неизвестный бренд"),
                    "name": caption_data.get("name", "Без названия"),
                    "link": caption_data.get("link", "#"),
                    "hash_type": "#" + hash_prefix + product_type,
                    "hash_category": "",
                    "hash_brand": "",
                }

                if "category" in p:
                    template_context["hash_category"] = (
                        "#"
                        + hash_prefix
                        + re.sub(r"\W+", "_", p["category"]["name"].lower())
                    )

                if "brand" in p:
                    template_context["hash_brand"] = (
                        "#"
                        + hash_prefix
                        + re.sub(r"\W+", "_", p["brand"]["name"].lower())
                    )

                promo_parts = []
                promo_parts.append(
                    f"🤖 <a href='{context.bot.link}'>@{context.bot.username}</a>"
                )

                settings = await BotSettings.get_active()
                chat_instance = await context.bot.get_chat(settings.marketing_group_id)
                if chat_instance.link:
                    promo_parts.append(
                        f"💬 <a href='{chat_instance.link}'>Группа поддержки</a>"
                    )

                template_context["promo"] = " | ".join(promo_parts)

                sizes = caption_data.get("sizes", [])

                active_prices = [
                    p.get("price")
                    for p in caption_data.get("sizes", [])
                    if p.get("price") is not None and p.get("price") > 0
                ]
                show_common_price = (
                    len(set(active_prices)) == 1 if active_prices else False
                )
                if active_prices:
                    min_price = min(active_prices)
                    max_price = max(active_prices)
                    if show_common_price:
                        template_context["price_display"] = (
                            f"{min_price:,.0f}".replace(",", " ") + " ₽"
                        )
                    else:
                        template_context["price_display"] = (
                            f"{min_price:,.0f} – {max_price:,.0f}".replace(",", " ")
                            + " ₽"
                        )
                else:
                    template_context["price_display"] = "—"

                template_context["sizes_display"] = format_sizes_for_template(
                    sizes, show_common_price
                )
                template_context["availability_display"] = (
                    "✅ В наличии"
                    if caption_data["availability"]
                    else "❌ Нет в наличии"
                )

                rendered_caption = render_template(default_template, template_context)

                pictures.append(
                    {
                        "media": p["media"],
                        "caption": rendered_caption,
                        "parse_mode": p["parse_mode"],
                    }
                )
                # Проверяем, есть ли уже записи с таким product_id
                existing_products = ParseProduct.objects.filter(product_id=i).order_by(
                    "-created_at"
                )

                if (await existing_products.acount()) > 1:
                    # Если найдено более одной записи, удаляем все, кроме самой новой
                    async for product in existing_products[1:]:
                        await ParseProduct.objects.filter(id=product.id).adelete()

                brand_obj = None
                brand_data = p.get("brand")
                if brand_data:
                    brand_id = brand_data.get("id")
                    brand_name = brand_data.get("name")

                    if brand_id and brand_name:
                        try:
                            brand_obj, created = await Brand.objects.aget_or_create(
                                brand_id=str(brand_id),
                                product_type=product_type,
                                defaults={"name": brand_name},
                            )
                        except Exception as e:
                            logger.error(
                                f"Ошибка при создании бренда {brand_name} ({brand_id}): {e}"
                            )

                category_obj = None
                category_data = p.get("category")
                if category_data:
                    category_id = category_data.get("id")
                    category_name = category_data.get("name")

                    if category_id and category_name:
                        try:
                            category_obj, created = (
                                await Category.objects.aget_or_create(
                                    subject_id=int(category_id),
                                    product_type=product_type,
                                    defaults={
                                        "name": category_name
                                    },  # только при создании
                                )
                            )
                        except Exception as e:
                            logger.error(
                                logger.error(
                                    f"Ошибка при создании категории {category_name} ({category_id}): {e}"
                                )
                            )

                product, product_created = await ParseProduct.objects.aupdate_or_create(
                    product_id=i,
                    name=p["name"],
                    defaults={
                        "brand": brand_obj,
                        "category": category_obj,
                        "caption": p["caption"],
                        "product_type": product_type,
                    },
                )

                need_update = False
                if brand_obj and product.brand_id != brand_obj.id:
                    product.brand = brand_obj
                    need_update = True

                if category_obj and product.category_id != category_obj.id:
                    product.category = category_obj
                    need_update = True

                if need_update:
                    await product.asave(update_fields=["brand", "category"])

                image_qs = product.images.all()
                if await image_qs.aexists():
                    # Берём первое (или можно выбрать по порядку, если будет сортировка)
                    product_image = await image_qs.afirst()
                else:
                    product_image = None

                media = p["media"]
                media_type = {"image_type": "", "file_id": None, "url": None}
                if media.lower().startswith(("http://", "https://")):
                    media_type["image_type"] = "link"
                    media_type["url"] = media
                else:
                    media_type["image_type"] = "telegram"
                    media_type["file_id"] = media

                if product_image is None:
                    # Создаём новое
                    product_image = await ProductImage.objects.acreate(
                        product=product, **media_type
                    )
                elif (
                    product_image.image_type != media_type["image_type"]
                    or (product_image.file_id or "") != (media_type["file_id"] or "")
                    or (product_image.url or "") != (media_type["url"] or "")
                ):
                    product_image.image_type = media_type["image_type"]
                    product_image.file_id = media_type["file_id"]
                    product_image.url = media_type["url"]
                    await product_image.asave()

                await TgUserProduct.objects.acreate(tg_user=user, product=product)

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
        logger.info(update)
        if not update.effective_message:
            return
        message_text = update.effective_message.caption or update.effective_message.text

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
        url = f"https://api.ozon.ru/entrypoint-api.bx/page/json/v2?url=/{ozon_id}"
        # ozon_req = requests.get(
        #     url,
        #     headers={
        #         "Content-Type": "application/json;charset=UTF-8",
        #         'x-o3-page-type': 'pdp',
        #     },
        # )
        # ozon_api = ozon_req.json()
        parser_url_ozon = (await BotSettings.get_active()).parser_url_ozon
        parser_url = f"{parser_url_ozon}/v1"

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
                logger.info(ozon_api.get("status"))
                logger.info(ozon_api)
                logger.info(ozon_api.text())
                logger.info(url)
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
            sku = None
            product_name = None
            price = None
            brand_name = None
            availability = True
            sizes = []

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
            aspects_data = self.get_ozon_widget(widget_states, "webAspects")

            if out_of_stock:
                sku = out_of_stock.get("sku")
                product_name = out_of_stock.get("skuName", "")
                availability = False
            else:
                sku = add_to_cart.get("sku", None) or heading.get("id", None)
                product_name = heading.get("title", "")
                brand_name = brand.get("name", "")

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

            if aspects_data:
                for aspect in aspects_data.get("aspects", []):
                    if aspect.get("aspectKey") == "size":  # Только размеры
                        for variant in aspect.get("variants", []):
                            size_name = ""
                            # Извлекаем название размера
                            text_rs = variant.get("data", {}).get("textRs", [])
                            if (
                                text_rs
                                and isinstance(text_rs, list)
                                and len(text_rs) > 0
                            ):
                                size_name = "".join(
                                    item.get("content", "")
                                    for item in text_rs
                                    if item.get("type") == "text"
                                ).strip()

                            # Определяем наличие
                            available = variant.get("availability") == "inStock"

                            # Парсим цену
                            raw_price = variant.get(
                                "price"
                            )  # Может быть строкой "2 900 ₽" или числом 2900
                            aspect_price = None
                            if raw_price:
                                if isinstance(raw_price, str):
                                    aspect_price = parse_price_string(raw_price)
                                else:
                                    aspect_price = float(raw_price)

                            sizes.append(
                                {
                                    "name": size_name,
                                    "available": available,
                                    "price": aspect_price,
                                }
                            )
            elif out_of_stock:
                sizes.append(
                    {
                        "name": out_of_stock.get("skuName"),
                        "available": False,
                        "price": parse_price_string(out_of_stock.get("price")),
                    }
                )

            if not img:
                raise Exception("no image")

            caption_data = {
                "sku": sku,
                "name": product_name[0:20],
                "link": f'https://ozon.ru{page_info.get("url", ozon_id)}',
                "sizes": sizes,
                "availability": availability,
            }

            if brand_name:
                caption_data["brand"] = brand_name

            result = {
                "name": product_name,
                "media": img,
                "caption_data": caption_data,
                "caption": "\n".join(txt),
                "parse_mode": "HTML",
            }

            if brand:
                try:
                    result["brand"] = {
                        "id": brand["link"].split("/")[-1],
                        "name": brand["content"]["title"]["text"][0]["content"],
                    }
                except Exception as e:
                    logger.info(e)

            if r.get("layoutTrackingInfo"):
                try:
                    track = json.loads(r.get("layoutTrackingInfo"))
                    result["category"] = {
                        "id": track["categoryId"],
                        "name": track["categoryName"],
                    }
                except Exception as e:
                    logger.info(e)
            return result
        except Exception as e:
            logger.error(e, exc_info=True)
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

    async def handle_topcategory_command(
        self, update: Update, context: CallbackContext
    ):
        exclude_cat_raw = update.message.text.split(maxsplit=1)
        exclude_cat_ids = []
        if len(exclude_cat_raw) > 1:
            exclude_cat_ids = [int(x) for x in exclude_cat_raw[1].strip().split(" ")]
        items = await sync_to_async(get_category_and_its_top_products)(
            hours=24, limit=5, exclude_category_ids=exclude_cat_ids
        )
        event_type = EventCaption.EventType.TOP_CATEGORY
        await self.send_to_marketing_group(
            items["top_products"],
            event_type,
            update,
            context,
        )

    async def handle_topbrand_command(self, update: Update, context: CallbackContext):
        exclude_cat_raw = update.message.text.split(maxsplit=1)
        exclude_cat_ids = []
        if len(exclude_cat_raw) > 1:
            exclude_cat_ids = [int(x) for x in exclude_cat_raw[1].strip().split(" ")]
        items = await sync_to_async(get_brand_and_its_top_products)(
            hours=24, limit=5, exclude_category_ids=exclude_cat_ids
        )
        event_type = EventCaption.EventType.TOP_BRAND
        await self.send_to_marketing_group(
            items["top_products"],
            event_type,
            update,
            context,
        )

    async def handle_popular_command(self, update: Update, context: CallbackContext):
        items = await sync_to_async(get_popular_products)(hours=24, limit=5)
        event_type = EventCaption.EventType.POPULAR
        await self.send_to_marketing_group(items, event_type, update, context)

    async def send_to_marketing_group(
        self,
        items: list[dict],
        event_type: EventCaption.EventType,
        update: Update,
        context: CallbackContext,
    ):
        try:

            if not update.message.from_user.first_name == "django_task":
                return

            # Получаем активные настройки (асинхронно)
            settings = await BotSettings.get_active()
            if not settings:
                logger.error("❌ Нет активных настроек бота.")
                return

            # Проверяем, что marketing_group_id задан
            target_chat_id = settings.marketing_group_id
            if not target_chat_id:
                logger.error("❌ Не задан chat_id для маркетинговой группы.")
                return

            if not items:
                logger.info("Нет товаров для вывода")
                return

            msg_obj = await EventCaption.aget_active_by_type(event_type)
            if not msg_obj:
                logger.info("Нет шаблона сообщений.")
                return

            # Формируем сообщение
            message = msg_obj["text"]
            for i, item in enumerate(items, start=1):
                name = item["name"]
                brand = item["brand"]
                platform = item["product_type"]
                count = item["request_count"]
                message += (
                    f"{i}. <b>{name}</b> ({brand}, {platform}) — {count} запросов\n"
                )

            # Отправляем в маркетинговую группу
            await context.bot.send_message(
                chat_id=target_chat_id, text=message, parse_mode="HTML"
            )
            # Формируем медиагруппу
            media_group = []
            for item in items:
                name = item["name"]
                brand = item["brand"]
                platform = item["product_type"]
                count = item["request_count"]
                caption_text = item["caption"].strip()

                # Полная подпись к фото
                full_caption = render_template(
                    msg_obj["caption"],
                    {
                        "name": name,
                        "caption_text": caption_text,
                        "brand": brand,
                        "platform": platform,
                        "count": count,
                    },
                )

                # Получаем товар и его фото
                try:
                    product = await ParseProduct.objects.aget(id=item["id"])
                    image = await ProductImage.objects.filter(
                        product=product, image_type="telegram"
                    ).afirst()

                    if image and image.file_id:
                        media_group.append(
                            InputMediaPhoto(
                                media=image.file_id,
                                caption=full_caption,
                                parse_mode="HTML",
                            )
                        )
                except ParseProduct.DoesNotExist:
                    continue

            # Отправляем в маркетинговую группу
            if media_group:
                try:
                    await context.bot.send_media_group(
                        chat_id=target_chat_id, media=media_group
                    )
                    logger.info(f"{event_type} {len(items)} с фото отправлен в группу.")
                except Exception as e:
                    logger.error(f"Не удалось отправить медиагруппу: {e}")
            else:
                logger.error("Нет картинок")

        except Exception as e:
            logger.error(
                f"Ошибка в отправке картинок в маркетинговую группу: {e}", exc_info=True
            )
