# Releases

Every version number Refrain has used, and where it can be downloaded
today. A version that can still be downloaded has to work: on 2026-09-18
every published version was installed and started again (Ubuntu 24.04,
Apple Music in Chrome on KDE Plasma), and whatever failed was taken down.
Tags stay in the repository, so the [CHANGELOG](../CHANGELOG.md) links
keep working.

## Available

| Version | Released | Get it from |
|---------|----------|-------------|
| 0.5.2 | 2026-09-18 | PyPI, AUR, GitHub |
| 0.5.1 | 2026-09-15 | PyPI, GitHub |
| 0.4.6 | 2026-08-26 | PyPI, GitHub |
| 0.4.5 | 2026-08-24 | PyPI, GitHub |
| 0.4.4 | 2026-08-24 | PyPI, GitHub |

## Removed

| Versions | Reason |
|----------|--------|
| 0.4.3 | Broken at release; replaced by 0.4.4 the same day |
| 0.2.2, 0.2.3, 0.2.4, 0.2.5, 0.2.6, 0.2.7, 0.3.0, 0.4.0, 0.4.1, 0.4.2 | Crash in the GLib main loop; fixed in 0.4.4 |
| 0.2.0 | Tray error on every track change |
| 0.1.0, 0.1.1, 0.1.2, 0.1.3, 0.1.4, 0.1.5 | Don't recognise Apple Music in Chrome |
| AppImages of every version | Never started (`No module named refrain`); fixed from 0.5.3 on |

Removed means: the GitHub release is gone and the version is yanked on
PyPI, so pip never picks it again. 0.1.0–0.1.2 were never on PyPI, which
Refrain has used since 0.1.3.

## Never released

| Version | Why |
|---------|-----|
| 0.5.0 | Its changes shipped in 0.5.1 |
| 0.2.1 | Its changes shipped in 0.2.2 |
