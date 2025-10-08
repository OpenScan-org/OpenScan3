import logging
import logging.config
import json
import os
from importlib import resources
from pathlib import Path


def _resolve_logs_dir() -> str:
    """Resolve a writable logs directory with precedence.

    Precedence:
    1) OPENSCAN_LOG_DIR env var
    2) ~/.openscan3/logs
    3) ./logs (cwd)
    """
    env_dir = os.getenv("OPENSCAN_LOG_DIR")
    if env_dir:
        return env_dir
    home_dir = Path.home() / ".openscan3" / "logs"
    return str(home_dir)


DEFAULT_LOGS_PATH = _resolve_logs_dir()


def _settings_dirs_precedence() -> list[str]:
    """Return list of settings directories in precedence order (not including packaged defaults)."""
    dirs: list[str] = []
    env_dir = os.getenv("OPENSCAN_SETTINGS_DIR")
    if env_dir:
        dirs.append(env_dir)
    dirs.append("/etc/openscan3")
    dirs.append("./settings")
    return dirs


def find_settings_file(filename: str) -> str | None:
    """Find a settings file according to precedence directories.

    Returns an absolute path if found, else None.
    """
    for d in _settings_dirs_precedence():
        candidate = Path(d) / filename
        if candidate.exists():
            return str(candidate)
    return None


def load_settings_json(filename: str) -> dict | None:
    """Load a JSON settings file from precedence or packaged defaults.

    Attempt order:
    - OPENSCAN_SETTINGS_DIR
    - /etc/openscan3/
    - ./settings/
    - packaged defaults (openscan.resources.settings)
    """
    path = find_settings_file(filename)
    if path:
        try:
            return json.loads(Path(path).read_text())
        except Exception:
            logging.getLogger(__name__).exception("Failed reading settings from %s", path)

    # packaged defaults
    try:
        pkg = resources.files("openscan.resources.settings").joinpath(filename)
        if pkg.is_file():
            with pkg.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        logging.getLogger(__name__).exception("Failed reading packaged default for %s", filename)
    return None


def _sanitize_logging_config(config: dict) -> dict:
    """Sanitize logging dictConfig for Python 3.11 compatibility and adjust file paths.

    - Remove unsupported keys (e.g., flushOnClose for MemoryHandler).
    - Rewrite relative handler filenames to DEFAULT_LOGS_PATH.
    """
    handlers = config.get("handlers", {})
    for name, handler in handlers.items():
        if handler.get("class") == "logging.handlers.MemoryHandler":
            # Remove unsupported field
            handler.pop("flushOnClose", None)
        # Normalize filename targets into our logs directory
        filename = handler.get("filename")
        if filename and not os.path.isabs(filename):
            # place into DEFAULT_LOGS_PATH with same basename
            handler["filename"] = str(Path(DEFAULT_LOGS_PATH) / Path(filename).name)
    return config


def setup_logging(preferred_filename: str | None = None, default_level=logging.INFO) -> None:
    """Configure logging using dictConfig with robust defaults.

    If preferred_filename is provided (e.g., "advanced_logging.json"), try to load it
    using load_settings_json. If not found, fall back to "default_logging.json".
    If neither is available, initialize basicConfig.
    """
    # Ensure logs directory exists (best-effort)
    try:
        Path(DEFAULT_LOGS_PATH).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
        logging.warning(f"Could not create log directory {DEFAULT_LOGS_PATH}: {e}. Using basicConfig.")

    config_dict = None
    filenames = [preferred_filename] if preferred_filename else []
    filenames.append("default_logging.json")

    for fname in filenames:
        if not fname:
            continue
        cfg = load_settings_json(fname)
        if cfg:
            config_dict = _sanitize_logging_config(cfg)
            break

    if config_dict:
        try:
            logging.config.dictConfig(config_dict)
            logging.getLogger(__name__).info("Logging configured from %s", preferred_filename or "default_logging.json")
            return
        except Exception as e:
            logging.basicConfig(level=default_level, format="%(levelname)s: %(message)s")
            logging.error("Error applying logging config: %s. Falling back to basicConfig.", e, exc_info=True)
            return

    # Fallback
    logging.basicConfig(level=default_level, format="%(levelname)s: %(message)s")
    logging.warning("No logging configuration found. Using basicConfig.")

def flush_memory_handlers():
    """Flush all MemoryHandler instances across all configured loggers.

    This ensures that any buffered DEBUG/INFO records are written to the
    underlying file handlers before reading or downloading log files.

    Note:
        We iterate across the root logger and all named loggers registered in
        the logging manager to catch every MemoryHandler.
    """
    def _flush_handlers(logger: logging.Logger) -> None:
        for handler in getattr(logger, "handlers", []) or []:
            try:
                # Only flush MemoryHandler to force write-through to targets
                if isinstance(handler, logging.handlers.MemoryHandler):
                    handler.flush()
            except Exception:
                # Use a local logger to avoid recursion on failures
                logging.getLogger(__name__).exception("Failed to flush handler %r", handler)

    # Flush root logger handlers
    root_logger = logging.getLogger()
    _flush_handlers(root_logger)

    # Flush all named loggers known to the logging system
    for _name, logger in logging.root.manager.loggerDict.items():
        if isinstance(logger, logging.Logger):
            _flush_handlers(logger)
