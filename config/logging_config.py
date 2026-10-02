import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re


class Redact(logging.Filter):
    secrets: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self.secrets:
            if secret:
                message = message.replace(secret, "[redacted]")
        record.msg = re.sub(r"(?i)(Bearer\s+)[\w.-]+|sk-[\w-]+", "[redacted]", message)
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        text = super().format(record)
        for secret in Redact.secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return re.sub(r"(?i)(Bearer\s+)[\w.-]+|sk-[\w-]+", "[redacted]", text)


def setup(root: Path) -> None:
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "copilot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(Redact())
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
