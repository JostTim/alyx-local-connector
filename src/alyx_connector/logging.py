from logging import Filter, Logger
from contextlib import contextmanager


class ExcludeMessageFilter(Filter):
    def __init__(self, message_to_exclude):
        super().__init__()
        self.message_to_exclude = message_to_exclude

    def filter(self, record):
        return self.message_to_exclude not in record.getMessage()


@contextmanager
def temporary_filter_out(logger: Logger, message_to_exclude):
    filter_instance = ExcludeMessageFilter(message_to_exclude)

    # Add the filter to the root logger
    logger.addFilter(filter_instance)
    try:
        yield
    finally:
        # Remove the filter from the root logger
        logger.removeFilter(filter_instance)
