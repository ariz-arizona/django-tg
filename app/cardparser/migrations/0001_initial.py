# cardparser/migrations/0001_initial.py

import django.db.models.deletion
from django.db import migrations, models
from django.db.migrations.operations.special import RunSQL


class Migration(migrations.Migration):

    initial = True
    
    dependencies = [
        ('tg_bot', '0030_parseproduct_name'),  # Убедись, что выполнена
    ]

    operations = [
        # Шаг 1: Физически переименовываем таблицы в БД
        RunSQL("ALTER TABLE tg_bot_brand RENAME TO cardparser_brand;"),
        RunSQL("ALTER TABLE tg_bot_category RENAME TO cardparser_category;"),
        RunSQL("ALTER TABLE tg_bot_parseproduct RENAME TO cardparser_parseproduct;"),
        RunSQL("ALTER TABLE tg_bot_productimage RENAME TO cardparser_productimage;"),
        RunSQL("ALTER TABLE tg_bot_tguserproduct RENAME TO cardparser_tguserproduct;"),

        # Шаг 2: Говорим Django, что модели теперь в cardparser, но НЕ создаем таблицы
        migrations.SeparateDatabaseAndState(
            database_operations=[],  # БД уже изменена выше
            state_operations=[
                migrations.CreateModel(
                    name='Brand',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('name', models.CharField(max_length=255, verbose_name='Название бренда')),
                        ('brand_id', models.CharField(max_length=50, verbose_name='Внешний ID бренда')),
                        ('product_type', models.CharField(choices=[('ozon', 'Ozon'), ('wb', 'Wildberries')], max_length=10, verbose_name='Тип площадки')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')),
                    ],
                    options={
                        'verbose_name': 'Card Parser: Бренд',
                        'verbose_name_plural': 'Card Parser: Бренды',
                        'ordering': ['name'],
                        'unique_together': {('brand_id', 'product_type')},
                    },
                ),
                migrations.CreateModel(
                    name='Category',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('name', models.CharField(max_length=255, verbose_name='Название категории')),
                        ('subject_id', models.IntegerField(verbose_name='subjectId / category_id')),
                        ('product_type', models.CharField(choices=[('ozon', 'Ozon'), ('wb', 'Wildberries')], max_length=10, verbose_name='Тип площадки')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')),
                    ],
                    options={
                        'verbose_name': 'Card Parser: Категория',
                        'verbose_name_plural': 'Card Parser: Категории',
                        'ordering': ['name'],
                        'unique_together': {('subject_id', 'product_type')},
                    },
                ),
                migrations.CreateModel(
                    name='ParseProduct',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('product_id', models.CharField(max_length=255, verbose_name='ID товара')),
                        ('caption', models.TextField(verbose_name='Подпись к фото')),
                        ('name', models.CharField(blank=True, help_text='Официальное название товара (опционально)', max_length=255, null=True, verbose_name='Название товара')),
                        ('product_type', models.CharField(choices=[('ozon', 'Ozon'), ('wb', 'Wildberries')], max_length=10, verbose_name='Тип продукта')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')),
                        ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Дата обновления')),
                        ('brand', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='cardparser.brand', verbose_name='Бренд')),
                        ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='cardparser.category', verbose_name='Категория')),
                    ],
                    options={
                        'verbose_name': 'Card Parser: Продукт',
                        'verbose_name_plural': 'Card Parser: Продукты',
                    },
                ),
                migrations.CreateModel(
                    name='ProductImage',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('image_type', models.CharField(choices=[('telegram', 'Telegram file_id'), ('link', 'Прямая ссылка')], max_length=10, verbose_name='Тип изображения')),
                        ('file_id', models.CharField(blank=True, max_length=500, null=True, verbose_name='ID изображения в Telegram')),
                        ('url', models.URLField(blank=True, max_length=1000, null=True, verbose_name='Прямая ссылка на изображение')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')),
                        ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Дата обновления')),
                        ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='images', to='cardparser.parseproduct', verbose_name='Товар')),
                    ],
                    options={
                        'verbose_name': 'Card Parser: Изображение товара',
                        'verbose_name_plural': 'Card Parser: Изображения товаров',
                        'ordering': ['-created_at'],
                    },
                ),
                migrations.CreateModel(
                    name='TgUserProduct',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('sent_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата отправки')),
                        ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='product_users', to='cardparser.parseproduct', verbose_name='Продукт')),
                        ('tg_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_products', to='tg_bot.tguser', verbose_name='Пользователь')),
                    ],
                    options={
                        'verbose_name': 'Card Parser: Продукт пользователя',
                        'verbose_name_plural': 'Card Parser: Продукты пользователей',
                    },
                ),
            ],
        ),
    ]