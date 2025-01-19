import logging

class ColorFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[92m',     # Green
        'INFO': '\033[94m',      # Blue
        'WARNING': '\033[93m',   # Yellow
        'ERROR': '\033[91m',     # Red
        'CRITICAL': '\033[1;91m' # Bold Red
    }
    RESET = '\033[0m'

    def format(self, record):
        # Добавляем цвет к уровню
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        log_message = super().format(record)

        return log_message

# Настраиваем логгер
formatter = ColorFormatter('%(asctime)s [%(levelname)s] || %(filename)s:%(lineno)d %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

logger = logging.getLogger('')
logger.setLevel(logging.INFO)
logger.addHandler(console_handler)