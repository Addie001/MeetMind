"""Context-local model attempt measurements, without credentials or prompt text."""
from contextvars import ContextVar

model_calls = ContextVar('benchmark_model_calls', default=None)


def record_call(record):
    calls = model_calls.get()
    if calls is not None:
        calls.append(record)
