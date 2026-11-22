class GarageError(ValueError):
    """An actionable, safe-to-display validation error."""
    def __init__(self, message, code='validation', detail=None):
        super().__init__(message)
        self.code, self.detail = code, detail

class Cancelled(GarageError):
    def __init__(self):
        super().__init__('Cancelled. Your source and game installation were not changed.', 'cancelled')
