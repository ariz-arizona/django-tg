## Запуск дев-сервера

Для запуска дев-сервера Django с подключением к базе данных Postgres из Docker, выполните следующие шаги:

### Шаги для запуска

1. Убедитесь, что контейнер с Postgres запущен:
   ```bash
   docker-compose up -d db
   ```
1. Извлеките порт Postgres, сгенерированный Docker:

1. Установите POSTGRES_HOST и выполните команду для запуска сервера:

1. Команда:
    ```bash
    POSTGRES_PORT=$(docker inspect postgres --format='{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}') POSTGRES_HOST=localhost poetry run python manage.py runserver
    ```
1. И редис
   ```bash
   REDIS_HOST=localhost REDIS_PORT=$(docker inspect redis --format='{{(index (index .NetworkSettings.Ports "6379/tcp") 0).HostPort}}') POSTGRES_PORT=$(docker inspect postgres --format='{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}') POSTGRES_HOST=localhost poetry run python manage.py runserver
   ```