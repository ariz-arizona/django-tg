# Generated by Django 5.1.5 on 2025-02-01 10:28

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tg_bot', '0022_rename_oraculum_oraculumitem'),
    ]

    operations = [
        migrations.RenameField(
            model_name='oraculumitem',
            old_name='file_id',
            new_name='img_id',
        ),
    ]
