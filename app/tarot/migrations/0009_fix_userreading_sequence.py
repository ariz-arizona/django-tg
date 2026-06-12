from django.db import migrations


def fix_sequence(apps, schema_editor):
    # Проверяем, что мы работаем с PostgreSQL
    if schema_editor.connection.vendor == 'postgresql':
        with schema_editor.connection.cursor() as cursor:
            # tarot_userreading — имя таблицы в БД
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence('tarot_userreading', 'id'), COALESCE(max(id), 1)) FROM tarot_userreading;"
            )

class Migration(migrations.Migration):

    dependencies = [
        ('tarot', '0008_remove_userreading_reading_format_userreading_count_and_more'), # Укажи имя миграции с переносом данных
    ]

    operations = [
        migrations.RunPython(fix_sequence, reverse_code=migrations.RunPython.noop),
    ]