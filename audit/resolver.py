"""
Entity resolution for fuzzy search.

Translates human-readable query strings into lists of Canvas entity
objects by delegating to the Canvas search API or filtering locally.

Resolution strategies
---------------------
- Terms:   local filter against cached ``list_terms()`` result.
           Canvas doesn't expose a term search endpoint; the full list
           is already cached with a 1-year TTL so local filtering is
           fast and free.

- Courses: Canvas account-level search via
           ``GET /api/v1/accounts/:id/courses?search_term=&enrollment_term_id=``
           A term_id is required — course names are not globally unique
           and an account-wide scan would be too slow and noisy.

- Quizzes: local filter against ``list_quizzes(course_id)``.
           Canvas has no quiz search endpoint; the quiz list for a
           course is already cheap to fetch and small enough to scan.
           A course_id is required (quiz titles are not globally unique).

- Users:   Canvas account-level search via
           ``GET /api/v1/accounts/:id/users?search_term=``
           Canvas handles both display name and SIS user ID matching,
           so we delegate entirely — no local scoring needed.

Multiple matches
----------------
All resolution methods return a list. The caller (``AuditPlanner``)
fans out into one sub-scope per result. Zero results raises
``ResolveError`` with a helpful message.

Errors
------
``ResolveError`` is raised when:
  - A query returns no matches (unknown name, typo, wrong term, etc.)
  - Required context is missing (course search without term_id, etc.)

Usage
-----
    resolver = Resolver(repo)
    terms = await resolver.resolve_term("Spring")
    courses = await resolver.resolve_course("Moral Principles", term_id=117)
    users = await resolver.resolve_user("McCarthy")
    quizzes = await resolver.resolve_quiz("Midterm", course_id=12977)
"""

from __future__ import annotations

import logging

from audit.models.canvas import Course, Quiz, Term, User

logger = logging.getLogger(__name__)


class ResolveError(Exception):
    """
    Raised when a name query cannot be resolved to any Canvas entity.

    Carries the original query and a human-readable message suitable
    for display in the CLI.
    """
    def __init__(self, message: str, *, query: str, entity_type: str) -> None:
        super().__init__(message)
        self.query = query
        self.entity_type = entity_type


