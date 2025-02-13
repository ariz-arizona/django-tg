# Generated by Django 5.1.5 on 2025-01-25 16:22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tg_bot', '0003_tarotcard_tarotdeck_alter_parseproduct_options_and_more_squashed_0004_alter_tarotcarditem_unique_together_and_more'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='bot',
            options={'verbose_name': 'Бот', 'verbose_name_plural': 'Боты'},
        ),
        migrations.AddField(
            model_name='bot',
            name='bot_type',
            field=models.CharField(choices=[('ParserBot', 'ParserBot'), ('TarotBot', 'TarotBot')], default='ParserBot', max_length=50, verbose_name='Тип бота'),
        ),
    ]
