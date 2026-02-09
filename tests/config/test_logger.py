import logging
from logging import handlers

import openscan_firmware.config.logger as logger_module


def test_sanitize_logging_config_rewrites_relative_paths(tmp_path):
    monkeypatched_logs_dir = tmp_path / "logs"
    monkeypatched_logs_dir.mkdir()

    logger_module.DEFAULT_LOGS_PATH = monkeypatched_logs_dir

    config = {
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": "app.log",
            }
        }
    }

    sanitized = logger_module._sanitize_logging_config(config)

    assert sanitized["handlers"]["file"]["filename"] == str(monkeypatched_logs_dir / "app.log")


def test_setup_logging_creates_log_directory(monkeypatch, tmp_path):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr(logger_module, "DEFAULT_LOGS_PATH", logs_dir)
    monkeypatch.setattr(logger_module, "load_settings_json", lambda filename: None)
    monkeypatch.setattr(logger_module.logging, "basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(logger_module.logging, "warning", lambda *args, **kwargs: None)

    logger_module.setup_logging()

    assert logs_dir.exists()


def test_setup_logging_uses_dict_config_when_available(monkeypatch, tmp_path):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr(logger_module, "DEFAULT_LOGS_PATH", logs_dir)

    captured = {}

    def fake_dict_config(cfg):
        captured["config"] = cfg

    monkeypatch.setattr(logger_module.logging.config, "dictConfig", fake_dict_config)
    monkeypatch.setattr(logger_module.logging, "getLogger", logging.getLogger)

    config_payload = {
        "version": 1,
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": "relative.log",
            }
        },
        "loggers": {},
    }

    monkeypatch.setattr(logger_module, "load_settings_json", lambda filename: config_payload)

    logger_module.setup_logging(preferred_filename="custom.json")

    assert "config" in captured
    assert captured["config"]["handlers"]["file"]["filename"] == str(logs_dir / "relative.log")


def test_setup_logging_falls_back_when_dict_config_fails(monkeypatch, tmp_path):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr(logger_module, "DEFAULT_LOGS_PATH", logs_dir)

    monkeypatch.setattr(
        logger_module,
        "load_settings_json",
        lambda filename: {"version": 1, "handlers": {}, "loggers": {}},
    )

    def boom(_cfg):
        raise ValueError("boom")

    monkeypatch.setattr(logger_module.logging.config, "dictConfig", boom)

    called = {}

    def fake_basic(**kwargs):
        called["basic"] = kwargs

    monkeypatch.setattr(logger_module.logging, "basicConfig", fake_basic)
    monkeypatch.setattr(logger_module.logging, "error", lambda *args, **kwargs: called.setdefault("error", True))

    logger_module.setup_logging()

    assert "basic" in called and "error" in called


def test_flush_memory_handlers_flushes_root_and_named(monkeypatch):
    flushed: list[str] = []

    class SpyMemoryHandler(handlers.MemoryHandler):
        def __init__(self, label):
            super().__init__(capacity=1)
            self.label = label

        def flush(self):
            flushed.append(self.label)

    root_logger = logging.getLogger()
    named_logger = logging.getLogger("named-test")

    original_root_handlers = list(root_logger.handlers)
    original_named_handlers = list(named_logger.handlers)
    original_logger_dict = dict(logging.root.manager.loggerDict)

    try:
        root_logger.handlers = [SpyMemoryHandler("root")]
        named_logger.handlers = [SpyMemoryHandler("named")]
        logging.root.manager.loggerDict = {"named-test": named_logger}
        logger_module.flush_memory_handlers()
    finally:
        root_logger.handlers = original_root_handlers
        named_logger.handlers = original_named_handlers
        logging.root.manager.loggerDict = original_logger_dict

    assert flushed == ["root", "named"]
