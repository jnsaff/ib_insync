---
id: task-3
title: Add async context manager support for IB class
status: To Do
assignee: []
created_date: '2025-07-15'
labels: []
dependencies: []
---

## Description

Implement async context manager (__aenter__/__aexit__) for the IB class to properly handle connection lifecycle and resource cleanup. This will allow for cleaner 'async with' usage patterns.

## Acceptance Criteria

- [ ] IB class implements __aenter__ and __aexit__ methods
- [ ] Context manager properly handles connection establishment and cleanup
- [ ] Documentation updated with async context manager examples
- [ ] Tests added for context manager functionality
- [ ] Backwards compatibility maintained with existing connect/disconnect methods
