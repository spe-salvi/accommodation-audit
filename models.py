from dataclasses import dataclass
from typing import Any, Dict, Optional

# @dataclass(frozen=True)
# class Term:
#     id: int
#     name: str

#     @staticmethod
#     def from_api(data: Dict[str, Any]) -> "Term":
#         return Term(
#             id=int(data["id"]),
#             name=str(data.get("name", "")),
#         )

#     def to_dict(self) -> Dict[str, Any]:
#         return {
#             "id": self.id,
#             "name": self.name,
#         }
    
# @dataclass(frozen=True)
# class Course:
#     id: int
#     name: str

#     @staticmethod
#     def from_api(data: Dict[str, Any]) -> "Course":
#         return Course(
#             id=int(data["id"]),
#             name=str(data.get("name", "")),
#         )

#     @staticmethod
#     def from_list_api(data: Dict[str, Any]) -> "Course":
#         return Course(
#             id=int(data["id"]),
#             name=str(data.get("name", "")),
#         )
    
#     # def to_dict(self) -> Dict[str, Any]:
#     #     return {
#     #         "id": self.id,
#     #         "name": self.name,
#     #     }
    
# @dataclass(frozen=True)
# class Quiz:
#     id: int
#     name: str

#     @staticmethod
#     def from_classic_api(data: Dict[str, Any]) -> "Quiz":
#         return Quiz(
#             id=int(data["id"]),
#             name=str(data.get("name", "")),
#         )
    
#     @staticmethod
#     def from_new_api(course_id: int, data: Dict[str, Any]) -> "Quiz":
#         return Quiz(
#             id=int(data["id"]),
#             name=str(data.get("name", "")),
#         )

#     @staticmethod
#     def from_classic_list_api(data: Dict[str, Any]) -> "Quiz":
#         return Quiz(
#             id=int(data["id"]),
#             name=str(data.get("name", "")),
#         )
    
#     @staticmethod
#     def from_new_list_api(course_id: int, data: Dict[str, Any]) -> "Quiz":
#         return Quiz(
#             id=int(data["id"]),
#             name=str(data.get("name", "")),
#         )

#     # def to_dict(self) -> Dict[str, Any]:
#     #     return {
#     #         "id": self.id,
#     #         "name": self.name,
#     #     }

# @dataclass(frozen=True)
# class User:
#     id: int
#     name: str

#     @staticmethod
#     def from_api(data: Dict[str, Any]) -> "User":
#         return User(
#             id=int(data.get('id')),
#             name=str(data.get('sortable_name')),
#             sis = str(data.get('sis_user_id')),
#         )

#     @staticmethod
#     def from_list_api(data: Dict[str, Any]) -> "User":
#         return [User(
#             id=int(data.get('id')),
#             name=str(data.get('sortable_name')),
#             sis = str(data.get('sis_user_id')),
#         )]
    
#         # uid = user.get('id')
#         # sortable_name = user.get('sortable_name')
#         # sis = user.get('sis_user_id')
#     # def to_dict(self) -> Dict[str, Any]:
#     #     return {
#     #         "id": self.id,
#     #         "name": self.name,
#     #     }

# @dataclass(frozen=True)
# class Enrollment:
#     id: int
#     name: str

#     @staticmethod
#     def from_api(data: Dict[str, Any]) -> "Enrollment":
#         return Enrollment(
#             id=int(data["id"]),
#             name=str(data.get("name", "")),
#         )

#     # def to_dict(self) -> Dict[str, Any]:
#     #     return {
#     #         "id": self.id,
#     #         "name": self.name,
#     #     }


@dataclass
class Submission:
    user_id: str
    course_id: str
    quiz_id: str
    extra_time: int
    extra_attempts: int
    date: str

    @staticmethod
    def _workflow_to_date(workflow: str) -> str:
        if workflow in ("complete", "graded"):
            return "past"
        elif workflow in ("settings_only", "unsubmitted"):
            return "future"
        return ""

    @classmethod
    def from_api(
        cls,
        course_id: int,
        quiz_id: int,
        data: Dict[str, Any],
    ) -> Optional["Submission"]:

        uid = int(data.get("user_id"))
        if not uid:
            return None

        workflow = data.get("workflow_state", "")

        return cls(
            user_id=int(uid),
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            extra_time=data.get("extra_time", 0),
            extra_attempts=data.get("extra_attempts", 0),
            date=cls._workflow_to_date(workflow),
        )


@dataclass
class Item:
    course_id: int
    quiz_id: int
    item_id: int
    interaction_type: str
    spell_check: bool

    @classmethod
    def from_api(
        cls,
        course_id: int,
        quiz_id: int,
        data: Dict[str, Any],
    ) -> Optional["Item"]:
        """
        Build Item from Canvas New Quiz item payload.
        Returns None if item is not relevant.
        """

        entry = data.get("entry", {})
        q_type = entry.get("interaction_type_slug", "unknown")

        # Only keep essay questions
        if q_type != "essay":
            return None

        spell_check = entry.get(
            "interaction_data", {}
        ).get("spell_check", False)

        return cls(
            course_id=int(course_id),
            quiz_id=int(quiz_id),
            item_id=int(data.get("id")),
            interaction_type=q_type,
            spell_check=spell_check,
        )