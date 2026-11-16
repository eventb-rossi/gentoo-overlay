import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import version_check

RODIN = "sci-mathematics/rodin"
BUNDLE = {"type": "rodin_bundle", "repo": "eventb-rossi/Rodin-Bundle", "prerelease": True}
EBUILD = ('EAPI=8\n\nMY_BUILD="{build}"\nMY_PV="${{PV/_p/-}}"\n'
          'SRC_URI="https://example.test/v${{MY_PV}}/${{PN}}-$(ver_cut 1-3).${{MY_BUILD}}'
          '-rossi-linux.gtk.x86_64.tar.gz"\n')
OTHER_MASK = "# Someone (2026-01-01)\n# Unrelated.\n~sci-mathematics/prob-bin-9.9.9\n"


class RodinBundleTest(unittest.TestCase):
    """Rodin-Bundle tags -> PVs, release filtering, and the dedicated bump."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.overlay = self.root / "overlay"
        self.pdir = self.overlay / RODIN
        self.pdir.mkdir(parents=True)
        (self.overlay / "profiles").mkdir()
        self.mask = self.overlay / "profiles/package.mask"
        self.mask.write_text(OTHER_MASK)
        self.outputs = self.root / "outputs"
        self.releases = []
        patches = [patch.object(version_check, "REPO_ROOT", self.overlay),
                   patch.object(version_check, "http_get", self.fake_http_get),
                   patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.outputs)})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def fake_http_get(self, url, **_):
        self.assertIn("/repos/eventb-rossi/Rodin-Bundle/releases", url)
        return json.dumps(self.releases).encode()

    def release(self, tag, build, *, prerelease=False, sums=True, draft=False,
                linux=True, digest=None):
        """Add a fake release whose Linux asset is a real local file."""
        base = tag[1:].split("-", 1)[0]
        name = f"rodin-{base}.{build}-rossi-linux.gtk.x86_64.tar.gz"
        payload = self.root / name
        payload.write_bytes(f"payload of {tag}".encode())
        assets = [{"name": f"rodin-{base}.{build}-rossi-win32.win32.x86_64.zip",
                   "browser_download_url": "https://example.test/win.zip"}]
        if linux:
            assets.append({"name": name, "browser_download_url": payload.as_uri(),
                           "digest": digest or "sha256:" + hashlib.sha256(
                               payload.read_bytes()).hexdigest()})
        if sums:
            assets.append({"name": "SHA256SUMS",
                           "browser_download_url": "https://example.test/SHA256SUMS"})
        # The API lists newest first.
        self.releases.insert(0, {"tag_name": tag, "draft": draft,
                                 "prerelease": prerelease, "assets": assets})
        return payload

    def package(self, pv, build):
        """Add an ebuild plus its Manifest DIST line."""
        (self.pdir / f"rodin-{pv}.ebuild").write_text(EBUILD.format(build=build))
        name = f"rodin-{pv.split('_', 1)[0]}.{build}-rossi-linux.gtk.x86_64.tar.gz"
        manifest = self.pdir / "Manifest"
        old = manifest.read_text() if manifest.exists() else ""
        manifest.write_text(old + f"DIST {name} 1 BLAKE2B 00 SHA512 00\n")

    def ebuilds(self):
        return sorted(p.name for p in self.pdir.glob("*.ebuild"))

    def output(self):
        return dict(line.split("=", 1) for line in self.outputs.read_text().splitlines())

    def test_tag_to_pv_and_ordering(self):
        def pv(tag):
            return version_check.bundle_pv(version_check.BUNDLE_TAG_RE.match(tag).groupdict())

        self.assertEqual(pv("v3.10.0-2611"), "3.10.0_p2611")
        self.assertEqual(pv("v3.10.0-2609.2"), "3.10.0_p2609_p2")
        self.assertEqual(pv("v3.10.0-2611-rc1"), "3.10.0_p2611_rc1")
        ordered = ["3.9.0", "3.10.0", "3.10.0_p2609", "3.10.0_p2609_p2",
                   "3.10.0_p2611_rc1", "3.10.0_p2611_rc2", "3.10.0_p2611",
                   "3.10.0_p2611-r1", "3.10.0_p2611_p1", "3.10.0_p2612", "3.11.0_p2701"]
        keys = [version_check.bundle_pv_key(v) for v in ordered]
        # -r1 is a Gentoo revision of the same upstream release: equal, not newer.
        self.assertEqual(keys[6], keys[7])
        del keys[7]
        self.assertEqual(keys, sorted(set(keys)))

    def test_incomplete_or_inconsistent_releases_are_skipped(self):
        self.release("v3.10.0-2609", "aaaaaaa")
        self.release("v3.10.0-2610", "bbbbbbb", draft=True)
        self.release("v3.10.0-2611", "ccccccc", sums=False)       # still uploading
        self.release("v3.10.0-2612", "ddddddd", linux=False)
        self.release("v3.10.0-2613-rc1", "eeeeeee")                # rc not a prerelease
        self.release("v3.10.0-2614", "fffffff", prerelease=True)   # prerelease, no -rc
        self.release("nightly", "0000000")
        rels = version_check.bundle_releases("eventb-rossi/Rodin-Bundle")
        self.assertEqual([r["pv"] for r in rels], ["3.10.0_p2609"])
        self.assertEqual(rels[0]["build"], "aaaaaaa")

    def test_up_to_date(self):
        self.package("3.10.0_p2611", "202611160826-a263a14")
        self.release("v3.10.0-2611-rc2", "202611160826-a263a14", prerelease=True)
        self.release("v3.10.0-2611", "202611160826-a263a14")
        plan = version_check.rodin_bundle_plan(RODIN, BUNDLE)
        self.assertEqual((plan["current"], plan["latest"], plan["target"]),
                         ("3.10.0_p2611", "3.10.0_p2611", None))
        self.assertEqual(version_check.cmd_bump(RODIN), 0)
        self.assertEqual(self.output(), {"bumped": "false"})
        self.assertEqual(self.ebuilds(), ["rodin-3.10.0_p2611.ebuild"])

    def test_prerelease_adds_masked_rc_then_supersedes_it(self):
        self.package("3.10.0_p2611", "202611160826-a263a14")
        self.release("v3.10.0-2611", "202611160826-a263a14")
        rc1 = self.release("v3.10.0-2612-rc1", "202612010000-1111111", prerelease=True)

        self.assertEqual(version_check.cmd_bump(RODIN), 0)
        self.assertEqual(self.output()["new"], "3.10.0_p2612_rc1")
        self.assertEqual(self.ebuilds(), ["rodin-3.10.0_p2611.ebuild",
                                          "rodin-3.10.0_p2612_rc1.ebuild"])
        self.assertIn('MY_BUILD="202612010000-1111111"',
                      (self.pdir / "rodin-3.10.0_p2612_rc1.ebuild").read_text())
        manifest = (self.pdir / "Manifest").read_text().splitlines()
        self.assertEqual(len(manifest), 2)
        self.assertIn(version_check.manifest_line_file(rc1), manifest)
        mask = self.mask.read_text()
        self.assertTrue(mask.startswith(OTHER_MASK + "\n# Denis Efremov"))
        self.assertTrue(mask.endswith("\n~sci-mathematics/rodin-3.10.0_p2612_rc1\n"))

        self.outputs.unlink()
        self.release("v3.10.0-2612-rc2", "202612020000-2222222", prerelease=True)
        self.assertEqual(version_check.cmd_bump(RODIN), 0)
        self.assertEqual(self.output()["dropped"], "3.10.0_p2612_rc1")
        self.assertEqual(self.ebuilds(), ["rodin-3.10.0_p2611.ebuild",
                                          "rodin-3.10.0_p2612_rc2.ebuild"])
        mask = self.mask.read_text()
        self.assertNotIn("_rc1", mask)
        self.assertEqual(mask.count("# Denis Efremov"), 1)
        self.assertTrue(mask.endswith("\n~sci-mathematics/rodin-3.10.0_p2612_rc2\n"))

        # Nothing newer: a second run is a no-op.
        self.outputs.unlink()
        self.assertEqual(version_check.cmd_bump(RODIN), 0)
        self.assertEqual(self.output(), {"bumped": "false"})

    def test_stable_release_drops_older_ebuilds_and_their_mask(self):
        self.package("3.10.0_p2611", "202611160826-a263a14")
        self.package("3.10.0_p2612_rc1", "202612010000-1111111")
        self.mask.write_text(OTHER_MASK + "\n" + version_check.RC_MASK_HEADER.format(
            date="2026-12-01") + "\n~sci-mathematics/rodin-3.10.0_p2612_rc1\n")
        self.release("v3.10.0-2611", "202611160826-a263a14")
        self.release("v3.10.0-2612-rc1", "202612010000-1111111", prerelease=True)
        final = self.release("v3.10.0-2612", "202612050000-3333333")

        self.assertEqual(version_check.cmd_bump(RODIN), 0)
        self.assertEqual(self.output(), {
            "bumped": "true", "pn": "rodin", "old": "3.10.0_p2611",
            "new": "3.10.0_p2612", "dropped": "3.10.0_p2611, 3.10.0_p2612_rc1"})
        self.assertEqual(self.ebuilds(), ["rodin-3.10.0_p2612.ebuild"])
        self.assertEqual((self.pdir / "rodin-3.10.0_p2612.ebuild").read_text(),
                         EBUILD.format(build="202612050000-3333333"))
        self.assertEqual((self.pdir / "Manifest").read_text(),
                         version_check.manifest_line_file(final) + "\n")
        self.assertEqual(self.mask.read_text(), OTHER_MASK)

    def test_checksum_mismatch_leaves_the_tree_alone(self):
        self.package("3.10.0_p2611", "202611160826-a263a14")
        self.release("v3.10.0-2611", "202611160826-a263a14")
        self.release("v3.10.0-2612", "202612050000-3333333", digest="sha256:" + "0" * 64)
        before = {p.name: p.read_text() for p in self.pdir.iterdir()}
        with self.assertRaisesRegex(ValueError, "sha256"):
            version_check.cmd_bump(RODIN)
        self.assertEqual({p.name: p.read_text() for p in self.pdir.iterdir()}, before)
        self.assertEqual(self.mask.read_text(), OTHER_MASK)


class Prob2DebTest(unittest.TestCase):
    def test_bump_requires_launchable_jar(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            package = root / "package"
            control = package / "DEBIAN"
            control.mkdir(parents=True)
            (control / "control").write_text(
                "Package: prob2-ui\nVersion: 1.4.0\nArchitecture: amd64\n"
                "Maintainer: Test <test@example.com>\nDescription: test\n"
            )
            jar = package / "opt/prob2-ui/lib/app/prob2-ui-1.4.0-linux.jar"
            jar.parent.mkdir(parents=True)
            archive = root / "prob2-ui-1.4.0.deb"

            subprocess.run(["dpkg-deb", "--build", "--root-owner-group",
                            str(package), str(archive)],
                           check=True, stdout=subprocess.DEVNULL)
            with self.assertRaisesRegex(ValueError, "missing.*linux.jar"):
                version_check.manifest_line_prob2_deb(archive.name, archive.as_uri(), "1.4.0")

            overlay = root / "overlay"
            pdir = overlay / "sci-mathematics/prob2-ui"
            pdir.mkdir(parents=True)
            (pdir / "prob2-ui-1.3.1.ebuild").write_text(
                f'SRC_URI="{archive.as_uri()} -> ${{P}}.deb"\n'
            )
            (pdir / "Manifest").write_text("previous manifest\n")
            with patch.object(version_check, "REPO_ROOT", overlay), \
                    patch.object(version_check, "latest_version", return_value="1.4.0"):
                with self.assertRaisesRegex(ValueError, "missing.*linux.jar"):
                    version_check.cmd_bump("sci-mathematics/prob2-ui")
            self.assertFalse((pdir / "prob2-ui-1.4.0.ebuild").exists())
            self.assertEqual((pdir / "Manifest").read_text(), "previous manifest\n")

            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
            subprocess.run(["dpkg-deb", "--build", "--root-owner-group",
                            str(package), str(archive)],
                           check=True, stdout=subprocess.DEVNULL)
            with self.assertRaisesRegex(ValueError, "no ProB2-UI Main-Class"):
                version_check.manifest_line_prob2_deb(archive.name, archive.as_uri(), "1.4.0")

            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr("META-INF/MANIFEST.MF",
                            "Manifest-Version: 1.0\nMain-Class: de.prob2.ui.Main\n")
            subprocess.run(["dpkg-deb", "--build", "--root-owner-group",
                            str(package), str(archive)],
                           check=True, stdout=subprocess.DEVNULL)
            self.assertEqual(version_check.manifest_line_prob2_deb(
                archive.name, archive.as_uri(), "1.4.0"),
                version_check.manifest_line_file(archive))
            with patch.object(version_check, "REPO_ROOT", overlay), \
                    patch.object(version_check, "latest_version", return_value="1.4.0"):
                self.assertEqual(version_check.cmd_bump("sci-mathematics/prob2-ui"), 0)
            self.assertEqual((pdir / "prob2-ui-1.4.0.ebuild").read_text(),
                             (pdir / "prob2-ui-1.3.1.ebuild").read_text())
            self.assertIn(version_check.manifest_line_file(archive),
                          (pdir / "Manifest").read_text())


if __name__ == "__main__":
    unittest.main()