class Resolver:
    """
    Resolves name query strings into Canvas entity lists.

    Parameters
    ----------
    repo:
        ``CanvasRepo`` instance. Used for all Canvas API calls.
    """

    def __init__(self, repo) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Term resolution — local filter against cached list
    # ------------------------------------------------------------------

    async def resolve_term(self, query: str) -> list[Term]:
        """
        Resolve a term name query to a list of matching Terms.

        Searches ``list_terms()`` locally (the result is cached with a
        1-year TTL, so this is free after the first call).

        Matching is case-insensitive substring. Canvas term names are
        short and structured (e.g. "2025-2026 - Spring"), so substring
        matching is almost always unambiguous.

        Parameters
        ----------
        query:
            The search string. Case-insensitive. e.g. "Spring", "2026".

        Raises
        ------
        ResolveError
            If no term names contain all query tokens.
        """
        terms = await self._repo.list_terms()

        # Split query into tokens and require all to appear in the term
        # name (in any order, case-insensitive). This means:
        #   "Spring 2026"    matches "2025-2026 - Spring"
        #   "2026 Spring"    matches "2025-2026 - Spring"
        #   "Spring"         matches "2025-2026 - Spring"
        #   "Fall 25"        matches "2025-2026 - Fall"
        tokens = query.lower().split()
        matches = [
            t for t in terms
            if t.name and all(tok in t.name.lower() for tok in tokens)
        ]

        if not matches:
            available = ", ".join(
                f'"{t.name}"' for t in terms if t.name
            )
            raise ResolveError(
                f"No term found matching {query!r}. "
                f"Available terms: {available or 'none'}",
                query=query,
                entity_type="term",
            )

        logger.info(
            "resolve_term: %r → %d match(es): %s",
            query,
            len(matches),
            [t.name for t in matches],
        )
        return matches

    # ------------------------------------------------------------------
    # Course resolution — Canvas account search
    # ------------------------------------------------------------------

    async def resolve_course(self, query: str, *, term_id: int) -> list[Course]:
        """
        Resolve a course name/code/SIS ID query to a list of matching Courses.

        Delegates to the Canvas account-level course search endpoint:
        ``GET /api/v1/accounts/:id/courses?search_term=&enrollment_term_id=``

        Canvas matches against course name, course code, and SIS course ID.

        Parameters
        ----------
        query:
            The search string. Canvas handles partial matching.
        term_id:
            Required. Scopes the search to a single enrollment term.

        Raises
        ------
        ResolveError
            If the Canvas search returns no courses.
        """
        courses = await self._repo.search_courses(query, term_id=term_id)

        if not courses:
            raise ResolveError(
                f"No course found matching {query!r} in term {term_id}. "
                f"Check the course name, code, or SIS ID and try again.",
                query=query,
                entity_type="course",
            )

        logger.info(
            "resolve_course: %r (term=%d) → %d match(es): %s",
            query,
            term_id,
            len(courses),
            [c.name for c in courses],
        )
        return courses

    # ------------------------------------------------------------------
    # Quiz resolution — local filter against course quiz list
    # ------------------------------------------------------------------

    async def resolve_quiz(self, query: str, *, course_id: int, engine: str) -> list[Quiz]:
        """
        Resolve a quiz title query to a list of matching Quizzes.

        Searches the quiz list for the given course locally. Canvas has
        no quiz search endpoint, but a course's quiz list is small and
        cheap to fetch (and cached with a 1-day TTL).

        Parameters
        ----------
        query:
            The search string. Case-insensitive substring match.
        course_id:
            Required. Quiz titles are not globally unique.
        engine:
            "new" or "classic". Each engine has its own quiz list.

        Raises
        ------
        ResolveError
            If no quiz titles contain the query string.
        """
        quizzes = await self._repo.list_quizzes(
            course_id=course_id, engine=engine,
        )
        tokens = query.lower().split()
        matches = [
            quiz for quiz in quizzes
            if quiz.title and all(tok in quiz.title.lower() for tok in tokens)
        ]

        if not matches:
            available = ", ".join(
                f'"{qz.title}"' for qz in quizzes if qz.title
            )
            raise ResolveError(
                f"No {engine} quiz found matching {query!r} in course {course_id}. "
                f"Available quizzes: {available or 'none'}",
                query=query,
                entity_type="quiz",
            )

        logger.info(
            "resolve_quiz: %r (course=%d engine=%s) → %d match(es): %s",
            query,
            course_id,
            engine,
            len(matches),
            [qz.title for qz in matches],
        )
        return matches

    # ------------------------------------------------------------------
    # User resolution — Canvas account search
    # ------------------------------------------------------------------

    async def resolve_user(self, query: str) -> list[User]:
        """
        Resolve a user name or SIS user ID query to a list of matching Users.

        Delegates to the Canvas account-level user search endpoint:
        ``GET /api/v1/accounts/:id/users?search_term=``

        Canvas matches against display name, sortable name, login ID,
        email, and SIS user ID — no local scoring needed.

        Parameters
        ----------
        query:
            The search string. Canvas handles partial name matching and
            exact SIS ID matching.

        Raises
        ------
        ResolveError
            If the Canvas search returns no users.
        """
        users = await self._repo.search_users(query)

        if not users:
            raise ResolveError(
                f"No user found matching {query!r}. "
                f"Try a partial last name or SIS user ID.",
                query=query,
                entity_type="user",
            )

        logger.info(
            "resolve_user: %r → %d match(es): %s",
            query,
            len(users),
            [u.sortable_name for u in users],
        )
        return users
