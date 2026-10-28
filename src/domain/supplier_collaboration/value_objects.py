from dataclasses import dataclass
import re

class InvalidEmailError(ValueError):
    pass

@dataclass(frozen=True)
class Email:
    value: str

    def __post_init__(self):
        if not self.value or not isinstance(self.value, str):
            raise InvalidEmailError("Email must be a non-empty string")
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, self.value):
            raise InvalidEmailError("Invalid email format")
