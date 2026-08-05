import __future__
import re
import aiohttp
import json
import requests
import asyncio
import os
from curl_cffi import requests as curl_requests 

from asgiref.sync import sync_to_async
from telegram import Update, InputMediaPhoto, Chat
from telegram.constants import ChatType
from telegram.ext import CommandHandler, MessageHandler, CallbackContext, filters

from django.utils.timezone import now
from django.db.models import Q, Subquery, OuterRef
from django.conf import settings

from server.logger import logger

from tg_bot.bot.abstract import AbstractBot
from tg_bot.models import (
    TgUser,
)
from cardparser.utils import render_template
from cardparser.services.wb_proxy import wb_fetch_with_session, wb_fetch_head, wb_fetch_image
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
default_caption_template = """\
Разбор карточки {sku}
{brand} <a href="{link}">{name}</a>
Цена: {price_display}
Размеры: {sizes_display}
Наличие: {availability_display}
"""


def format_sizes_for_template(
    sizes: list, show_common_price: bool = False, limit=10
) -> str:
    was_filtered_or_truncated = False

    original_len = len(sizes)
    if original_len > limit:
        # Фильтруем только доступные
        sizes = [s for s in sizes if s.get("available", False)]
        if len(sizes) != original_len:
            was_filtered_or_truncated = True

    # Если после фильтрации всё ещё больше 10 — берём первые 10
    if len(sizes) > limit:
        sizes = sizes[:limit]
        was_filtered_or_truncated = True

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
        
    note = "больше размеров в источнике" if was_filtered_or_truncated else ''

    return ", ".join([*parts, note])


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
            CommandHandler("last", self.handle_last_products),
            CommandHandler("start", self.start),
            CommandHandler("search", self.handle_search_command, has_args=True),
            CommandHandler("popular", self.handle_popular_command),
            CommandHandler("top_brand", self.handle_topbrand_command),
            CommandHandler("top_category", self.handle_topcategory_command),
        ]

    async def wb_image_url_get(self, context, card_id):
        """Максимально простой вариант: строим URL, пробуем webp, потом jpg."""
        REQUEST_TIMEOUT = 30
        base_url = Se.construct_host_v2(card_id, 'nm')
        
        for ext in ["webp", "jpg"]:
            image_url = f"{base_url}/images/big/1.{ext}"
            img_response = await wb_fetch_image(image_url, timeout=REQUEST_TIMEOUT)
            
            if img_response.status_code == 200:
                picture_chat_id = (await BotSettings.get_active()).picture_chat_id
                sent_photo = await context.bot.send_photo(picture_chat_id, img_response.content)
                return sent_photo.photo[-1].file_id
        
        return None
    
    async def wb(self, card_id, context: CallbackContext, update: Update = None):
        """
        Фаза 1: Строим URL картинки, загружаем в Telegram, возвращаем данные для медиагруппы.
        Не отправляем пользователю напрямую — отправка происходит в _handle_links_background.
        """
        # Строим URL картинки напрямую
        image_url = Se.construct_host_v2(card_id, 'nm') + "/images/big/1.webp"
        
        # Пробуем загрузить картинку (без прокси)
        img_response = await wb_fetch_image(image_url, timeout=30)
        
        # Если webp не сработал — пробуем jpg
        if img_response.status_code != 200:
            image_url = Se.construct_host_v2(card_id, 'nm') + "/images/big/1.jpg"
            img_response = await wb_fetch_image(image_url, timeout=30)
        
        if img_response.status_code != 200:
            logger.error(f"Не удалось загрузить картинку для {card_id}")
            return None
        
        # Загружаем на сервер Telegram
        picture_chat_id = (await BotSettings.get_active()).picture_chat_id
        sent_photo = await context.bot.send_photo(picture_chat_id, img_response.content)
        file_id = sent_photo.photo[-1].file_id
        
        # Базовая подпись для медиагруппы
        base_caption = (
            f"📦 Арт. {card_id}\n"
            f"🔗 <a href='https://wildberries.ru/catalog/{card_id}/detail.aspx'>Ссылка на товар</a>"
        )
        
        # Возвращаем структуру для медиагруппы + данные для фонового обогащения
        return {
            "sku": card_id,
            "media": file_id,
            "parse_mode": "HTML",
            "name": f"WB-{card_id}",
            "caption": base_caption,  # для медиагруппы
            "caption_data": {
                "sku": card_id,
                "name": f"WB-{card_id}",
                "link": f"https://wildberries.ru/catalog/{card_id}/detail.aspx",
                "sizes": [],
                "availability": True,
            },
            "brand": None,
            "category": None,
        }

    async def _wb_enrich_background(
        self,
        card_id: str,
        file_id: str,
        message_id: int,
        chat_id,
        context: CallbackContext,
    ):
        """
        Фоновая задача: достаём данные через API WB и обновляем сообщение в группе.
        """
        card_url = f"https://card.wb.ru/cards/v4/detail?curr=rub&dest=-1059500,-72639,-3826860,-5551776&nm={card_id}"
        
        try:
            response = await wb_fetch_with_session(card_url)
            
            # Если статус не 200 или тело пустое — выходим
            if response.status_code != 200 or not response.content:
                return
            
            # Пробуем распарсить JSON с защитой
            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.warning(f"WB API вернул не-JSON для {card_id}: {response.text[:200]}")
                return
            
            # Если нет продуктов — просто выходим
            if not data.get("products") or len(data["products"]) == 0:
                return
            
            product = data["products"][0]
            
            # Парсим данные
            brand = product.get("brand", "")
            name = product.get("name", f"WB-{card_id}")
            link = f"https://wildberries.ru/catalog/{card_id}/detail.aspx"
            
            sizes = []
            for size in product.get("sizes", []):
                size_name = size.get("name", "")
                available = len(size.get("stocks", [])) > 0
                obj = {
                    "name": size_name,
                    "available": available,
                }
                price_data = size.get("price")
                if price_data:
                    current_price = price_data.get("product")
                    if current_price is None:
                        current_price = price_data.get("basic", 0)
                    price_rub = current_price / 100
                    obj["price"] = price_rub
                sizes.append(obj)
            
            availability = any(s["available"] for s in sizes)
            
            caption_data = {
                "sku": card_id,
                "name": name,
                "link": link,
                "sizes": sizes,
                "availability": availability,
            }
            if brand:
                caption_data["brand"] = brand
            
            # Сохраняем в БД (без привязки к пользователю — это делает _handle_links_background)
            # Рендерим подпись через временный объект
            # Создаём минимальный продукт для render_product_caption
            from dataclasses import dataclass
            
            # Используем существующий продукт из БД или создаём временный
            try:
                product_obj = await ParseProduct.objects.aget(product_id=card_id)
                product_obj.caption_data = caption_data
                product_obj.name = name
            except ParseProduct.DoesNotExist:
                # Временный объект — не сохраняем, только для рендера
                product_obj = ParseProduct(
                    product_id=card_id,
                    name=name,
                    caption_data=caption_data,
                    product_type="wb",
                )
            
            # Получаем шаблон
            default_template = await ProductTemplate.aget_default_template()
            if not default_template:
                default_template = default_caption_template
            
            settings = await BotSettings.get_active()
            chat_instance = None
            try:
                chat_instance = await context.bot.get_chat(settings.marketing_group_id)
            except:
                pass
            
            rendered_caption = await self.render_product_caption(
                product_obj,
                default_template,
                context,
                chat_instance.link if chat_instance else None,
            )
            
            # Редактируем сообщение в группе
            try:
                await context.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=rendered_caption,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"Не удалось отредактировать сообщение {message_id}: {e}")
            
        except Exception as e:
            logger.error(f"Error in _wb_enrich_background for {card_id}: {e}", exc_info=True)
            # При ошибке базовая подпись остаётся как есть
    
    async def get_or_update_product_data(
        self,
        p: dict,
        product_type: str,
        user: "TgUser",
        item_id: str,
        context: CallbackContext = None,  # опционально, если понадобится позже
    ) -> tuple["ParseProduct", bool]:
        """
        Создаёт или обновляет товар, бренд, категорию, изображение и связь с пользователем.
        Возвращает кортеж: (product, created)
        """

        # --- 1. Очистка дубликатов по product_id ---
        existing_products = ParseProduct.objects.filter(product_id=item_id).order_by(
            "-created_at"
        )
        existing_count = await existing_products.acount()
        if existing_count > 1:
            async for product in existing_products[1:]:
                await ParseProduct.objects.filter(id=product.id).adelete()

        # --- 2. Получение/создание бренда ---
        brand_obj = None
        brand_data = p.get("brand")
        if brand_data:
            brand_id = brand_data.get("id")
            brand_name = brand_data.get("name")
            if brand_id and brand_name:
                try:
                    brand_obj, _ = await Brand.objects.aupdate_or_create(
                        brand_id=str(brand_id),
                        product_type=product_type,
                        defaults={"name": brand_name},
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка при создании бренда {brand_name} ({brand_id}): {e}"
                    )

        # --- 3. Получение/создание категории ---
        category_obj = None
        category_data = p.get("category")
        if category_data:
            category_id = category_data.get("id")
            category_name = category_data.get("name")
            if category_id and category_name:
                try:
                    category_obj, _ = await Category.objects.aupdate_or_create(
                        subject_id=int(category_id),
                        product_type=product_type,
                        defaults={"name": category_name},
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка при создании категории {category_name} ({category_id}): {e}"
                    )

        # --- 4. Получение/создание товара ---
        sku = p.get("sku") or item_id
        name = p.get("name", "Без названия")

        # --- 4.1. Обновление sku ozon ---
        if product_type == "ozon":
            try:
                # Ищем товар по старому item_id (если он ещё не был обновлён)
                old_ozon_product = await ParseProduct.objects.aget(
                    product_type=product_type, product_id=item_id
                )
                # Если нашли — обновляем его product_id на sku
                if old_ozon_product.product_id != sku:
                    old_ozon_product.product_id = sku
                    await old_ozon_product.asave(update_fields=["product_id"])
                    logger.info(f"Ozon: обновлён product_id с {item_id} на {sku}")

                    # logger.info(await ParseProduct.objects.filter(product_id=sku).acount())
            except ParseProduct.DoesNotExist:
                # Ничего страшного — просто такого товара ещё не было
                pass
            except Exception as e:
                logger.error(f"Ошибка при обновлении Ozon product_id: {e}")

        product, product_created = await ParseProduct.objects.aupdate_or_create(
            product_id=sku,
            name=name,
            defaults={
                "brand": brand_obj,
                "category": category_obj,
                "caption_data": p["caption_data"],
                "product_type": product_type,
            },
        )

        # --- 5. Обновление, если данные изменились ---
        need_update = False

        # logger.info('UPDATE!!!!')

        # Обновляем caption_data, если изменился
        if product.caption_data != p["caption_data"]:
            product.caption_data = p["caption_data"]
            need_update = True

        # Обновляем бренд, если изменился
        if brand_obj and product.brand_id != brand_obj.id:
            product.brand = brand_obj
            need_update = True

        # Обновляем категорию, если изменилась
        if category_obj and product.category_id != category_obj.id:
            product.category = category_obj
            need_update = True

        if need_update:
            # Важно: обновляем ВСЕ изменённые поля
            update_fields = ["brand", "category", "caption_data"]
            if not product_created:
                await product.asave(update_fields=update_fields)
            else:
                # При создании сохранять не нужно — уже сохранён
                pass

        # --- 6. Работа с изображением ---
        image_qs = product.images.all()
        product_image = await image_qs.afirst()

        media = p["media"]
        media_type = {"image_type": "", "file_id": None, "url": None}

        if media.lower().startswith(("http://", "https://")):
            media_type["image_type"] = "link"
            media_type["url"] = media
        else:
            media_type["image_type"] = "telegram"
            media_type["file_id"] = media

        if product_image is None:
            # Создаём новое изображение
            product_image = await ProductImage.objects.acreate(
                product=product, **media_type
            )
        else:
            # Обновляем, если что-то изменилось
            if (
                product_image.image_type != media_type["image_type"]
                or (product_image.file_id or "") != (media_type["file_id"] or "")
                or (product_image.url or "") != (media_type["url"] or "")
            ):
                product_image.image_type = media_type["image_type"]
                product_image.file_id = media_type["file_id"]
                product_image.url = media_type["url"]
                await product_image.asave()

        # --- 7. Создание связи пользователь-товар ---
        await TgUserProduct.objects.acreate(tg_user=user, product=product)

        # --- 8. обновление товара из базы ---
        await product.arefresh_from_db(
            from_queryset=ParseProduct.objects.select_related("brand", "category")
        )
        await product_image.arefresh_from_db()

        return product, product_image

    async def render_product_caption(
        self,
        product: "ParseProduct",
        template: str,
        bot_context: CallbackContext = None,
        marketing_chat_link: str = None,
    ) -> str:
        hash_prefix = "bot_"
        caption_data = product.caption_data or {}

        # Базовые поля
        template_context = {
            "sku": caption_data.get("sku", "N/A"),
            "brand": caption_data.get("brand", "Неизвестный бренд"),
            "name": caption_data.get("name", "Без названия"),
            "link": caption_data.get("link", "#"),
            "hash_type": f"#{hash_prefix}{product.product_type}",
            "hash_category": "",
            "hash_brand": "",
        }

        # Хэштеги категории и бренда (если есть в caption_data или связях)
        if product.category and product.category.name:
            clean_name = re.sub(r"\W+", "_", product.category.name.lower())
            template_context["hash_category"] = f"#{hash_prefix}{clean_name}"

        if product.brand and product.brand.name:
            clean_name = re.sub(r"\W+", "_", product.brand.name.lower())
            template_context["hash_brand"] = f"#{hash_prefix}{clean_name}"

        # Промо-ссылки
        promo_parts = []
        if (
            bot_context
            and bot_context.bot
            and bot_context.bot.username
            and bot_context.bot.link
        ):
            promo_parts.append(
                f"🤖 <a href='{bot_context.bot.link}'>@{bot_context.bot.username}</a>"
            )

        if marketing_chat_link:
            promo_parts.append(
                f"💬 <a href='{marketing_chat_link}'>Группа поддержки</a>"
            )

        template_context["promo"] = " | ".join(promo_parts) if promo_parts else ""

        # Цены и размеры
        sizes = caption_data.get("sizes", [])
        active_prices = [
            s.get("price")
            for s in sizes
            if s.get("price") is not None and s.get("price") > 0
        ]
        show_common_price = len(set(active_prices)) == 1 if active_prices else False

        if active_prices:
            min_price = min(active_prices)
            max_price = max(active_prices)
            if show_common_price:
                template_context["price_display"] = (
                    f"{min_price:,.0f}".replace(",", " ") + " ₽"
                )
            else:
                template_context["price_display"] = (
                    f"{min_price:,.0f} – {max_price:,.0f}".replace(",", " ") + " ₽"
                )
        else:
            template_context["price_display"] = "—"

        template_context["sizes_display"] = format_sizes_for_template(
            sizes, show_common_price
        )
        template_context["availability_display"] = (
            "✅ В наличии" if caption_data.get("availability") else "❌ Нет в наличии"
        )

        return render_template(template, template_context)

    async def handle_links_based_on_message(
        self, update: Update, context: CallbackContext
    ):
        if not update.effective_message:
            return
        message_text = update.effective_message.caption or update.effective_message.text

        # Ищем ссылки Wildberries
        wb_matches = re.findall(wb_regexp, message_text)
        wb_items = [match[1] or match[2] for match in wb_matches]

        # Ищем ссылки Ozon
        ozon_matches = re.findall(ozon_regexp, message_text)

        total = len(wb_items) + len(ozon_matches)
        if total == 0:
            return

        # Мгновенно возвращаем управление боту, тяжёлая работа — в фоне
        if wb_items:
            asyncio.create_task(
                self._handle_links_background(wb_items, "wb", self.wb, update, context)
            )
        if ozon_matches:
            asyncio.create_task(
                self._handle_links_background(ozon_matches, "ozon", self.parse_ozon, update, context)
            )

        logger.info(
            f"🔍 Найдено {len(wb_items)} WB + {len(ozon_matches)} Ozon. "
            f"Парсинг запущен в фоне, результаты придут отдельными сообщениями."
        )
    
    async def _handle_links_background(
        self, items, product_type, parse_func, update: Update, context: CallbackContext
    ):
        """
        Фоновая задача парсинга и отправки товаров.
        Для WB: собираем медиагруппу с базовыми подписями, отправляем, потом обновляем.
        Для Ozon: старая логика.
        """
        # === WB: медиагруппа + фоновое обогащение ===
        if product_type == "wb":
            pictures = []
            enrich_tasks = []  # (card_id, file_id, future_message_index)
            
            for card_id in items:
                try:
                    p = await self.wb(card_id, context, update)
                    if p is None:
                        logger.warning(f"WB вернул None для {card_id}, пропускаем")
                        continue
                    
                    pictures.append({
                        "media": p["media"],
                        "caption": p["caption"],
                        "parse_mode": p["parse_mode"],
                    })
                    # Запоминаем для фонового обогащения
                    enrich_tasks.append({
                        "card_id": card_id,
                        "file_id": p["media"],
                    })
                except Exception as e:
                    logger.error(f"Ошибка WB товара {card_id}: {e}", exc_info=True)
                    continue
            
            if not pictures:
                return
            
            # Отправляем медиагруппу
            media_group = [InputMediaPhoto(**photo) for photo in pictures]
            sent_messages = []
            
            try:
                sent_messages = await update.message.reply_media_group(
                    media=media_group,
                    reply_to_message_id=update.message.message_id,
                )
            except Exception as e:
                logger.error(f"Ошибка отправки медиагруппы WB: {e}", exc_info=True)
                # Fallback: отправляем по одному
                for photo in pictures:
                    try:
                        msg = await update.message.reply_photo(
                            photo=photo["media"],
                            caption=photo["caption"],
                            parse_mode=photo["parse_mode"],
                        )
                        sent_messages.append(msg)
                    except Exception as e2:
                        logger.error(f"Fallback ошибка: {e2}")
                        continue
            
            # Запускаем фоновое обогащение для каждого сообщения
            chat_id = update.effective_chat.id
            for idx, task in enumerate(enrich_tasks):
                if idx < len(sent_messages):
                    asyncio.create_task(
                        self._wb_enrich_background(
                            task["card_id"],
                            task["file_id"],
                            sent_messages[idx].message_id,
                            chat_id,
                            context,
                        )
                    )
            
            # Сохраняем в БД (пользователь, продукт)
            user_obj = update.effective_user
            user, _ = await TgUser.objects.aget_or_create(
                tg_id=user_obj.id,
                defaults={
                    "username": user_obj.username,
                    "first_name": user_obj.first_name,
                    "last_name": user_obj.last_name,
                    "language_code": user_obj.language_code,
                    "is_bot": user_obj.is_bot,
                },
            )
            
            for idx, task in enumerate(enrich_tasks):
                if idx < len(sent_messages):
                    # Сохраняем базовый продукт (без данных API)
                    p = {
                        "sku": task["card_id"],
                        "media": task["file_id"],
                        "parse_mode": "HTML",
                        "name": f"WB-{task['card_id']}",
                        "caption_data": {
                            "sku": task["card_id"],
                            "name": f"WB-{task['card_id']}",
                            "link": f"https://wildberries.ru/catalog/{task['card_id']}/detail.aspx",
                            "sizes": [],
                            "availability": True,
                        },
                        "brand": None,
                        "category": None,
                    }
                    try:
                        await self.get_or_update_product_data(
                            p, "wb", user, task["card_id"], context
                        )
                    except Exception as e:
                        logger.error(f"Ошибка сохранения {task['card_id']}: {e}")
            
            return

        # === Ozon: старая логика ===
        user_obj = update.effective_user
        user, created = await TgUser.objects.aget_or_create(
            tg_id=update.effective_user.id,
            defaults={
                "username": user_obj.username,
                "first_name": user_obj.first_name,
                "last_name": user_obj.last_name,
                "language_code": user_obj.language_code,
                "is_bot": user_obj.is_bot,
            },
        )

        pictures = []
        default_template = await ProductTemplate.aget_default_template()
        if not default_template:
            default_template = default_caption_template

        settings = await BotSettings.get_active()
        chat_instance = None
        try:
            chat_instance = await context.bot.get_chat(settings.marketing_group_id)
        except:
            logger.info("Не найден маркетинговый чат")

        for i in items:
            try:
                p = await parse_func(i, context)
                if p is None:
                    logger.warning(
                        f"Парсинг {product_type} вернул None для товара {i}, пропускаем"
                    )
                    continue

                product, product_image = await self.get_or_update_product_data(
                    p,
                    product_type,
                    user,
                    i,
                    context,
                )
                rendered_caption = await self.render_product_caption(
                    product,
                    default_template,
                    context,
                    (None if not chat_instance else chat_instance.link),
                )

                pictures.append(
                    {
                        "media": (
                            product_image.url
                            if product_image.image_type == "link"
                            else product_image.file_id
                        ),
                        "caption": rendered_caption,
                        "parse_mode": p["parse_mode"],
                    }
                )
            except Exception as e:
                logger.error(f"Ошибка при обработке товара {i}: {e}", exc_info=True)
                continue

        for i in range(0, len(pictures), 10):
            group = pictures[i : i + 10]
            media_group = [
                InputMediaPhoto(**photo) for photo in group if photo is not None
            ]
            logger.info(media_group)
            if len(media_group):
                try:
                    await update.message.reply_media_group(
                        media=media_group, reply_to_message_id=update.message.message_id
                    )
                except Exception as e:
                    try:
                        await update.message.reply_media_group(media=media_group)
                    except Exception as e:
                        logger.info(media_group)
                        logger.error("Ошибка отправки медиагруппы", exc_info=True)

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
        payload = {"cmd": "request.get", "maxTimeout": 120000, "url": url}
        response = requests.post(
            parser_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=120,
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
            img = None
            sku = None
            product_name = None
            price = None
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
                img = out_of_stock.get("coverImage")
            elif price or sale:
                img = gallery.get("coverImage") or heading.get("coverImage")
            elif fulltext_results_header:
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
                    for variant in aspect.get("variants", []):
                        size_name = ""
                        # Извлекаем название размера
                        text_rs = variant.get("data", {}).get("textRs", [])
                        if text_rs and isinstance(text_rs, list) and len(text_rs) > 0:
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
            elif price:
                sizes.append(
                    {
                        "name": "Единый",
                        "available": True,
                        "price": parse_price_string(price.get("cardPrice")),
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

            if brand and "title" in brand.get("content", {}):
                caption_data["brand"] = brand["content"]["title"]["text"][0]["content"]

            result = {
                "sku": sku,
                "name": product_name,
                "media": img,
                "caption_data": caption_data,
                "parse_mode": "HTML",
            }

            if brand and "title" in brand.get("content", {}):
                try:
                    result["brand"] = {
                        "id": brand["link"].split("/")[1],
                        "name": brand["content"]["title"]["text"][0]["content"],
                    }
                except Exception as e:
                    logger.info(e, exc_info=True)

            if r.get("layoutTrackingInfo"):
                try:
                    track = json.loads(r.get("layoutTrackingInfo"))
                    result["category"] = {
                        "id": track["categoryId"],
                        "name": track["categoryName"],
                    }
                except Exception as e:
                    logger.info(e, exc_info=True)
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
            user = await TgUser.objects.aget(tg_id=update.effective_user.id)

            # Получаем шаблон и ссылку на чат поддержки один раз
            default_template = await ProductTemplate.aget_default_template()
            if not default_template:
                default_template = default_caption_template

            marketing_chat_link = None
            settings = await BotSettings.get_active()
            if settings and settings.marketing_group_id:
                try:
                    chat_instance = await context.bot.get_chat(
                        settings.marketing_group_id
                    )
                    marketing_chat_link = chat_instance.link
                except Exception as e:
                    logger.warning(f"Не удалось получить ссылку на чат поддержки: {e}")

            # Извлекаем последние 10 товаров, отсортированных по дате (sent_at)
            latest_id_per_product = (
                TgUserProduct.objects.filter(
                    tg_user=user, product_id=OuterRef("product_id")
                )
                .order_by("-sent_at")
                .values("id")[:1]
            )
            user_products = []
            async for item in (
                TgUserProduct.objects.filter(
                    tg_user=user, id__in=Subquery(latest_id_per_product)
                )
                .select_related("product__brand", "product__category")
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

                # 🔗 Получаем изображение (с подгрузкой, если нужно)
                product_image = await product.images.afirst()
                if not product_image:
                    logger.warning(
                        f"У товара {product.id} нет изображения, пропускаем."
                    )
                    continue

                # 🖋️ Генерируем актуальную подпись через нашу функцию
                try:
                    caption = await self.render_product_caption(
                        product=product,
                        template=default_template,
                        bot_context=context,
                        marketing_chat_link=marketing_chat_link,
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка генерации подписи для товара {product.id}: {e}"
                    )
                    caption = "Описание недоступно"

                # 📸 Определяем тип медиа
                media_value = (
                    product_image.url
                    if product_image.image_type == "link"
                    else product_image.file_id
                )

                if not media_value:
                    logger.warning(
                        f"У товара {product.id} нет валидного media, пропускаем."
                    )
                    continue

                # ➕ Добавляем в медиа-группу
                media_group.append(
                    InputMediaPhoto(
                        media=media_value,
                        caption=caption,
                        parse_mode="HTML",
                    )
                )

            if not media_group:
                await update.message.reply_text(
                    "Нет товаров с изображениями для отображения."
                )
                return

            # 📤 Отправляем группу
            await update.message.reply_media_group(
                media=media_group, reply_to_message_id=update.message.message_id
            )
        except Exception as e:
            logger.error(e)

    async def handle_search_command(self, update: Update, context: CallbackContext):
        try:
            # Получаем текст запроса
            parts = update.message.text.split(maxsplit=1)
            if len(parts) < 2:
                await update.message.reply_text(
                    "Пожалуйста, укажите запрос для поиска."
                )
                return

            query = parts[1].strip()[:50]
            if not query:
                await update.message.reply_text(
                    "Пожалуйста, укажите запрос для поиска."
                )
                return

            # Получаем шаблон и ссылку на чат поддержки один раз
            default_template = await ProductTemplate.aget_default_template()
            if not default_template:
                default_template = default_caption_template

            marketing_chat_link = None
            settings = await BotSettings.get_active()
            if settings and settings.marketing_group_id:
                try:
                    chat_instance = await context.bot.get_chat(
                        settings.marketing_group_id
                    )
                    marketing_chat_link = chat_instance.link
                except Exception as e:
                    logger.warning(f"Не удалось получить ссылку на чат поддержки: {e}")

            # --- 🔍 Поиск по name, brand.name, category.name ---
            results = (
                ParseProduct.objects.filter(
                    Q(name__icontains=query)
                    | Q(brand__name__icontains=query)
                    | Q(category__name__icontains=query)
                )
                .select_related("brand", "category")  # предзагружаем связи
                .order_by("-created_at")[:10]  # последние 10
            )

            user_products = []
            async for product in results:
                user_products.append(product)

            if not user_products:
                await update.message.reply_text(
                    f"По запросу '{query}' ничего не найдено."
                )
                return

            # --- 🖼️ Формируем медиа-группу ---
            media_group = []

            for product in user_products:
                # Получаем изображение
                product_image = await product.images.afirst()
                if not product_image:
                    logger.warning(
                        f"У товара {product.id} нет изображения, пропускаем."
                    )
                    continue

                # Генерируем подпись
                try:
                    caption = await self.render_product_caption(
                        product=product,
                        template=default_template,
                        bot_context=context,
                        marketing_chat_link=marketing_chat_link,
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка генерации подписи для товара {product.id}: {e}"
                    )
                    caption = "Описание недоступно"

                # Определяем медиа
                media_value = (
                    product_image.url
                    if product_image.image_type == "link"
                    else product_image.file_id
                )
                if not media_value:
                    logger.warning(
                        f"У товара {product.id} нет валидного media, пропускаем."
                    )
                    continue

                # Добавляем в группу
                media_group.append(
                    InputMediaPhoto(
                        media=media_value,
                        caption=caption,
                        parse_mode="HTML",
                    )
                )

            if not media_group:
                await update.message.reply_text(
                    "Нет товаров с изображениями для отображения."
                )
                return

            # --- 📤 Отправляем ---
            await update.message.reply_media_group(
                media=media_group, reply_to_message_id=update.message.message_id
            )

        except IndexError:
            await update.message.reply_text("Пожалуйста, укажите запрос для поиска.")
        except Exception as e:
            logger.error("Ошибка при выполнении команды /search", exc_info=True)
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
        if "top_products" not in items:
            logger.info("no topcategory products")
            return
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
        if "top_products" not in items:
            logger.info("no topbrand products")
            return
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

            if settings.marketing_group_id:
                try:
                    chat_instance = await context.bot.get_chat(
                        settings.marketing_group_id
                    )
                    marketing_chat_link = chat_instance.link
                except Exception as e:
                    logger.warning(f"Не удалось получить ссылку на чат поддержки: {e}")

            # Формируем сообщение
            message = msg_obj.text.strip().replace("\\n", "\n")

            # Отправляем в маркетинговую группу
            logger.info(f"attempt to send msg to {target_chat_id}")
            await context.bot.send_message(
                chat_id=target_chat_id, text=message, parse_mode="HTML"
            )
            # Формируем медиагруппу
            media_group = []
            for item in items:
                try:
                    product = await ParseProduct.objects.prefetch_related(
                        "brand", "category"
                    ).aget(id=item["id"])
                    image = await ProductImage.objects.filter(product=product).afirst()

                    if not image.media_data:
                        continue

                    caption = await self.render_product_caption(
                        product=product,
                        template=msg_obj.product_template or default_caption_template,
                        bot_context=context,
                        marketing_chat_link=marketing_chat_link,
                    )

                    media_group.append(
                        InputMediaPhoto(
                            media=image.media_data,
                            caption=caption,
                            parse_mode="HTML",
                        )
                    )
                except ParseProduct.DoesNotExist:
                    continue

            # Отправляем в маркетинговую группу
            if media_group:
                try:
                    logger.info(media_group)
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
