# Generated by Django 5.1.5 on 2025-02-01 16:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tg_bot', '0024_rune_alter_oraculumdeck_options_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='rune',
            name='inverted_keys',
            field=models.TextField(blank=True, null=True, verbose_name='Ключи (перевернутое)'),
        ),
        migrations.AlterField(
            model_name='rune',
            name='inverted_meaning',
            field=models.TextField(blank=True, null=True, verbose_name='Значение (перевернутое)'),
        ),
        migrations.AlterField(
            model_name='rune',
            name='inverted_pos_1',
            field=models.TextField(blank=True, null=True, verbose_name='Позиция 1 (перевернутое)'),
        ),
        migrations.AlterField(
            model_name='rune',
            name='inverted_pos_2',
            field=models.TextField(blank=True, null=True, verbose_name='Позиция 2 (перевернутое)'),
        ),
        migrations.AlterField(
            model_name='rune',
            name='inverted_pos_3',
            field=models.TextField(blank=True, null=True, verbose_name='Позиция 3 (перевернутое)'),
        ),
    ]
