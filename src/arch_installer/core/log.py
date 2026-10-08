"""console logging for the installer, built on the standard logging module.

modules log through `logging.getLogger(__name__)`; this module only wires the
handlers once, at startup. progress goes to stdout, warnings and errors to stderr.
"""

import logging
import sys

PACKAGE_LOGGER_NAME = "arch_installer"
BANNER_WIDTH = 80

LEVEL_PREFIXES = {
    logging.WARNING: "Warning: ",
    logging.ERROR: "Error: ",
    logging.CRITICAL: "Error: ",
}


class ConsoleFormatter(logging.Formatter):
    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802
        return LEVEL_PREFIXES.get(record.levelno, "") + record.message


class BelowLevelFilter(logging.Filter):
    def __init__(self, level: int) -> None:
        super().__init__()
        self._level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self._level


def configure_logging(level: int) -> None:
    formatter = ConsoleFormatter()

    progress_handler = logging.StreamHandler(sys.stdout)
    progress_handler.addFilter(BelowLevelFilter(logging.WARNING))
    progress_handler.setFormatter(formatter)

    problem_handler = logging.StreamHandler(sys.stderr)
    problem_handler.setLevel(logging.WARNING)
    problem_handler.setFormatter(formatter)

    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    package_logger.handlers = [progress_handler, problem_handler]
    package_logger.setLevel(level)
    package_logger.propagate = False


def banner(title: str, subtitle: str = "") -> str:
    lines = ["", "=" * BANNER_WIDTH, title.center(BANNER_WIDTH).rstrip()]
    if subtitle:
        lines.append(subtitle.center(BANNER_WIDTH).rstrip())
    lines.append("=" * BANNER_WIDTH)
    return "\n".join(lines)
