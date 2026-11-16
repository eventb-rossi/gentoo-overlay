#!/usr/bin/env python3
"""Upstream version monitor for the eventb-rossi overlay.

Two responsibilities, mirroring the two halves of homebrew-tap's release tracker:

  * "bump" packages have a single distfile and a clean ${PV}-templated SRC_URI,
    so a version bump is mechanical: copy the ebuild to the new version and
    regenerate the thin Manifest by downloading the distfile(s) and hashing them.
    These are turned into pull requests. sci-mathematics/rodin (Rodin-Bundle) is
    a bump package with a dedicated path, cmd_bump_rodin_bundle: its asset name
    embeds an opaque build qualifier (MY_BUILD), and GitHub prereleases land as
    masked _rc ebuilds.

  * "cargo" packages are cargo-eclass ebuilds: a bump isn't a verbatim copy
    because the CRATES= and LICENSE= blocks are generated from Cargo.lock and the
    Manifest must hash the app tarball plus every crate distfile. We regenerate
    those with pycargoebuild, splice them into the previous ebuild, rebuild the
    Manifest, and open a PR. The workflow build+tests the result before merging
    and falls back to a tracking issue on failure.

  * "track" packages embed opaque build ids, bundle vendored archives, or carry
    detached signatures, so they can't be bumped blindly. We only detect the new
    version and file a GitHub issue for a human to handle.

No Gentoo/Portage environment is required: the overlay uses thin manifests
(metadata/layout.conf: thin-manifests = true), so a Manifest is just
`DIST <name> <size> BLAKE2B <hex> SHA512 <hex>` lines, which we compute with
hashlib. Verified to reproduce Portage's output byte-for-byte.

Subcommands:
  check              Print a JSON report of every package (current/latest/outdated).
  bump <atom>        For one outdated "bump" atom, write the new ebuild + Manifest
                     into the working tree. No-op (exit 0) if already up to date.
  bump-cargo <atom>  Like bump, but for a "cargo" atom: regenerate CRATES/LICENSE
                     with pycargoebuild and rebuild the Manifest from the crate
                     distfiles. Requires pycargoebuild (python3 -m pycargoebuild).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(os.environ.get("OVERLAY_ROOT") or Path(__file__).resolve().parents[2])

# --- package configuration -------------------------------------------------
# mode: "bump"  -> auto-bump into a PR (single distfile, clean ${PV} SRC_URI)
#       "cargo" -> auto-bump a cargo-eclass ebuild into a PR by regenerating
#                  CRATES/LICENSE with pycargoebuild (needs source.cargo_member)
#       "track" -> detect only, file an issue (opaque/complex bump)
#       "skip"  -> not monitored (moving-master snapshot or key bundle)
#
# source.type:
#   github       -> latest non-prerelease GitHub release tag (repo = owner/name)
#   atelierb     -> scrape the Atelier B download page for the free version
#   apache_index -> newest X.Y.Z subdirectory in an Apache/nginx autoindex
#   rodin_bundle -> eventb-rossi/Rodin-Bundle releases, tags vX.Y.Z-YYMM[.N][-rcN]
#                   (repo = owner/name; prerelease = also package -rc releases
#                   as masked _rc ebuilds). Bumped by cmd_bump_rodin_bundle.
PACKAGES = [
    {"atom": "sci-mathematics/eventb-checker", "mode": "bump",
     "source": {"type": "github", "repo": "eventb-rossi/eventb-checker"}},
    {"atom": "sci-mathematics/eventb-animate", "mode": "bump",
     "source": {"type": "github", "repo": "eventb-rossi/eventb-animate"}},
    {"atom": "sci-mathematics/evbt", "mode": "bump",
     "source": {"type": "github", "repo": "viklauverk/EventBTool"}},
    {"atom": "sci-mathematics/eventb-to-txt", "mode": "bump",
     "source": {"type": "github", "repo": "eventb-rossi/eventb-to-txt"}},
    {"atom": "sci-mathematics/rodin-headless", "mode": "bump",
     "source": {"type": "github", "repo": "eventb-rossi/rodin-headless"}},
    # Detect versions from the release index; the bump validates the distfile.
    {"atom": "sci-mathematics/prob2-ui", "mode": "bump",
     "source": {"type": "apache_index",
                "url": "https://stups.hhu-hosting.de/downloads/prob2/"}},
    {"atom": "sci-mathematics/prob-bin", "mode": "bump",
     "source": {"type": "apache_index",
                "url": "https://stups.hhu-hosting.de/downloads/prob/tcltk/releases/"}},
    # The bump rewrites MY_BUILD from the release asset, keeps one stable
    # ebuild, and adds prereleases as masked _rc ebuilds (cmd_bump_rodin_bundle).
    {"atom": "sci-mathematics/rodin", "mode": "bump",
     "source": {"type": "rodin_bundle", "repo": "eventb-rossi/Rodin-Bundle",
                "prerelease": True}},

    {"atom": "sci-mathematics/tlc4b", "mode": "track",
     "source": {"type": "github", "repo": "hhu-stups/tlc4b"}},
    {"atom": "sci-mathematics/ltsmin", "mode": "track",
     "source": {"type": "github", "repo": "utwente-fmt/ltsmin"}},
    # Cargo workspace: the bump regenerates CRATES/LICENSE/Manifest with
    # pycargoebuild (cmd_bump_cargo). cargo_member is the workspace member to run
    # pycargoebuild in — it refuses at a virtual workspace root, and any member
    # yields the full crate set via the shared Cargo.lock.
    {"atom": "sci-mathematics/rossi", "mode": "cargo",
     "source": {"type": "github", "repo": "eventb-rossi/rossi",
                "cargo_member": "crates/rossi"}},
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


def highest_current_stable(atom: str) -> str | None:
    """Highest packaged version, ignoring pre-release ebuilds (_rc/_alpha/...).

    version_tuple strips suffixes, so highest_current() would rank a masked
    `_rc` ebuild equal to its stable form and could return the rc — which a
    cargo bump must never treat as `current` (it would compare/delete the
    wrong, package.mask-protected ebuild). CLAUDE.md allows a masked `_rc` to
    coexist with the stable version, so the auto-bumper targets the stable line.
    """
    vs = [v for v in current_versions(atom) if not PRERELEASE_RE.search(v)]
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


# --- Rodin-Bundle -----------------------------------------------------------
# Tags are v3.10.0-2611, v3.10.0-2609.2 (a second release in one month) and
# v3.10.0-2611-rc1 (published as a GitHub prerelease). The PV keeps every part,
# so 3.10.0_p2611_rc1 < 3.10.0_p2611 < 3.10.0_p2611_p1 < 3.10.0_p2612, both
# in Portage and in bundle_key (version_tuple can't tell _p2611 from _p2612).
BUNDLE_TAG_RE = re.compile(
    r"^v(?P<base>\d+(?:\.\d+)*)-(?P<yymm>\d{4})(?:\.(?P<n>\d+))?(?:-rc(?P<rc>\d+))?$")
BUNDLE_PV_RE = re.compile(
    r"^(?P<base>\d+(?:\.\d+)*)_p(?P<yymm>\d{4})(?:_p(?P<n>\d+))?(?:_rc(?P<rc>\d+))?(?:-r\d+)?$")
# The Linux product archive. <build>, the OSGi qualifier (commit date + hash
# since 2611, a bare hash before), can't be derived from the tag; the ebuild
# keeps it in MY_BUILD.
BUNDLE_ASSET_RE = re.compile(
    r"^(?P<pn>[\w-]+?)-(?P<base>\d+(?:\.\d+)*)\.(?P<build>[0-9A-Za-z][0-9A-Za-z-]*?)"
    r"-rossi-linux\.gtk\.x86_64\.tar\.gz$")
MY_BUILD_RE = re.compile(r'^MY_BUILD="[^"]*"$', re.MULTILINE)
_FINAL = 1 << 30  # rc component of a final release: an -rcN sorts below it


def bundle_key(parts: dict) -> tuple:
    """Ordering key from BUNDLE_TAG_RE / BUNDLE_PV_RE groups."""
    base = version_tuple(parts["base"])
    rc = parts.get("rc")
    return (base + (0,) * (4 - len(base)), int(parts["yymm"]),
            int(parts.get("n") or 0), int(rc) if rc else _FINAL)


def bundle_pv_key(pv: str) -> tuple:
    """Ordering key of a packaged PV.

    A PV without the _pYYMM part (vanilla Rodin, e.g. 3.10.0) sorts below every
    bundle release of the same base, so the bundle supersedes it.
    """
    m = BUNDLE_PV_RE.match(pv)
    if m:
        return bundle_key(m.groupdict())
    base = version_tuple(pv)
    return (base + (0,) * (4 - len(base)), -1, 0, _FINAL)


def bundle_pv(parts: dict) -> str:
    """Tag groups -> PV: v3.10.0-2609.2-rc1 -> 3.10.0_p2609_p2_rc1."""
    pv = f"{parts['base']}_p{parts['yymm']}"
    if parts.get("n"):
        pv += f"_p{parts['n']}"
    if parts.get("rc"):
        pv += f"_rc{parts['rc']}"
    return pv


def bundle_distfile(pn: str, pv: str, ebuild_text: str) -> str:
    """The distfile name an existing bundle ebuild fetches (its SRC_URI)."""
    m = BUNDLE_PV_RE.match(pv)
    build = re.search(r'^MY_BUILD="([^"]*)"$', ebuild_text, re.MULTILINE)
    if not (m and build):
        raise ValueError(f"{pn}-{pv}.ebuild: not a Rodin-Bundle ebuild (PV or MY_BUILD)")
    return f"{pn}-{m['base']}.{build.group(1)}-rossi-linux.gtk.x86_64.tar.gz"


def bundle_releases(repo: str) -> list[dict]:
    """Complete, non-draft Rodin-Bundle releases, oldest first.

    A release counts only once both its Linux archive and SHA256SUMS are
    attached (release.yml uploads SHA256SUMS last), so one still being published
    is picked up by a later run instead of half-packaged. A prerelease must be an
    -rcN tag and vice versa (release.yml marks exactly those); anything else is
    skipped rather than packaged under a misleading version.
    """
    data = json.loads(http_get(f"https://api.github.com/repos/{repo}/releases?per_page=100",
                               headers=gh_headers()))
    out = []
    for rel in data:
        m = BUNDLE_TAG_RE.match(rel.get("tag_name") or "")
        if rel.get("draft") or not m:
            continue
        parts = m.groupdict()
        if bool(rel.get("prerelease")) != bool(parts["rc"]):
            continue
        assets = rel.get("assets") or []
        if not any(a.get("name") == "SHA256SUMS" for a in assets):
            continue
        linux = [(a, am) for a in assets
                 if (am := BUNDLE_ASSET_RE.match(a.get("name") or ""))
                 and am["base"] == parts["base"]]
        if len(linux) != 1:
            continue
        asset, am = linux[0]
        digest = asset.get("digest") or ""
        out.append({
            "pv": bundle_pv(parts), "key": bundle_key(parts), "tag": rel["tag_name"],
            "prerelease": bool(parts["rc"]), "build": am["build"],
            "distfile": asset["name"], "url": asset["browser_download_url"],
            "sha256": digest[len("sha256:"):] if digest.startswith("sha256:") else None,
        })
    return sorted(out, key=lambda r: r["key"])


def rodin_bundle_plan(atom: str, source: dict) -> dict:
    """Decide what a bump of `atom` would do.

    Returns {"current", "latest", "target"}: `current` is the highest packaged
    stable PV (a coexisting masked _rc is not the baseline); `target` is the
    release to package, or None when up to date:
      * the newest stable release, if newer than `current`;
      * else, with source.prerelease, the newest -rc release if it is newer
        than `current` and than every packaged _rc ebuild.
    """
    pvs = current_versions(atom)
    stable = [v for v in pvs if not PRERELEASE_RE.search(v)]
    rcs = [v for v in pvs if PRERELEASE_RE.search(v)]
    current = max(stable, key=bundle_pv_key) if stable else None

    rels = bundle_releases(source["repo"])
    finals = [r for r in rels if not r["prerelease"]]
    if not finals:
        raise ValueError(f"{source['repo']}: no complete stable release")
    latest = finals[-1]
    target = None
    if current is None or latest["key"] > bundle_pv_key(current):
        target = latest
    elif source.get("prerelease"):
        pres = [r for r in rels if r["prerelease"]]
        floor = max(bundle_pv_key(v) for v in [current, *rcs])
        if pres and pres[-1]["key"] > floor:
            target = pres[-1]
    return {"current": current, "latest": (target or latest)["pv"], "target": target}


def latest_version(source: dict) -> str:
    t = source["type"]
    if t == "github":
        return latest_github(source["repo"])
    if t == "rodin_bundle":
        return [r for r in bundle_releases(source["repo"]) if not r["prerelease"]][-1]["pv"]
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


def _dist_line(name: str, chunks) -> str:
    """Thin-Manifest DIST line from an iterable of byte chunks.

    Distfiles here run to ~100 MB (e.g. eventb-animate), so hash incrementally
    instead of buffering the whole artifact in memory. The format is the single
    source of truth for both the URL and local-file producers below.
    """
    b2, sha, size = hashlib.blake2b(), hashlib.sha512(), 0
    for chunk in chunks:
        b2.update(chunk)
        sha.update(chunk)
        size += len(chunk)
    return f"DIST {name} {size} BLAKE2B {b2.hexdigest()} SHA512 {sha.hexdigest()}"


def manifest_line(dest: str, url: str, timeout: int = 120) -> str:
    """Stream `url` and return its thin-Manifest DIST line.

    No auth header: release-asset downloads redirect to a CDN that rejects a
    forwarded Authorization.
    """
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _dist_line(dest, iter(lambda: resp.read(1 << 20), b""))


def manifest_line_file(path: Path) -> str:
    """Thin-Manifest DIST line for a local file (named by its basename).

    For files already on disk: the crate distfiles pycargoebuild downloaded and
    the app tarball.
    """
    with open(path, "rb") as fh:
        return _dist_line(path.name, iter(lambda: fh.read(1 << 20), b""))


def manifest_line_prob2_deb(dest: str, url: str, pv: str) -> str:
    """Hash a ProB2-UI Debian release only if it contains the launchable jar."""
    with tempfile.TemporaryDirectory(prefix="prob2-ui-bump-") as work:
        archive = Path(work) / dest
        download_to(url, archive)
        subprocess.run(["dpkg-deb", "--extract", str(archive), work], check=True)
        jar = Path(work) / "opt/prob2-ui/lib/app" / f"prob2-ui-{pv}-linux.jar"
        if not jar.is_file():
            raise ValueError(f"{url}: missing {jar.relative_to(work)}")
        with zipfile.ZipFile(jar) as zf:
            manifest = zf.read("META-INF/MANIFEST.MF").decode("utf-8")
        if "Main-Class: de.prob2.ui.Main" not in manifest.splitlines():
            raise ValueError(f"{url}: jar has no ProB2-UI Main-Class")
        return manifest_line_file(archive)


# --- subcommands -----------------------------------------------------------
def cmd_check() -> int:
    report = []
    for pkg in PACKAGES:
        atom, mode = pkg["atom"], pkg["mode"]
        # Compare against the stable line, ignoring a coexisting masked _rc.
        # version_tuple strips suffixes, so highest_current() ranks a masked
        # `_rc` equal to its stable form: a final release equal to the packaged
        # rc (3.10.0 vs 3.10.0_rc2, 0.2.0 vs 0.2.0_rc1) would then compare
        # not-newer and be missed. cargo bumps already relied on this; all modes
        # need it. Fall back to highest_current() only when no stable ebuild
        # exists (an rc-only package, where the rc is the only baseline).
        current = highest_current_stable(atom) or highest_current(atom)
        entry = {"atom": atom, "mode": mode, "current": current,
                 "latest": None, "outdated": False, "error": None}
        if mode != "skip":
            try:
                if pkg["source"]["type"] == "rodin_bundle":
                    plan = rodin_bundle_plan(atom, pkg["source"])
                    entry.update(current=plan["current"], latest=plan["latest"],
                                 outdated=plan["target"] is not None)
                    report.append(entry)
                    continue
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
    if pkg["source"]["type"] == "rodin_bundle":
        return cmd_bump_rodin_bundle(atom, pkg["source"])

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

    print(f"{atom}: {current} -> {latest}")

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
        if atom == "sci-mathematics/prob2-ui":
            lines[dest] = manifest_line_prob2_deb(dest, url, latest)
        else:
            lines[dest] = manifest_line(dest, url)

    # A missing or changed distfile must not leave a partial new ebuild.
    new_ebuild.write_text(text)
    print(f"  created {new_ebuild.relative_to(REPO_ROOT)}")
    manifest.write_text("".join(f"{lines[k]}\n" for k in sorted(lines)))
    print(f"  wrote {manifest.relative_to(REPO_ROOT)} ({len(lines)} DIST entries)")
    emit_outputs(bumped="true", pn=pn, old=current, new=latest)
    return 0


# --- Rodin-Bundle bump -------------------------------------------------------
RC_MASK_HEADER = (
    "# Denis Efremov <efremov@linux.com> ({date})\n"
    "# Rodin Bundle release candidates (GitHub prereleases), masked for\n"
    "# testing only. Unmask in /etc/portage/package.unmask to try them out.")


def manifest_line_verified(dest: str, url: str, sha256: str | None,
                           timeout: int = 180) -> str:
    """manifest_line(), also checking the download against the SHA-256 GitHub
    publishes for the asset, so a truncated download never lands in a Manifest."""
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        def chunks():
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                h.update(chunk)
                yield chunk
        line = _dist_line(dest, chunks())
    if sha256 and h.hexdigest() != sha256:
        raise ValueError(f"{url}: sha256 {h.hexdigest()} != published {sha256}")
    return line


def update_rc_mask(text: str, atom: str, add: str | None, remove: list[str]) -> str:
    """Return profiles/package.mask `text` with `atom`'s _rc entries updated.

    Our block is the one whose entries are all `~atom-<pv>_rc*`: drop the
    removed PVs from it (and the whole block once empty), and append `add`
    to it, starting a new dated block if there is none. Other blocks are
    left untouched.
    """
    def ours(entry: str) -> bool:
        return entry.startswith(f"~{atom}-") and bool(PRERELEASE_RE.search(entry))

    gone = {f"~{atom}-{pv}" for pv in remove}
    blocks, mine = [], None
    for block in re.split(r"\n[ \t]*\n", text.strip("\n")):
        lines = block.splitlines()
        entries = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
        if entries and all(ours(e) for e in entries):
            lines = [ln for ln in lines if ln not in gone]
            if not any(ln.strip() and not ln.startswith("#") for ln in lines):
                continue  # every masked rc of this block is gone
            mine = len(blocks)
        if block.strip():
            blocks.append(lines)
    if add:
        if mine is None:
            header = RC_MASK_HEADER.format(date=datetime.date.today().isoformat())
            blocks.append(header.splitlines())
            mine = len(blocks) - 1
        blocks[mine].append(f"~{atom}-{add}")
    return "\n\n".join("\n".join(b) for b in blocks) + ("\n" if blocks else "")


def cmd_bump_rodin_bundle(atom: str, source: dict) -> int:
    """Package the release rodin_bundle_plan() picked.

    The new ebuild is the newest stable one with MY_BUILD swapped for the
    release's build qualifier. A stable release supersedes every older ebuild,
    a masked _rc one included (one version per package); a prerelease is added
    as a masked _rc ebuild next to the stable one and supersedes older _rc ones.
    """
    pn = atom.split("/", 1)[1]
    plan = rodin_bundle_plan(atom, source)
    rel = plan["target"]
    if rel is None:
        print(f"{atom}: up to date ({plan['current']}, upstream {plan['latest']})")
        emit_outputs(bumped="false")
        return 0

    pdir = package_dir(atom)
    pvs = current_versions(atom)
    if not pvs:
        raise ValueError(f"{atom}: no ebuild to start from")
    stable = [v for v in pvs if not PRERELEASE_RE.search(v)]
    template = max(stable or pvs, key=bundle_pv_key)
    text = (pdir / f"{pn}-{template}.ebuild").read_text()
    if len(MY_BUILD_RE.findall(text)) != 1:
        raise ValueError(f"{pn}-{template}.ebuild: expected exactly one MY_BUILD= line")
    new_text = MY_BUILD_RE.sub(f'MY_BUILD="{rel["build"]}"', text)
    # The ebuild derives the asset name from PV + MY_BUILD; make sure that is
    # the asset we are about to hash.
    if bundle_distfile(pn, rel["pv"], new_text) != rel["distfile"]:
        raise ValueError(f"{rel['tag']}: asset {rel['distfile']} does not match "
                         f"{pn}-{rel['pv']}.ebuild's SRC_URI")

    if rel["prerelease"]:
        drop = [v for v in pvs if PRERELEASE_RE.search(v) and bundle_pv_key(v) < rel["key"]]
    else:
        drop = [v for v in pvs if bundle_pv_key(v) < rel["key"]]
    drop.sort(key=bundle_pv_key)
    keep = [v for v in pvs if v not in drop]

    print(f"{atom}: {plan['current']} -> {rel['pv']} ({rel['tag']})")

    # Thin Manifest: the kept ebuilds' DIST lines plus the new distfile.
    manifest = pdir / "Manifest"
    old_lines = {}
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            if line.startswith("DIST "):
                old_lines[line.split()[1]] = line
    lines = {}
    for v in keep:
        name = bundle_distfile(pn, v, (pdir / f"{pn}-{v}.ebuild").read_text())
        if name not in old_lines:
            raise ValueError(f"Manifest has no DIST entry for {name}")
        lines[name] = old_lines[name]
    print(f"  fetching {rel['url']}")
    lines[rel["distfile"]] = manifest_line_verified(rel["distfile"], rel["url"], rel["sha256"])

    # Everything is fetched and validated: only now touch the tree.
    new_ebuild = pdir / f"{pn}-{rel['pv']}.ebuild"
    new_ebuild.write_text(new_text)
    print(f"  created {new_ebuild.relative_to(REPO_ROOT)}")
    for v in drop:
        (pdir / f"{pn}-{v}.ebuild").unlink()
        print(f"  dropped {pn}-{v}.ebuild")
    manifest.write_text("".join(f"{lines[k]}\n" for k in sorted(lines)))
    print(f"  wrote {manifest.relative_to(REPO_ROOT)} ({len(lines)} DIST entries)")

    mask = REPO_ROOT / "profiles" / "package.mask"
    removed_rcs = [v for v in drop if PRERELEASE_RE.search(v)]
    if rel["prerelease"] or removed_rcs:
        old_mask = mask.read_text() if mask.exists() else ""
        new_mask = update_rc_mask(old_mask, atom,
                                  rel["pv"] if rel["prerelease"] else None, removed_rcs)
        if new_mask != old_mask:
            mask.write_text(new_mask)
            print(f"  updated {mask.relative_to(REPO_ROOT)}")

    emit_outputs(bumped="true", pn=pn, old=plan["current"] or template, new=rel["pv"],
                 dropped=", ".join(drop))
    return 0


# --- cargo bump ------------------------------------------------------------
# Regions of a pycargoebuild-generated ebuild that change between versions. A
# cargo bump = the previous ebuild with exactly these swapped; everything else
# (SRC_URI is ${PV}-templated, plus the maintainer's SLOT/KEYWORDS/IUSE/
# RESTRICT/CARGO_SKIP_TESTS/DOCS/src_install) carries over verbatim.
COPYRIGHT_RE = re.compile(r"^# Copyright \d{4} Gentoo Authors$", re.MULTILINE)
CRATES_BLOCK_RE = re.compile(r'^CRATES="\n.*?\n"\n', re.MULTILINE | re.DOTALL)
# Match only the LICENSE statements themselves — the `LICENSE="..."` line plus
# any following comment / `LICENSE+="..."` lines — NOT everything up to SLOT, so
# a maintainer assignment between LICENSE and SLOT is never swallowed.
LICENSE_REGION_RE = re.compile(
    r'^LICENSE="[^"]*"(?:\n#[^\n]*|\nLICENSE\+="[^"]*")*\n', re.MULTILINE)
PYCARGO_COMMENT_RE = re.compile(r"^# Autogenerated by pycargoebuild \S+.*$", re.MULTILINE)
# pycargoebuild emits `declare -A GIT_CRATES=(...)` when a dep is git-sourced.
GIT_CRATES_RE = re.compile(r"^(?:declare -A )?GIT_CRATES=", re.MULTILINE)
SKIP_TESTS_RE = re.compile(r"CARGO_SKIP_TESTS=\((.*?)\)", re.DOTALL)
# dobin "$(cargo_target_dir)"/{a,b} or .../bin — the binaries src_install ships.
DOBIN_RE = re.compile(r"dobin\b([^\n]*)")


def app_tarball(ebuild_text: str, pn: str, pv: str) -> tuple[str, str]:
    """Return (url, dest) of the application source tarball in a cargo SRC_URI.

    A cargo ebuild's SRC_URI is the app tarball followed by ${CARGO_CRATE_URIS}
    (expanded by the eclass). We only need the explicit `<url> -> <dest>` entry
    whose url fully resolves after ${P}/${PN}/${PV} substitution; the unresolved
    ${CARGO_CRATE_URIS} token is left for the Manifest's per-crate hashing.
    """
    m = SRC_URI_RE.search(ebuild_text)
    if not m:
        raise ValueError("no SRC_URI found")
    body = m.group("body")
    for var, val in {"P": f"{pn}-{pv}", "PN": pn, "PV": pv}.items():
        body = body.replace("${%s}" % var, val)
    # The only token allowed to stay unresolved is the eclass-expanded
    # ${CARGO_CRATE_URIS}; reject USE-conditionals / other unresolved vars so we
    # never pick the wrong artifact (mirrors parse_distfiles' strictness).
    leftover = body.replace("${CARGO_CRATE_URIS}", "")
    if "$" in leftover or "(" in leftover or "?" in leftover:
        raise ValueError(f"unsupported cargo SRC_URI construct: {body!r}")
    tokens = body.split()
    for i in range(len(tokens) - 2):
        if tokens[i + 1] == "->" and "$" not in tokens[i]:
            return tokens[i], tokens[i + 2]
    raise ValueError("no resolvable '<url> -> <dest>' app tarball in SRC_URI")


def installed_bins(ebuild_text: str) -> list[str]:
    """Binary basenames the ebuild's dobin lines install.

    Used to verify, after a build, that every binary src_install ships still
    exists — a renamed upstream binary passes `cargo build`/`test` but would die
    in Portage's src_install. Handles the brace-list form
    `dobin .../{a,b}` and plain space-separated paths; returns [] if it can't
    parse (the caller then skips the check rather than failing the bump).
    """
    bins: list[str] = []
    for args in DOBIN_RE.findall(ebuild_text):
        for tok in args.split():
            tok = tok.strip('"').rsplit("/", 1)[-1]  # drop dir + quotes
            m = re.fullmatch(r"\{([\w.-]+(?:,[\w.-]+)*)\}", tok)
            if m:
                bins.extend(m.group(1).split(","))
            elif re.fullmatch(r"[\w.-]+", tok):
                bins.append(tok)
    return bins


def download_to(url: str, dest: Path, timeout: int = 180) -> None:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            fh.write(chunk)


def splice_cargo_blocks(old_text: str, gen_text: str) -> str:
    """Swap CRATES, the LICENSE region, and the pycargoebuild comment in `old_text`
    with the freshly generated ones from `gen_text`."""
    out = old_text
    for regex, what in ((COPYRIGHT_RE, "copyright line"),
                        (PYCARGO_COMMENT_RE, "pycargoebuild comment"),
                        (CRATES_BLOCK_RE, "CRATES block"),
                        (LICENSE_REGION_RE, "LICENSE region")):
        gen = regex.search(gen_text)
        cur = regex.search(out)
        if not gen:
            raise ValueError(f"pycargoebuild output has no {what}")
        if not cur:
            raise ValueError(f"previous ebuild has no {what}")
        out = out.replace(cur.group(0), gen.group(0), 1)
    return out


def cmd_bump_cargo(atom: str) -> int:
    pkg = next((p for p in PACKAGES if p["atom"] == atom), None)
    if not pkg or pkg["mode"] != "cargo":
        print(f"refusing to bump {atom!r}: not a configured cargo package", file=sys.stderr)
        return 2
    member = pkg["source"].get("cargo_member")
    if not member:
        print(f"{atom}: missing source.cargo_member", file=sys.stderr)
        return 2

    pn = atom.split("/", 1)[1]
    current = highest_current_stable(atom)
    latest = latest_version(pkg["source"])
    if not (current and latest and version_newer(latest, current)):
        print(f"{atom}: up to date ({current}, upstream {latest})")
        emit_outputs(bumped="false")
        return 0

    pdir = package_dir(atom)
    src_ebuild = pdir / f"{pn}-{current}.ebuild"
    new_ebuild = pdir / f"{pn}-{latest}.ebuild"
    old_text = src_ebuild.read_text()
    url, dest = app_tarball(old_text, pn, latest)  # validate SRC_URI before fetching

    # Persistent workdir: the extracted source is handed to the workflow (srcdir
    # output) for the build+test verification, so it must outlive this process.
    work = Path(tempfile.mkdtemp(prefix="rossi-bump-"))
    cratesdir = work / "crates"
    cratesdir.mkdir()
    tarball = work / dest

    print(f"{atom}: {current} -> {latest}")
    print(f"  fetching {url}")
    download_to(url, tarball)
    with tarfile.open(tarball) as tf:
        names = tf.getnames()
        if not names:
            raise ValueError(f"empty/corrupt source tarball: {url}")
        top = names[0].split("/", 1)[0]
        tf.extractall(work, filter="data")
    srcdir = work / top

    gen = work / "gen.ebuild"
    print(f"  running pycargoebuild in {member}")
    # pycargoebuild maps crate SPDX licenses -> Gentoo LICENSE via Gentoo's
    # metadata/license-mapping.conf. Without --license-mapping it imports
    # `portage` to locate it, which the CI runner lacks; point it at our
    # vendored copy (and --no-config for determinism) so Portage is never needed.
    subprocess.run(
        [sys.executable, "-m", "pycargoebuild", "--no-config",
         "--distdir", str(cratesdir),
         "--license-mapping", str(REPO_ROOT / ".github" / "license-mapping.conf"),
         "-o", str(gen), str(srcdir / member)],
        check=True,
    )

    gen_text = gen.read_text()
    # A git-sourced dependency makes pycargoebuild emit a GIT_CRATES block (and
    # fetch <repo>-<commit>.gh.tar.gz distfiles). The splice only swaps
    # CRATES/LICENSE, so it can't carry GIT_CRATES into the ebuild — abort and
    # let the workflow fall back to a tracking issue for a human to handle.
    if GIT_CRATES_RE.search(gen_text):
        raise ValueError("pycargoebuild emitted GIT_CRATES (git dependency) — "
                         "cargo auto-bump can't handle git crates; bump by hand")

    new_text = splice_cargo_blocks(old_text, gen_text)
    # Guard against a structural surprise (pycargoebuild output shape change, a
    # crate-tarball SRC_URI, etc.): the maintainer's customizations must survive.
    for marker in ("inherit cargo", "CARGO_SKIP_TESTS", "src_install"):
        if marker not in new_text:
            raise ValueError(f"spliced ebuild lost its {marker!r} — aborting")

    new_ebuild.write_text(new_text)
    print(f"  created {new_ebuild.relative_to(REPO_ROOT)}")
    if new_ebuild != src_ebuild:
        src_ebuild.unlink()  # one version per package
        print(f"  dropped {src_ebuild.relative_to(REPO_ROOT)}")

    # Rebuild the thin Manifest from scratch: the app tarball plus every distfile
    # pycargoebuild downloaded into --distdir (every regular file — .crate and,
    # in principle, git .gh.tar.gz), sorted by filename like Portage.
    lines = {tarball.name: manifest_line_file(tarball)}
    for f in cratesdir.iterdir():
        if f.is_file():
            lines[f.name] = manifest_line_file(f)
    manifest = pdir / "Manifest"
    manifest.write_text("".join(f"{lines[k]}\n" for k in sorted(lines)))
    print(f"  wrote {manifest.relative_to(REPO_ROOT)} ({len(lines)} DIST entries)")

    skips = SKIP_TESTS_RE.search(new_text)
    emit_outputs(bumped="true", pn=pn, old=current, new=latest,
                 srcdir=str(srcdir),
                 skip_tests=" ".join(skips.group(1).split()) if skips else "",
                 bins=" ".join(installed_bins(new_text)))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "check":
        return cmd_check()
    if len(argv) >= 2 and argv[0] == "bump":
        return cmd_bump(argv[1])
    if len(argv) >= 2 and argv[0] == "bump-cargo":
        return cmd_bump_cargo(argv[1])
    print(__doc__)
    print("usage: version_check.py check | bump <atom> | bump-cargo <atom>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
