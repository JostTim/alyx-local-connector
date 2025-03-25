from logging import Filter, Logger, LogRecord, StreamHandler, INFO, Formatter
from contextlib import contextmanager


class ExcludeMessageFilter(Filter):
    def __init__(self, message_to_exclude: str):
        super().__init__()
        self.message_to_exclude = message_to_exclude

    def filter(self, record: LogRecord):
        return self.message_to_exclude not in record.getMessage()


@contextmanager
def filter_message(logger: Logger, message_to_exclude):
    """Filters log messages for a given logger, excluding specified messages during the context of a yield.

    This function sets up a temporary filter on the provided logger to exclude messages that match
    the specified `message_to_exclude`. If the logger does not have any handlers, a custom stream
    handler is created and added. The filter is applied to both the logger and its handlers. After
    the context is exited, the filter is removed, and the logger's original state is restored.

    Args:
        logger (Logger): The logger instance to which the filter will be applied.
        message_to_exclude (str): The message that should be excluded from logging.

    Yields:
        None: This function is a generator that yields control back to the caller.

    Raises:
        None: This function does not raise any exceptions.
    """

    # if the user didn't set any handler, filtering won't work and unwanted spammy messages are going to be printed out
    custom_handler = None
    if not logger.hasHandlers():
        # so we setup one simple one, taht we will remove later
        custom_handler = StreamHandler()
        custom_handler.setLevel(INFO)
        custom_handler.setFormatter(Formatter("%(name)s - %(levelname)s - %(message)s"))
        logger.addHandler(custom_handler)

    # propagate_initial_value = logger.propagate
    # logger.propagate = True
    filter_instance = ExcludeMessageFilter(message_to_exclude)

    # Add the filter to the logger and it's handler(s)
    logger.addFilter(filter_instance)
    for handler in logger.handlers:
        handler.addFilter(filter_instance)

    try:
        yield None

    finally:
        # Remove the filter from the logger and it' handler(s)
        logger.removeFilter(filter_instance)
        for handler in logger.handlers:
            handler.removeFilter(filter_instance)
        # logger.propagate = propagate_initial_value

        if custom_handler:
            # remove the temporary handler if existing
            logger.removeHandler(custom_handler)
