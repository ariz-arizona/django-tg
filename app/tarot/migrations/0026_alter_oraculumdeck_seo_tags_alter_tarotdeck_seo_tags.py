from django.db import migrations, models
import django.contrib.postgres.fields


def text_to_array(apps, schema_editor):
    TarotDeck = apps.get_model('tarot', 'TarotDeck')
    OraculumDeck = apps.get_model('tarot', 'OraculumDeck')

    for model_cls in [TarotDeck, OraculumDeck]:
        for obj in model_cls.objects.using(schema_editor.connection.alias).all():
            raw = obj.seo_tags_old
            if raw and isinstance(raw, str):
                tags = [t.strip() for t in raw.split(',') if t.strip()]
            else:
                tags = []
            obj.seo_tags = tags
            obj.save(update_fields=['seo_tags'])


class Migration(migrations.Migration):
    dependencies = [
        ('tarot', '0025_oraculumdeck_is_active_tarotdeck_is_active'),  # ЗАМЕНИ НА СВОЮ
    ]

    operations = [
        # 0. Создаём вспомогательную функцию
        migrations.RunSQL(
            sql="""
                CREATE OR REPLACE FUNCTION immutable_array_to_string(arr text[])
                RETURNS text
                LANGUAGE sql
                IMMUTABLE
                PARALLEL SAFE
                AS $$ SELECT array_to_string(arr, ' '); $$;
            """,
            reverse_sql="DROP FUNCTION IF EXISTS immutable_array_to_string(text[]);"
        ),

        # 1. Переименовываем старое поле
        migrations.RenameField(
            model_name='tarotdeck',
            old_name='seo_tags',
            new_name='seo_tags_old',
        ),
        migrations.RenameField(
            model_name='oraculumdeck',
            old_name='seo_tags',
            new_name='seo_tags_old',
        ),

        # 2. Удаляем старые индексы
        migrations.RemoveIndex(
            model_name='tarotdeck',
            name='tarotdeck_seo_tags_trgm_idx',
        ),
        migrations.RemoveIndex(
            model_name='oraculumdeck',
            name='oraculumdeck_seo_tags_trgm_idx',
        ),

        # 3. Создаём новое ArrayField
        migrations.AddField(
            model_name='tarotdeck',
            name='seo_tags',
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=255),
                null=True,
                blank=True,
                default=list,
                verbose_name='SEO-теги',
            ),
        ),
        migrations.AddField(
            model_name='oraculumdeck',
            name='seo_tags',
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=255),
                null=True,
                blank=True,
                default=list,
                verbose_name='SEO-теги',
            ),
        ),

        # 4. Переносим данные: строка → массив
        migrations.RunPython(text_to_array),

        # 5. Удаляем старое поле
        migrations.RemoveField(
            model_name='tarotdeck',
            name='seo_tags_old',
        ),
        migrations.RemoveField(
            model_name='oraculumdeck',
            name='seo_tags_old',
        ),

        # 6. Создаём новые индексы
        migrations.RunSQL(
            sql="""
                CREATE INDEX tarotdeck_seo_tags_gin_idx 
                ON tarot_tarotdeck 
                USING gin (seo_tags);
                
                CREATE INDEX tarotdeck_seo_tags_trgm_idx 
                ON tarot_tarotdeck 
                USING gin (immutable_array_to_string(seo_tags) gin_trgm_ops);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS tarotdeck_seo_tags_gin_idx;
                DROP INDEX IF EXISTS tarotdeck_seo_tags_trgm_idx;
            """
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX oraculumdeck_seo_tags_gin_idx 
                ON tarot_oraculumdeck 
                USING gin (seo_tags);
                
                CREATE INDEX oraculumdeck_seo_tags_trgm_idx 
                ON tarot_oraculumdeck 
                USING gin (immutable_array_to_string(seo_tags) gin_trgm_ops);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS oraculumdeck_seo_tags_gin_idx;
                DROP INDEX IF EXISTS oraculumdeck_seo_tags_trgm_idx;
            """
        ),
    ]