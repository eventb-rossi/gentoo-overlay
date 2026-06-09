#!/usr/bin/env python3
"""Upstream version monitor for the eventb-rossi overlay.

Two responsibilities, mirroring the two halves of homebrew-tap's release tracker:

  * "bump" packages have a single distfile and a clean ${PV}-templated SRC_URI,
    so a version bump is mechanical: copy the ebuild to the new version and
    regenerate the thin Manifest by downloading the distfile(s) and hashing them.
    These are turned into pull requests.

  * "track" packages embed opaque build ids, bundle vendored archives, or carry
    detached signatures, so they can't be bumped blindly. We only detect the new
    version and file a GitHub issue for a human to handle.

No Gentoo/Portage environment is required: the overlay uses thin manifests
(metadata/layout.conf: thin-manifests = true), so a Manifest is just
`DIST <name> <size> BLAKE2B <hex> SHA512 <hex>` lines, which we compute with
hashlib. Verified to reproduce Portage's output byte-for-byte.

Subcommands:
  check        Print a JSON report of every package (current/latest/outdated).
  bump <atom>  For one outdated "bump" atom, write the new ebuild + Manifest
               into the working tree. No-op (exit 0) if already up to date.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(os.environ.get("OVERLAY_ROOT") or Path(__file__).resolve().parents[2])

# --- package configuration -------------------------------------------------
# mode: "bump"  -> auto-bump into a PR (single distfile, clean ${PV} SRC_URI)
#       "track" -> detect only, file an issue (opaque/complex bump)
#       "skip"  -> not monitored (moving-master snapshot or key bundle)
#
# source.type:
#   github       -> latest non-prerelease GitHub release tag (repo = owner/name)
#   sourceforge  -> newest version directory under a SourceForge files path
#   atelierb     -> scrape the Atelier B download page for the free version
#   apache_index -> newest X.Y.Z subdirectory in an Apache/nginx autoindex
PACKAGES = [
    {"atom": "sci-mathematics/eventb-checker", "mode": "bump",
     "source": {"type": "github", "repo": "eventb-rossi/eventb-checker"}},
    {"atom": "sci-mathematics/eventb-animate", "mode": "bump",
     "source": {"type": "github", "repo": "eventb-rossi/eventb-animate"}},
    {"atom": "sci-mathematics/evbt", "mode": "bump",
     "source": {"type": "github", "repo": "viklauverk/EventBTool"}},
    {"atom": "sci-mathematics/eventb-to-txt", "mode": "bump",
     "source": {"type": "github", "repo": "eventb-rossi/eventb-to-txt"}},
    # Both detect the version from the same host the distfile lives on, so a
    # detected version implies its release directory (and artifact) exists.
    {"atom": "sci-mathematics/prob2-ui", "mode": "bump",
     "source": {"type": "apache_index",
                "url": "https://stups.hhu-hosting.de/downloads/prob2/"}},
    {"atom": "sci-mathematics/prob-bin", "mode": "bump",
     "source": {"type": "apache_index",
                "url": "https://stups.hhu-hosting.de/downloads/prob/tcltk/releases/"}},

    {"atom": "sci-mathematics/tlc4b", "mode": "track",
     "source": {"type": "github", "repo": "hhu-stups/tlc4b"}},
    {"atom": "sci-mathematics/rodin", "mode": "track",
     "source": {"type": "sourceforge", "project": "rodin-b-sharp",
                "path": "/Core_Rodin_Platform"}},
    {"atom": "sci-mathematics/atelier-b", "mode": "track",
     "source": {"type": "atelierb",
                "url": "https://www.atelierb.eu/en/atelier-b-support-maintenance/download-atelier-b/"}},

    {"atom": "sci-mathematics/b2program", "mode": "skip", "source": {}},
    {"atom": "sec-keys/openpgp-keys-stups", "mode": "skip", "source": {}},
]

UA = {"User-Agent": "eventb-rossi-version-check/1 (+https://github.com/eventb-rossi/gentoo-overlay)"}


# --- helpers ---------------------------------------------------------------
def http_get(url: str, *, headers: dict | None = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# Leading dotted-numeric run of a version-ish string (drops a v-prefix / suffix).
VERSION_RE = re.compile(r"\d+(?:\.\d+)*")


def version_tuple(s: str) -> tuple[int, ...]:
    """`s` as ints, e.g. 'v3.10.0_rc2' -> (3,10,0).

    Numeric, not lexical, so 3.10 > 3.9. Suffixes (_rc, _p, -beta) are ignored,
    which is what we want for comparing against clean upstream releases.
    """
    m = VERSION_RE.search(s)
    return tuple(int(p) for p in m.group(0).split(".")) if m else ()


def version_newer(latest: str, current: str) -> bool:
    """True if `latest` is a newer release than `current`.

    Components are zero-padded to equal length before comparing, so a shorter
    string isn't treated as older than its own longer-but-equal form
    (3.10 == 3.10.0, while 3.10 > 3.9.0). Note: suffixes are ignored, so this
    can't tell a release from its own _rc (a known limitation of the track path).
    """
    a, b = version_tuple(latest), version_tuple(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def clean_pv(tag: str) -> str:
    """Upstream tag -> Gentoo PV, e.g. 'v1.4' -> '1.4'. Empty if no number."""
    m = VERSION_RE.search(tag)
    return m.group(0) if m else ""


# --- current (packaged) version -------------------------------------------
# PV is the dotted/suffixed version plus an optional -rN revision (which
# `[^-]*` alone would stop short of, dropping revisioned ebuilds entirely).
EBUILD_RE = re.compile(r"^(?P<pn>.+)-(?P<pv>\d[^-]*(?:-r\d+)?)\.ebuild$")


def package_dir(atom: str) -> Path:
    return REPO_ROOT / atom


def current_versions(atom: str) -> list[str]:
    pn = atom.split("/", 1)[1]
    out = []
    for f in package_dir(atom).glob(f"{pn}-*.ebuild"):
        m = EBUILD_RE.match(f.name)
        if m and m.group("pn") == pn:
            out.append(m.group("pv"))
    return out


def highest_current(atom: str) -> str | None:
    vs = current_versions(atom)
    return max(vs, key=version_tuple) if vs else None


# --- latest (upstream) version --------------------------------------------
PRERELEASE_RE = re.compile(r"(?:^|[-_.])(?:rc|alpha|beta|pre|dev|snapshot)", re.IGNORECASE)


def latest_github(repo: str) -> str:
    """Latest release tag, falling back to the highest stable tag.

    Not every repo marks a "latest" release (some only push tags, some publish
    only pre-releases), so `releases/latest` 404s there -> fall back to tags.
    """
    try:
        data = json.loads(http_get(f"https://api.github.com/repos/{repo}/releases/latest",
                                   headers=gh_headers()))
        return clean_pv(data["tag_name"])
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    tags = json.loads(http_get(f"https://api.github.com/repos/{repo}/tags?per_page=100",
                               headers=gh_headers()))
    names = [t["name"] for t in tags if not PRERELEASE_RE.search(t["name"])]
    if not names:
        raise ValueError(f"{repo}: no stable tags found")
    return clean_pv(max(names, key=version_tuple))


def latest_sourceforge(project: str, path: str) -> str:
    """Newest version directory directly under a SourceForge files path.

    Matches `<path>/<X.Y.Z>/` in the files RSS; a trailing `/` is required, so
    decorated dirs like `3.10-RC2/` (release candidates) are skipped.
    """
    rss = http_get(f"https://sourceforge.net/projects/{project}/rss?path={path}").decode("utf-8", "replace")
    pat = re.escape(path.rstrip("/")) + r"/(\d+(?:\.\d+)*)/"
    versions = re.findall(pat, rss)
    if not versions:
        raise ValueError(f"no versions matched under {path}")
    return max(versions, key=version_tuple)


def latest_atelierb(url: str) -> str:
    page = http_get(url).decode("utf-8", "replace")
    versions = re.findall(r"atelierb-free-(\d+(?:\.\d+)+)-", page)
    if not versions:
        raise ValueError("no atelierb-free-<version> link found")
    return max(versions, key=version_tuple)


def latest_apache_index(url: str) -> str:
    """Newest multi-component version subdirectory in an Apache/nginx autoindex.

    Only dotted-numeric names with at least two components count: the trailing
    `/"` anchor skips decorated siblings (betas, rcs, 'final', 'profile'), and
    requiring a dot skips non-version dirs such as a bare year archive.
    """
    page = http_get(url).decode("utf-8", "replace")
    versions = re.findall(r'href="(\d+(?:\.\d+)+)/"', page)
    if not versions:
        raise ValueError(f"no version directories found at {url}")
    return max(versions, key=version_tuple)


def latest_version(source: dict) -> str:
    t = source["type"]
    if t == "github":
        return latest_github(source["repo"])
    if t == "sourceforge":
        return latest_sourceforge(source["project"], source["path"])
    if t == "atelierb":
        return latest_atelierb(source["url"])
    if t == "apache_index":
        return latest_apache_index(source["url"])
    raise ValueError(f"unknown source type {t!r}")


# --- SRC_URI -> distfiles --------------------------------------------------
SRC_URI_RE = re.compile(r'SRC_URI=(?P<q>["\'])(?P<body>.*?)(?P=q)', re.DOTALL)


def parse_distfiles(ebuild_text: str, pn: str, pv: str) -> list[tuple[str, str]]:
    """Return [(url, dest_filename)] from a simple ${PV}-templated SRC_URI.

    Substitutes the common PMS vars and rejects (raises) anything fancier
    (USE-conditionals, unresolved ${...}), so we never emit a wrong Manifest.
    """
    m = SRC_URI_RE.search(ebuild_text)
    if not m:
        raise ValueError("no SRC_URI found")
    body = m.group("body")
    # Only the braced ${VAR} form is expanded: substituting bare $P would also
    # eat the "$P" inside "$PN"/"$PV". Any leftover "$" then reliably means an
    # unresolved construct, which the guard below rejects rather than guessing.
    subs = {"P": f"{pn}-{pv}", "PN": pn, "PV": pv}
    for var, val in subs.items():
        body = body.replace("${%s}" % var, val)
    if "$" in body or "(" in body or "?" in body:
        raise ValueError(f"unsupported SRC_URI construct after substitution: {body!r}")

    tokens = body.split()
    files: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        url = tokens[i]
        if i + 2 < len(tokens) and tokens[i + 1] == "->":
            files.append((url, tokens[i + 2]))
            i += 3
        else:
            files.append((url, url.rsplit("/", 1)[-1]))
            i += 1
    return files


def manifest_line(dest: str, url: str, timeout: int = 120) -> str:
    """Stream `url` and return its thin-Manifest DIST line.

    Distfiles here run to ~100 MB (e.g. eventb-animate), so hash incrementally
    in fixed-size chunks instead of buffering the whole artifact in memory.
    No auth header: release-asset downloads redirect to a CDN that rejects a
    forwarded Authorization.
    """
    b2, sha, size = hashlib.blake2b(), hashlib.sha512(), 0
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            b2.update(chunk)
            sha.update(chunk)
            size += len(chunk)
    return f"DIST {dest} {size} BLAKE2B {b2.hexdigest()} SHA512 {sha.hexdigest()}"


# --- subcommands -----------------------------------------------------------
def cmd_check() -> int:
    report = []
    for pkg in PACKAGES:
        atom, mode = pkg["atom"], pkg["mode"]
        current = highest_current(atom)
        entry = {"atom": atom, "mode": mode, "current": current,
                 "latest": None, "outdated": False, "error": None}
        if mode != "skip":
            try:
                latest = latest_version(pkg["source"])
                entry["latest"] = latest
                entry["outdated"] = bool(latest and current
                                         and version_newer(latest, current))
            except Exception as exc:  # detection is best-effort; never fail the run
                entry["error"] = f"{type(exc).__name__}: {exc}"
        report.append(entry)
    print(json.dumps(report, indent=2))
    return 0


def emit_outputs(**kv: str) -> None:
    """Append key=value lines to $GITHUB_OUTPUT when running under Actions."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


