class LogMixin:
    def set_log(self, log: dict):
        log_type = log.get("type")
        if not log_type:
            return
        method = getattr(self.logger, log_type, None)
        if not method:
            return
        method(log.get("msg"))
