from dataclasses import dataclass
from typing import Any, Dict, Optional

@dataclass(frozen=True)
class Term:
    id: int
    name: str

    @staticmethod
    def from_api(data: Dict[str, Any]) -> "Term":
        return Term(
            id=int(data["id"]),
            name=str(data.get("name", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
        }
    
# @dataclass(frozen=True)
# class Course:

# @dataclass(frozen=True)
# class Quiz:

# @dataclass(frozen=True)
# class User:

# @dataclass(frozen=True)
# class Enrollment:

# @dataclass(frozen=True)
# class Submission:

# @dataclass(frozen=True)
# class Item: