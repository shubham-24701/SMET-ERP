import json
import logging
import re
import traceback

# Order matters: secrets first, then emails, then phone-like digit runs (9+ digits so dates/UUIDs survive).
_PATTERNS = [
    (
        re.compile(r"(?i)(bearer\s+|session=|token=|password=|passwd=|api[_-]?key=|code=)\S+"),
        r"\1[redacted]",
    ),
    (re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)[^@\s]+@"), r"\1[redacted]@"),
    # Starts only at the beginning of a word run, so a long run with no "@" stays linear-time.
    (re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"(?<!\w)\+?\d(?:[\s().-]?\d){8,14}(?!\w)"), "[phone]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage())[:4000],  # cut after redacting: no half secrets
        }
        if request_id := getattr(record, "request_id", None):
            data["request_id"] = request_id
        if record.exc_info:
            # Where it failed, not the error's text: database errors can quote row values.
            kind, _, tb = record.exc_info
            data["exc"] = kind.__name__ + "\n" + "".join(traceback.format_tb(tb))
        return json.dumps(data, ensure_ascii=False)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # Uvicorn's own loggers print crash tracebacks in their default format, past our redaction.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers, logger.propagate = [handler], False
    logging.getLogger("uvicorn.access").disabled = True  # our middleware logs requests instead
