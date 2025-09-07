# services/popular.py
from typing import Dict, Any, Optional, List
from datetime import timedelta
from django.utils import timezone
from django.db.models import Count, Max
from cardparser.models import TgUserProduct, ParseProduct, Brand

def get_brand_and_its_top_products(
    hours: int = 24,
    days: int = 0,
    exclude_category_ids: Optional[List[int]] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Возвращает самый активный бренд по количеству уникальных запрошенных товаров
    и топ-N самых запрашиваемых товаров этого бренда.

    :param hours: Часы для фильтрации (по умолчанию 24)
    :param days: Дни для фильтрации (по умолчанию 0)
    :param exclude_category_ids: Список ID категорий для исключения
    :param limit: Сколько топ-товаров вернуть (по умолчанию 5)
    :return: Словарь с ключами "brand" и "top_products" или пустой словарь
    """
    time_threshold = timezone.now() - timedelta(days=days, hours=hours)

    # 1. Уникальные product_id, запрошенные за период
    requested_product_ids = TgUserProduct.objects.filter(
        sent_at__gte=time_threshold
    ).values_list('product_id', flat=True).distinct()

    if not requested_product_ids:
        return {}

    # 2. Находим бренд с наибольшим количеством уникальных запрошенных товаров
    brand_stat = (
        ParseProduct.objects.filter(
            id__in=requested_product_ids,
            brand__isnull=False
        )
        .select_related('brand', 'category')
        .exclude(
            category_id__in=exclude_category_ids or []
        )
        .values('brand_id')
        .annotate(
            product_count=Count('id', distinct=True),
            last_sent=Max('product_users__sent_at')
        )
        .order_by('-product_count', '-last_sent')
        .first()
    )

    if not brand_stat:
        return {}

    # 3. Получаем объект бренда
    brand = Brand.objects.get(id=brand_stat['brand_id'])

    brand_data = {
        "id": brand.id,
        "name": brand.name,
        "product_type": brand.get_product_type_display(),
        "product_count": brand_stat["product_count"],
        "last_sent": brand_stat["last_sent"],
    }

    # 4. Получаем топ товаров ЭТОГО бренда по количеству запросов
    top_products_stats = (
        TgUserProduct.objects.filter(
            sent_at__gte=time_threshold,
            product_id__in=ParseProduct.objects.filter(brand_id=brand.id).values_list('id', flat=True)
        )
        .values('product_id')
        .annotate(
            request_count=Count('product_id'),
            last_sent=Max('sent_at')
        )
        .order_by('-request_count', '-last_sent')[:limit]
    )

    if not top_products_stats:
        return {"brand": brand_data, "top_products": []}

    # 5. Собираем product_id → статистика
    product_stats_map = {item['product_id']: item for item in top_products_stats}

    # 6. Получаем товары с деталями
    products = ParseProduct.objects.filter(
        id__in=product_stats_map.keys()
    ).select_related('brand', 'category')

    top_products = []
    for product in products:
        stat = product_stats_map[product.id]
        top_products.append({
            "id": product.id,
            "product_id": product.product_id,
            "name": product.name or "Без названия",
            "caption": product.caption,
            "product_type": product.get_product_type_display(),
            "brand": product.brand.name if product.brand else "—",
            "category": product.category.name if product.category else "—",
            "request_count": stat['request_count'],
            "last_sent": stat['last_sent'],
        })

    # 7. Сортируем, чтобы сохранить порядок из БД (на всякий случай)
    top_products.sort(key=lambda x: (-x['request_count'], -x['last_sent'].timestamp()))

    return {
        "brand": brand_data,
        "top_products": top_products[:limit]
    }

def get_popular_products(
    hours: int = 24,
    days: int = 0,
    limit: int = 10
):
    """
    Возвращает топ-товаров за период:
    - Сортирует по количеству запросов (desc)
    - При равном количестве — по времени последнего запроса (desc)
    Реализовано через SQL (annotate), чтобы не грузить Python.
    """
    time_threshold = timezone.now() - timedelta(days=days, hours=hours)

    # 1. Агрегируем в БД: count и last_sent
    stats = (
        TgUserProduct.objects
        .filter(sent_at__gte=time_threshold)
        .values('product_id')
        .annotate(
            request_count=Count('product_id'),
            last_sent=Max('sent_at')
        )
        .order_by('-request_count', '-last_sent')[:limit]
    )

    if not stats:
        return []

    # 2. Извлекаем статистику в словарь
    stats_map = {item['product_id']: item for item in stats}

    # 3. Получаем данные о товарах
    products = ParseProduct.objects.filter(id__in=stats_map.keys()).select_related(
        'brand', 'category'
    ).only(
        'id', 'product_id', 'name', 'caption', 'product_type',
        'brand__name', 'category__name'
    )

    # 4. Формируем результат с сохранением порядка
    result = []
    for product in products:
        stat = stats_map[product.id]
        result.append({
            "id": product.id,
            "product_id": product.product_id,
            "name": product.name or "Без названия",
            "caption": product.caption,
            "product_type": product.get_product_type_display(),
            "brand": product.brand.name if product.brand else "—",
            "category": product.category.name if product.category else "—",
            "request_count": stat['request_count'],
            "last_sent": stat['last_sent'],
        })

    # 5. Сортируем, чтобы сохранить порядок из БД
    result.sort(key=lambda x: (-x['request_count'], -x['last_sent'].timestamp()))

    return result[:limit]