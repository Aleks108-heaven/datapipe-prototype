class DataPipeError(Exception):
    """Base class. Messages must never contain raw record values."""


class IngestError(DataPipeError):
    pass


class SchemaError(DataPipeError):
    pass


class AnalysisError(DataPipeError):
    pass


class AuditError(DataPipeError):
    pass
