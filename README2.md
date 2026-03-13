# Accommodation Audit System
## Overview



## Why I Built This




## Architecture





## Key Features




## Example Usage




## Installation




## Development Process

Phase 1 – Initial Script

- Naive approach to API calls and input processing
- No caching or asynchronous functions
- One course, one user, one quiz with local json fetch implemented

Phase 2 - Broaden Scope

- All users for a quiz
- Expanded by accommodation type:
    1. Extra time in new quizzes (most complicated because of different API source)
    2. Extra time in classic quizzes
    3. Extra attempts in new quizzes
    4. Extra attempts in classic quizzes
    5. Spell-check in new quizzes, per question
    6. All accommodation types (or selected combination) for one request
- All quizzes in a course


## Tradeoffs & Design Decisions

- AuditRow contains two different shapes: per user and per quiz item
  - Decided to keep as one row (at least early in development) for simplicity of implementation and testing
- Users may have multiple submissions for a given quiz. However, decided to only keep the first submission (early in development) for simplicity of implementation and testing


## Performance Considerations




## Future Improvements




## Testing




## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.