# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit edo java-pkg-2

# Upstream tags no releases (perpetual 0.1.0-SNAPSHOT); pin a known-good
# commit from master instead.
COMMIT="6deb3e17a4cdb97ccc2e2946f7aaafb8e5fa2ba6"

DESCRIPTION="Code generator from B to Java, C++, Python, Rust, and TypeScript"
HOMEPAGE="https://github.com/favu100/b2program"
# GitHub commit archives are not guaranteed byte-stable; if the digest
# ever drifts, re-manifest or mirror the tarball as a release asset.
SRC_URI="https://github.com/favu100/b2program/archive/${COMMIT}.tar.gz -> ${P}.tar.gz"
S="${WORKDIR}/${PN}-${COMMIT}"

# Upstream declares no license; all rights remain with the authors.
LICENSE="all-rights-reserved"
SLOT="0"
KEYWORDS="~amd64"
# network-sandbox: upstream publishes no release artifacts, so the
# package is built from source with Gradle, which resolves its
# dependencies from the network at build time (Portage-specific
# RESTRICT value, allowed via restrict-allowed in metadata/layout.conf).
RESTRICT="bindist mirror network-sandbox"

RDEPEND=">=virtual/jre-17:*"
# The Gradle 9 wrapper does not run on newer JDKs.
DEPEND="virtual/jdk:21"

src_compile() {
	local -x GRADLE_USER_HOME="${T}/gradle"
	edo ./gradlew --no-daemon fatJar -x test
}

src_install() {
	java-pkg_newjar build/libs/B2Program-all-*.jar "${PN}.jar"
	java-pkg_dolauncher "${PN}" --jar "${PN}.jar"
}
