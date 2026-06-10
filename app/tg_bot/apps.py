# tg_bot/apps.py
import os
import sys
from django.apps import AppConfig
from django.db import connection
from django.core.management import call_command
from server.logger import logger

class TgBotConfig(AppConfig):
    name = 'tg_bot'