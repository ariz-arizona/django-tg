from django.db import migrations, models
import django.db.models.deletion

def migrate_text_to_pages(apps, schema_editor):
    AIReadingInterpretation = apps.get_model('tarot', 'AIReadingInterpretation')
    AIReadingPage = apps.get_model('tarot', 'AIReadingPage')
    
    # Размер чанка (например, 1000 символов)
    CHUNK_SIZE = 1000
    
    for obj in AIReadingInterpretation.objects.all():
        text = obj.response_text
        if text:
            # Разбиваем текст на части и сохраняем как страницы
            chunks = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
            for index, content in enumerate(chunks):
                AIReadingPage.objects.create(
                    interpretation=obj,
                    content=content,
                    page_number=index
                )

def reverse_migrate_text(apps, schema_editor):
    # Логика обратного переноса (если потребуется откат)
    AIReadingInterpretation = apps.get_model('tarot', 'AIReadingInterpretation')
    AIReadingPage = apps.get_model('tarot', 'AIReadingPage')
    
    for obj in AIReadingInterpretation.objects.all():
        pages = AIReadingPage.objects.filter(interpretation=obj).order_by('page_number')
        full_text = "".join([p.content for p in pages])
        obj.response_text = full_text
        obj.save()

class Migration(migrations.Migration):

    dependencies = [
        ('tarot', '0016_userreading_original_query'),
    ]

    operations = [
        # 1. Сначала создаем новую модель
        migrations.CreateModel(
            name='AIReadingPage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content', models.TextField(verbose_name='Часть текста')),
                ('page_number', models.PositiveIntegerField(verbose_name='Порядковый номер чанка')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('interpretation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pages', to='tarot.aireadinginterpretation', verbose_name='ИИ-Интерпретация')),
            ],
            options={
                'verbose_name': 'Часть текста ответа',
                'ordering': ['page_number'],
            },
        ),
        # 2. ПЕРЕНОСИМ ДАННЫЕ (пока response_text еще существует в модели)
        migrations.RunPython(migrate_text_to_pages, reverse_migrate_text),
        
        # 3. Теперь безопасно удаляем поле
        migrations.RemoveField(
            model_name='aireadinginterpretation',
            name='response_text',
        ),
        # 4. Добавляем индекс (отдельно, так как модель была создана до него)
        migrations.AddIndex(
            model_name='aireadingpage',
            index=models.Index(fields=['interpretation', 'page_number'], name='tarot_airea_interpr_887388_idx'),
        ),
    ]