import logging
import sys

APP_LOGGER_NAME = "lawlah"


def configure_logging() -> None:
    logger = logging.getLogger(APP_LOGGER_NAME)
    if logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    child_name = name.removeprefix("src.")
    return logging.getLogger(f"{APP_LOGGER_NAME}.{child_name}")
