import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import version_check


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
