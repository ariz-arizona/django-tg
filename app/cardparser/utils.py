# utils.py

def render_template(template: str, context: dict) -> str:
    """
    Подставляет значения из словаря `context` в шаблон.
    Заменяет все вхождения {key} на соответствующее значение.

    Пример:
        render_template("Привет, {name}!", {"name": "Анна"}) → "Привет, Анна!"

    :param template: строка с шаблоном (может быть None или пустой)
    :param context: словарь с данными для подстановки
    :return: обработанная строка
    """
    if not template or not isinstance(template, str):
        return ""

    result = template
    for key, value in context.items():
        placeholder = f"{{{key}}}"
        # Заменяем только если значение — строка или число
        if value is not None:
            result = result.replace(placeholder, str(value))

    return result