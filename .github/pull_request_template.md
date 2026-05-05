<!--
Thanks for the PR! A few quick checks before merging — fill in
whatever's relevant and delete the rest.
-->

## Summary

<!-- One or two sentences. What does this change and why? -->

## Type

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / cleanup (no behavior change)
- [ ] Documentation
- [ ] CI / tooling
- [ ] Other:

## Linked issues

<!-- e.g. Closes #12, Refs #34 -->

## Tested on

<!-- Which distro / desktop did you actually run this on? Tick what applies. -->

- [ ] KDE Plasma 6 (Wayland)
- [ ] KDE Plasma 6 (X11)
- [ ] GNOME (with AppIndicator extension)
- [ ] Other DE (specify):
- [ ] Apple Music Web playback verified
- [ ] Bluetooth (AVRCP) playback verified
- [ ] Discord RPC visible in profile

## Checklist

- [ ] `pytest` passes locally
- [ ] `ruff check .` and `ruff format --check .` are clean
- [ ] New / changed behavior is covered by tests (or unreachable from tests due to system integration — explain)
- [ ] No new private data leaks into the repo (no real Discord client IDs other than the project's, no MAC addresses, no tokens)
- [ ] If user-visible strings changed, they're English
- [ ] If the version was bumped, both `pyproject.toml` and `src/refrain/__init__.py` match
- [ ] README / CHANGELOG updated if behavior changed

## Screenshots (UI changes only)