def cmd_bump(atom: str) -> int:
    pkg = next((p for p in PACKAGES if p["atom"] == atom), None)
    if not pkg or pkg["mode"] != "bump":
        print(f"refusing to bump {atom!r}: not a configured bump package", file=sys.stderr)
        return 2

    pn = atom.split("/", 1)[1]
    current = highest_current(atom)
    latest = latest_version(pkg["source"])
    if not (current and latest and version_newer(latest, current)):
        print(f"{atom}: up to date ({current}, upstream {latest})")
        emit_outputs(bumped="false")
        return 0

    pdir = package_dir(atom)
    src_ebuild = pdir / f"{pn}-{current}.ebuild"
    new_ebuild = pdir / f"{pn}-{latest}.ebuild"
    text = src_ebuild.read_text()
    distfiles = parse_distfiles(text, pn, latest)  # validate SRC_URI before touching the tree

    # PV comes from the filename, so the ebuild body is copied verbatim.
    new_ebuild.write_text(text)
    print(f"{atom}: {current} -> {latest}")
    print(f"  created {new_ebuild.relative_to(REPO_ROOT)}")

    # Regenerate the thin Manifest: keep existing DIST lines (other versions),
    # add/replace the new version's distfiles. Sorted by filename, like Portage.
    lines: dict[str, str] = {}
    manifest = pdir / "Manifest"
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            if line.startswith("DIST "):
                lines[line.split()[1]] = line

    for url, dest in distfiles:
        print(f"  fetching {url}")
        lines[dest] = manifest_line(dest, url)

    manifest.write_text("".join(f"{lines[k]}\n" for k in sorted(lines)))
    print(f"  wrote {manifest.relative_to(REPO_ROOT)} ({len(lines)} DIST entries)")
    emit_outputs(bumped="true", pn=pn, old=current, new=latest)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "check":
        return cmd_check()
    if len(argv) >= 2 and argv[0] == "bump":
        return cmd_bump(argv[1])
    print(__doc__)
    print("usage: version_check.py check | bump <category/package>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
