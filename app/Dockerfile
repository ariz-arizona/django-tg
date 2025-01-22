# Используем Python 3.10 в качестве базового образа
FROM python:3.10-slim

# Устанавливаем зависимости системы
RUN apt-get update && apt-get install -y \
    libpq-dev gcc curl --no-install-recommends && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Устанавливаем Poetry
ENV POETRY_VERSION=1.6.1
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы Poetry
COPY poetry.lock pyproject.toml /app/

# Устанавливаем зависимости через Poetry
RUN poetry config virtualenvs.create false && poetry install --no-root --no-interaction --no-ansi

# Копируем весь проект
COPY . /app/

# Устанавливаем переменные окружения
ENV PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=server.settings

RUN poetry run python manage.py collectstatic --noinput
# RUN poetry run python manage.py migrate

# Команда запуска приложения
CMD ["gunicorn", "server.wsgi:application", "--bind", "0.0.0.0:8000"]
