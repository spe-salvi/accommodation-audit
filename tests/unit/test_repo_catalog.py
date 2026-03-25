import pytest


async def test_new_repo_lists_quizzes_for_course(new_repo):
    quizzes = await new_repo.list_quizzes(course_id=12977, engine="new")
    quiz_ids = {q.quiz_id for q in quizzes}

    assert 189437 in quiz_ids


async def test_new_repo_lists_submissions_for_quiz(new_repo):
    submissions = await new_repo.list_submissions(
        course_id=12977,
        quiz_id=189437,
        engine="new",
    )

    assert len(submissions) > 0


async def test_classic_repo_lists_quizzes_for_course(classic_repo):
    quizzes = await classic_repo.list_quizzes(course_id=12977, engine="classic")
    quiz_ids = {q.quiz_id for q in quizzes}

    assert 48379 in quiz_ids or 48372 in quiz_ids


async def test_classic_repo_lists_submissions_for_quiz(classic_repo):
    submissions = await classic_repo.list_submissions(
        course_id=12977,
        quiz_id=48379,
        engine="classic",
    )

    assert len(submissions) > 0


@pytest.mark.parametrize(
    ("engine", "course_id", "quiz_id"),
    [
        ("new", 12977, 189437),
        ("classic", 12977, 48379),
    ],
)
async def test_repo_lists_submissions_by_engine(engine, course_id, quiz_id, new_repo, classic_repo):
    repo = new_repo if engine == "new" else classic_repo

    submissions = await repo.list_submissions(
        course_id=course_id,
        quiz_id=quiz_id,
        engine=engine,
    )

    assert len(submissions) > 0