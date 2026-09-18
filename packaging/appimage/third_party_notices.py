"""Write usr/share/doc/refrain/THIRD-PARTY-NOTICES and LICENSES/ into the AppDir.

Usage: third_party_notices.py <AppDir> <apt archives dir> <repo root>
"""

import re
import shutil
import subprocess
import sys
import textwrap
from email.parser import HeaderParser
from pathlib import Path

DIST_PACKAGES = "usr/lib/python3/dist-packages"
DOC_DIRS = ("usr/share/doc", "runtime/compat/usr/share/doc")
CONNECTORS = {"or", "and", "with", "exception"}

HEADER = """\
Refrain AppImage - third-party notices
======================================

The Refrain AppImage bundles third-party software that is not covered by the
Refrain License. Each component is distributed under its own licence, listed
below. Nothing in the Refrain License restricts the rights these licences grant:
you may modify or replace these components (for example, swap in your own build
of Qt or PySide6) and reverse-engineer the AppImage to debug such modifications.

Refrain itself: Refrain {refrain_version}, see LICENSE in this directory.

Licence texts referenced below (GPL-2, GPL-3, LGPL-2.1, LGPL-3, Apache-2.0, ...)
are in the LICENSES/ directory next to this file. Copyright and licence details
of every Ubuntu package are in usr/share/doc/<package>/copyright inside the
AppImage (runtime/compat/usr/share/doc/ for the C library).
"""

QT_SECTION = """\
Qt for Python (PySide6) and Qt
------------------------------

PySide6-Essentials {pyside}, shiboken6 {shiboken} and the Qt {qt} libraries
shipped inside the PySide6 wheel are used under the GNU Lesser General Public
License v3 (LICENSES/LGPL-3, which refers to LICENSES/GPL-3). Copyright (C) The
Qt Company Ltd. and other contributors. Only the Qt modules Refrain uses
(Core, Gui, Widgets, Svg, DBus, Network and the libraries they load) are
bundled; no GPL-only Qt module is included.

Qt contains third-party code under its own licences, listed at
https://doc.qt.io/qt-6/licenses-used-in-qt.html

Replacing Qt or PySide6: Refrain loads them as ordinary Python modules and
shared libraries from

  {dist}/PySide6
  {dist}/shiboken6

Run the AppImage with --appimage-extract, replace those two directories with
your own build (same Python ABI), then start squashfs-root/AppRun or repack the
directory with appimagetool.
"""

SOURCE_SECTION = """\
Source code
-----------

Source code for the LGPL/GPL-licensed components in this AppImage:

  PySide6 / shiboken6 {pyside}:
    https://download.qt.io/official_releases/QtForPython/
    https://pypi.org/project/PySide6-Essentials/{pyside}/#files
  Qt {qt}:
    https://download.qt.io/official_releases/qt/
  Ubuntu 24.04 packages (source package and exact version listed above):
    https://launchpad.net/ubuntu/+source/<source package>/<version>
    or: apt-get source <source package>=<version>

On request, for three years after the release of this AppImage, the complete
corresponding source code of every component listed here will be provided for
no more than the cost of physically performing the distribution. Contact:
contact@rockykln.com
"""


def read_metadata(dist_info):
    return HeaderParser().parsestr((dist_info / "METADATA").read_text(encoding="utf-8"))


