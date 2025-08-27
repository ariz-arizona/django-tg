# services/popular.py

from datetime import timedelta
from django.utils import timezone
from django.db.models import Count, Max
from cardparser.models import TgUserProduct, ParseProduct


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