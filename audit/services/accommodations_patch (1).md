# accommodations.py — 3 surgical replacements

# -----------------------------------------------------------------------
# Fix 1: quiz scope — fetch _course before calling audit_quiz
# -----------------------------------------------------------------------

# FIND (lines ~511-519):
        # --- quiz scope: direct, no enrollment lookup needed ---
        if quiz_id is not None and course_id is not None:
            rows = await self.audit_quiz(
                course_id=course_id,
                quiz_id=quiz_id,
                engine=engine,
                accommodation_types=types,
            )
            return _filter_user(rows, user_id)

# REPLACE WITH:
        # --- quiz scope: direct, no enrollment lookup needed ---
        if quiz_id is not None and course_id is not None:
            _course = None
            if hasattr(self.repo, "get_course_by_id"):
                _course = await self.repo.get_course_by_id(course_id)
            rows = await self.audit_quiz(
                course_id=course_id,
                quiz_id=quiz_id,
                engine=engine,
                accommodation_types=types,
                _course=_course,
            )
            return _filter_user(rows, user_id)


# -----------------------------------------------------------------------
# Fix 2: course scope — fetch _course before calling audit_course
# -----------------------------------------------------------------------

# FIND (lines ~521-528):
        # --- course scope: direct, no enrollment lookup needed ---
        if course_id is not None:
            rows = await self.audit_course(
                course_id=course_id,
                engine=engine,
                accommodation_types=types,
            )
            return _filter_user(rows, user_id)

# REPLACE WITH:
        # --- course scope: direct, no enrollment lookup needed ---
        if course_id is not None:
            _course = None
            if hasattr(self.repo, "get_course_by_id"):
                _course = await self.repo.get_course_by_id(course_id)
            rows = await self.audit_course(
                course_id=course_id,
                engine=engine,
                accommodation_types=types,
                _course=_course,
            )
            return _filter_user(rows, user_id)


# -----------------------------------------------------------------------
# Fix 3: enrollment scope — fetch Course objects before building tasks
# -----------------------------------------------------------------------

# FIND (lines ~555-562):
        tasks = [
            self._audit_course_with_semaphore(
                course_id=enrollment.course_id,
                engine=engine,
                accommodation_types=types,
            )
            for enrollment in enrollments
        ]

# REPLACE WITH:
        # Fetch Course objects concurrently — usually warm in persistent cache.
        if hasattr(self.repo, "get_course_by_id"):
            course_results = await asyncio.gather(
                *[self.repo.get_course_by_id(e.course_id) for e in enrollments],
                return_exceptions=True,
            )
            course_by_id: dict[int, Course] = {
                e.course_id: c
                for e, c in zip(enrollments, course_results)
                if isinstance(c, Course)
            }
        else:
            course_by_id = {}

        tasks = [
            self._audit_course_with_semaphore(
                course_id=enrollment.course_id,
                engine=engine,
                accommodation_types=types,
                _course=course_by_id.get(enrollment.course_id),
            )
            for enrollment in enrollments
        ]
