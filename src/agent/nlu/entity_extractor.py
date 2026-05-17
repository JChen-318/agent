"""Entity extraction from user commands — time, contact, location, message text."""

import re
from dataclasses import dataclass, field
from typing import Optional

# Time expression patterns
_TIME_PATTERNS = [
    r"\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?",
    r"(?:today|tomorrow|yesterday|今天|明天|昨天|后天)",
    r"(?:morning|afternoon|evening|night|早上|上午|下午|晚上|中午)",
    r"(?:in\s+\d+\s+(?:minute|hour|day|week|month|year)s?)",
    r"(?:\d+分钟后|\d+小时后|\d+天后)",
    r"(?:下*(?:星期|周)[一二三四五六日])",
]

# Phone number pattern (China mainland)
_PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")

# Message text extraction pattern
_MESSAGE_PATTERN = re.compile(
    r"(?:说|发|send|say|告诉(?:他|她)?|msg|message|写|write)\s*[:：]?\s*(.+?)(?:$|给|to)",
    re.IGNORECASE,
)

# Location extraction patterns
_LOCATION_PATTERNS = [
    re.compile(r"(?:at|in|near|去|在|到|附近)\s+([一-鿿\w\s]{2,20})"),
]


@dataclass
class ExtractedEntities:
    app_name: Optional[str] = None
    app_package: Optional[str] = None
    contact_name: Optional[str] = None
    phone_number: Optional[str] = None
    time_expression: Optional[str] = None
    location: Optional[str] = None
    message_text: Optional[str] = None
    raw: dict = field(default_factory=dict)


def extract_entities(text: str) -> ExtractedEntities:
    """Extract structured entities from a user command."""
    entities = ExtractedEntities(raw={"original": text})

    # Time
    for pattern in _TIME_PATTERNS:
        m = re.search(pattern, text)
        if m:
            entities.time_expression = m.group()
            break

    # Phone number
    m = _PHONE_PATTERN.search(text)
    if m:
        entities.phone_number = m.group()

    # Message text
    m = _MESSAGE_PATTERN.search(text)
    if m:
        entities.message_text = m.group(1).strip()

    # Location
    for pattern in _LOCATION_PATTERNS:
        m = pattern.search(text)
        if m:
            entities.location = m.group(1).strip()
            break

    return entities
