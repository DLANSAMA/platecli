class LoggerProxy:
    def __getattr__(self, name):
        import logging

        try:
            from bambu_cli import bambu

            return getattr(getattr(bambu, "logger", None) or logging.getLogger("bambu"), name)
        except ImportError:
            return getattr(logging.getLogger("bambu"), name)


logger = LoggerProxy()
