class DeviceConnectionError(Exception):
    ...


class DeviceCloseError(Exception):
    ...


class DeviceApiError(Exception):
    def __init__(self, message, status=0, code=""):
        super().__init__(message)
        self.status = status
        self.code = code


class DownloadCancelled(Exception):
    pass
