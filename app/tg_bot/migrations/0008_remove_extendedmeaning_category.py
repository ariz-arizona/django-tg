# Generated by Django 5.1.5 on 2025-01-29 20:16

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tg_bot', '0007_category_extendedmeaning_category_new'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='extendedmeaning',
            name='category',
        ),
    ]