def deb_fields(deb):
    out = subprocess.run(
        ["dpkg-deb", "-f", str(deb), "Package", "Version", "Source"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return HeaderParser().parsestr(out)


def compare_versions(a, b):
    if a == b:
        return 0
    return 1 if subprocess.run(["dpkg", "--compare-versions", a, "gt", b]).returncode == 0 else -1


def newest_debs(archives):
    # A reused build cache can hold several versions; apt deploys the newest.
    newest = {}
    for deb in archives.glob("*.deb"):
        fields = deb_fields(deb)
        package, version = fields["Package"], fields["Version"]
        if package not in newest or compare_versions(version, newest[package]["Version"]) > 0:
            newest[package] = fields
    return newest


def looks_like_identifier(value):
    # DEP-5 files sometimes put licence prose after "License:"; keep only short names.
    if not value or len(value) > 60 or "'" in value or value.endswith("."):
        return False
    for token in re.split(r"[\s,]+", value):
        if token and token.isalpha() and token.islower() and token not in CONNECTORS:
            return False
    return True


def copyright_licenses(path):
    if not path.is_file():
        return "no copyright file"
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("Format:"):
        return "see copyright file"
    found = []
    for match in re.finditer(r"^License:[ \t]*(.*)$", text, re.MULTILINE):
        value = match.group(1).strip()
        if looks_like_identifier(value) and value not in found:
            found.append(value)
    return "; ".join(found) or "see copyright file"


def debian_packages(appdir, archives):
    rows = []
    for package, fields in sorted(newest_debs(archives).items()):
        roots = [r for r in DOC_DIRS if (appdir / r / package).exists()]
        if not roots:
            continue
        # glibc moves to runtime/compat and leaves an empty doc dir behind.
        with_copyright = [r for r in roots if (appdir / r / package / "copyright").is_file()]
        doc_root = (with_copyright or roots)[0]
        version = fields["Version"]
        source = fields["Source"] or package
        if "(" not in source:
            source = f"{source} ({version})"
        licences = copyright_licenses(appdir / doc_root / package / "copyright")
        rows.append((package, version, source, licences, doc_root))
    return rows


def python_packages(dist):
    rows = []
    for info in sorted(dist.glob("*.dist-info")):
        meta = read_metadata(info)
        name = meta["Name"]
        if name.lower() == "refrain":
            continue
        licence = meta["License-Expression"] or meta["License"] or "see package metadata"
        files = sorted(p for p in (info / "licenses").rglob("*") if p.is_file())
        rows.append((name, meta["Version"], licence, files))
    return rows


def main():
    appdir, archives, repo = (Path(a).resolve() for a in sys.argv[1:4])
    dist = appdir / DIST_PACKAGES
    out = appdir / "usr/share/doc/refrain"
    licenses = out / "LICENSES"
    shutil.rmtree(out, ignore_errors=True)
    licenses.mkdir(parents=True)

    shutil.copy(repo / "LICENSE", out / "LICENSE")
    for text in sorted(Path("/usr/share/common-licenses").iterdir()):
        if text.is_file():
            shutil.copy(text, licenses / text.name)

    refrain_info = next(dist.glob("refrain-*.dist-info"))
    python_rows = python_packages(dist)
    versions = {name.lower(): version for name, version, _, _ in python_rows}
    pyside = versions["pyside6_essentials"]

    parts = [HEADER.format(refrain_version=read_metadata(refrain_info)["Version"])]
    parts.append(
        QT_SECTION.format(
            pyside=pyside, shiboken=versions["shiboken6"], qt=pyside, dist=DIST_PACKAGES
        )
    )

    lines = ["Python packages installed with pip", "-" * 34, ""]
    for name, version, licence, files in python_rows:
        copied = []
        for f in files:
            target = licenses / f"{name}-{f.name}"
            shutil.copy(f, target)
            copied.append(f"LICENSES/{target.name}")
        if not copied and name.lower().startswith(("pyside6", "shiboken6")):
            copied = ["LICENSES/LGPL-3", "LICENSES/GPL-3"]
        where = ", ".join(copied) or "see package metadata"
        lines.append(f"  {name} {version}")
        lines.append(f"    Licence: {licence}")
        lines.append(f"    Text:    {where}")
    parts.append("\n".join(lines) + "\n")

    lines = [
        "Ubuntu 24.04 (noble) packages",
        "-" * 29,
        "",
        "Includes Python (PSF-2.0), GLib and PyGObject (LGPL-2.1+), the GNU C",
        "library (LGPL-2.1+), libdbus (AFL-2.1 or GPL-2+) and dbus-python (MIT).",
        "Licence names are taken from each package's copyright file.",
        "",
    ]
    for package, version, source, licence, doc_root in debian_packages(appdir, archives):
        lines.append(f"  {package} {version}")
        lines.append(f"    Source:  {source}")
        lines.append(
            textwrap.fill(
                licence,
                width=78,
                initial_indent="    Licence: ",
                subsequent_indent=" " * 13,
                break_on_hyphens=False,
                break_long_words=False,
            )
        )
        if doc_root != DOC_DIRS[0]:
            lines.append(f"    Details: {doc_root}/{package}/copyright")
    parts.append("\n".join(lines) + "\n")

    parts.append(SOURCE_SECTION.format(pyside=pyside, qt=pyside))
    (out / "THIRD-PARTY-NOTICES").write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
