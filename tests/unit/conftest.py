from pathlib import Path

import pytest

from audit.services.accommodations import AccommodationService, AccommodationType
from audit.repos.json_repo import JsonRepo


DUMPS_DIR = Path(__file__).resolve().parent.parent.parent
PATH = DUMPS_DIR / "dumps"


@pytest.fixture
def new_repo() -> JsonRepo:
    return JsonRepo(
        participant_path=PATH / "participants.json",
        submission_path=PATH / "new_submissions.json",
        items_path=PATH / "new_items.json",
        quizzes_path=PATH / "new_quizzes.json",
        courses_path=PATH / "courses.json",
    )


@pytest.fixture
def classic_repo() -> JsonRepo:
    return JsonRepo(
        participant_path=PATH / "participants.json",
        submission_path=PATH / "classic_submissions.json",
        items_path=PATH / "new_items.json",
        quizzes_path=PATH / "classic_quizzes.json",
        courses_path=PATH / "courses.json",
    )


@pytest.fixture
def new_svc(new_repo: JsonRepo) -> AccommodationService:
    return AccommodationService(new_repo)


@pytest.fixture
def classic_svc(classic_repo: JsonRepo) -> AccommodationService:
    return AccommodationService(classic_repo)