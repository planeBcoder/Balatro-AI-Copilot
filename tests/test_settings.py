from config.settings import SettingsStore, Settings, protect
from config.logging_config import Redact
import logging


def test_dpapi_roundtrip():
    plaintext = b"temporary-unit-test-value"
    encrypted = protect(plaintext)
    assert plaintext not in encrypted
    assert protect(encrypted, True) == plaintext


def test_key_encrypted_and_settings_separate(tmp_path):
    store = SettingsStore(tmp_path)
    store.save_key("unit-test-only-value")
    store.save(Settings(model="deepseek-v4-pro"))
    assert store.key() == "unit-test-only-value"
    assert "unit-test-only-value" not in store.path.read_text()
    assert b"unit-test-only-value" not in store.key_path.read_bytes()
    assert store.load().model == "deepseek-v4-pro"


def test_redaction():
    record = logging.LogRecord("test", logging.INFO, "", 1, "Bearer secret sk-sample123", (), None)
    assert Redact().filter(record)
    assert "secret" not in record.getMessage() and "sk-sample" not in record.getMessage()
