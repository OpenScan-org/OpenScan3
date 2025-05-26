import logging
import logging.config
import json
import os


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_LOG_CONFIG_PATH = os.path.join(PROJECT_ROOT, "settings", "default_logging.json")
DEFAULT_LOGS_PATH = os.path.join(PROJECT_ROOT, "logs")

def setup_logging_from_json_file(path_to_config=DEFAULT_LOG_CONFIG_PATH, default_level=logging.INFO):
    """
    Loads logging configuration from a JSON file.

    Args:
        path_to_config (str): Path to the logging configuration JSON file.
        default_level (logging.Level): Default logging level to use if the config file is not found.
    """
    log_dir = DEFAULT_LOGS_PATH

    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
            print(f"Log directory created: {log_dir}") # Temporäres Print für Setup-Info
        except OSError as e:
            logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
            logging.warning(f"Could not create log directory {log_dir}: {e}. Using basicConfig.")

    if os.path.exists(path_to_config):
        try:
            with open(path_to_config, "rt") as f:
                config_dict = json.load(f)
            logging.config.dictConfig(config_dict)
            logging.info(f"Logging configured successfully from JSON file: {path_to_config}")
        except Exception as e:
            logging.basicConfig(level=default_level, format="%(levelname)s: %(message)s")
            logging.error(f"Error loading logging config from {path_to_config}: {e}. Falling back to basicConfig.", exc_info=True)
    else:
        logging.basicConfig(level=default_level, format="%(levelname)s: %(message)s")
        logging.warning(f"Logging config file {path_to_config} not found. Falling back to basicConfig.")
