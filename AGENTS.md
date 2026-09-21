# Perseverer development instructions

These are standing project requirements from the project owner. Apply them to every change,
including work performed only in the local development environment.

- Run every available automated check before delivering a feature: the complete Python test
  suite, whole-project Ruff and mypy checks, the complete frontend test suite, the production
  frontend build, formatting checks, package build, dependency audit, and migration validation.
  Do not call a feature ready when a relevant check has not completed successfully. Report any
  environmental limitation explicitly.
- For UI work, follow Perseverer's existing graphic charter and reusable styles. Verify the
  finished result in the local browser at desktop and narrow responsive widths. Do not claim a
  control is visible or working without inspecting it in the rendered page.
- Keep feature documentation current. When an API changes, update `docs/API.md` and the rendered
  API guide used by the frontend.
- Develop and verify locally first. Deploy to production only when the project owner asks for it.
- Preserve unrelated work in a dirty working tree.
