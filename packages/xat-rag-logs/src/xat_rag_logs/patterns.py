"""The regular expressions of the log query builder.

Each pattern has a left anchor and bounded parts, so that a start position costs a small, fixed number
of steps. A test checks the time of the worst-case lines. Lines are cut to 4096 characters first.
"""

import re

MAX_LINE_CHARS = 4096
MAX_LINES = 2000

LEVEL = re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|SEVERE|FATAL|CRITICAL|PANIC)\b")

# Identifiers. They are extracted before masking.
EXCEPTION = re.compile(r"(?<![\w$.])(?:[A-Za-z_][\w$]{0,60}\.){0,8}[A-Z][\w$]{0,80}(?:Exception|Error|Fault|Panic)\b")
JAVA_FRAME = re.compile(r"\bat [\w$.<>]{1,200}\(([\w$-]{1,100}\.[A-Za-z]{1,6}):(\d{1,7})\)")
PYTHON_FRAME = re.compile(r'File "([^"\n]{1,400})", line (\d{1,7})')
_EXTENSIONS = r"(?:py|java|kt|kts|scala|cs|go|js|jsx|ts|tsx|rb|cpp|cc|c|h|hpp|rs|php|swift|m|dart)"
FILE_LINE_HINT = re.compile(r"\." + _EXTENSIONS + r":\d")
FILE_LINE = re.compile(r"(?<![\w./\\-])((?:[\w-]{1,100}[./\\]){0,30}?[\w-]{1,100}\." + _EXTENSIONS + r"):(\d{1,7})\b")
JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b")
NOT_JIRA_PREFIXES = frozenset(
    {"UTF", "SHA", "ISO", "RFC", "TLS", "SSL", "AES", "MD", "HTTP", "CP", "IEEE", "X", "PKCS", "CVE"}
)
ERROR_CODE = re.compile(r"\b(?:[A-Z]{1,6}[-_]?\d{2,6}|0x[0-9A-Fa-f]{4,8})\b")
SHA = re.compile(r"\b(?=[0-9a-f]{0,39}[a-f])(?=[0-9a-f]{0,39}\d)[0-9a-f]{7,40}\b")
ENDPOINT = re.compile(r"\b(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[\w/{}.:-]{0,200})")

UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

# Masks. They run in this order.
MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?\b"), "<TS>"),
    (UUID, "<UUID>"),
    (re.compile(r"\b0x[0-9a-fA-F]{1,16}\b"), "<HEX>"),
    (re.compile(r"\b(?=[0-9a-fA-F]{0,63}\d)[0-9a-fA-F]{16,64}\b"), "<HEX>"),
    (re.compile(r"\b(?=[0-9a-f]{0,39}[a-f])(?=[0-9a-f]{0,39}\d)[0-9a-f]{7,40}\b"), "<SHA>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b"), "<IP>"),
    (re.compile(r"(?<![\w.])-?\d{1,30}(?:\.\d{1,30})?"), "<NUM>"),
)

WORD = re.compile(r"[^\W\d_]\w{2,40}")
STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "was",
        "were",
        "are",
        "not",
        "has",
        "have",
        "info",
        "debug",
        "trace",
        "warn",
        "warning",
        "error",
        "fatal",
        "true",
        "false",
        "null",
        "none",
    }
)
