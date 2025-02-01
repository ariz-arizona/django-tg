# Generated by Django 5.1.5 on 2025-02-01 09:45

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    replaces = [('tg_bot', '0019_oraculumdeck_oraculum'), ('tg_bot', '0020_alter_oraculum_inverted'), ('tg_bot', '0021_alter_oraculum_description_alter_oraculum_direct')]

    dependencies = [
        ('tg_bot', '0018_tarotuserreading_message_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='OraculumDeck',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="Название колоды (например, 'Колода МЛАДЕНЦА').", max_length=255, verbose_name='Название колоды')),
                ('description', models.TextField(blank=True, help_text='Краткое описание колоды.', null=True, verbose_name='Описание колоды')),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='Дата и время создания колоды.', verbose_name='Дата создания')),
            ],
            options={
                'verbose_name': 'Колода оракула',
                'verbose_name_plural': 'Колоды оракула',
            },
        ),
        migrations.CreateModel(
            name='Oraculum',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file_id', models.CharField(help_text='Идентификатор файла (например, изображения карты).', max_length=255, verbose_name='ID файла')),
                ('name', models.CharField(help_text="Название карты (например, 'МЛАДЕНЕЦ').", max_length=255, verbose_name='Название карты')),
                ('description', models.TextField(blank=True, help_text='Краткое описание карты.', null=True, verbose_name='Описание карты')),
                ('direct', models.TextField(blank=True, help_text='Значение карты в прямом положении.', null=True, verbose_name='Прямое значение')),
                ('inverted', models.TextField(blank=True, help_text='Значение карты в перевернутом положении.', null=True, verbose_name='Перевернутое значение')),
                ('deck', models.ForeignKey(help_text='Колода, к которой относится карта.', on_delete=django.db.models.deletion.CASCADE, related_name='cards', to='tg_bot.oraculumdeck', verbose_name='Колода')),
            ],
            options={
                'verbose_name': 'Карта оракула',
                'verbose_name_plural': 'Карты оракула',
            },
        ),
    ]
