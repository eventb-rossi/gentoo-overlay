#!/usr/bin/env python3
"""Upstream version monitor for the eventb-rossi overlay.

Two responsibilities, mirroring the two halves of homebrew-tap's release tracker:

  * "bump" packages have a single distfile and a clean ${PV}-templated SRC_URI,
    so a version bump is mechanical: copy the ebuild to the new version and
    regenerate the thin Manifest by downloading the distfile(s) and hashing them.
    These are turned into pull requests.

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
    {"atom": "sci-mathematics/rodin-headless", "mode": "bump",
     "source": {"type": "github", "repo": "eventb-rossi/rodin-headless"}},
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
    {"atom": "sci-mathematics/ltsmin", "mode": "track",
     "source": {"type": "github", "repo": "utwente-fmt/ltsmin"}},
    # LTSmin 3.0.2 requires Sylvan >=1.1,<1.2. Newer Sylvan releases need an
    # LTSmin compatibility port before they can be packaged here.
    {"atom": "sci-mathematics/sylvan", "mode": "track",
     "source": {"type": "github", "repo": "trolando/sylvan"}},
    # Cargo workspace: the bump regenerates CRATES/LICENSE/Manifest with
    # pycargoebuild (cmd_bump_cargo). cargo_member is the workspace member to run
    # pycargoebuild in — it refuses at a virtual workspace root, and any member
    # yields the full crate set via the shared Cargo.lock.
    {"atom": "sci-mathematics/rossi", "mode": "cargo",
     "source": {"type": "github", "repo": "eventb-rossi/rossi",
                "cargo_member": "crates/rossi"}},
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
