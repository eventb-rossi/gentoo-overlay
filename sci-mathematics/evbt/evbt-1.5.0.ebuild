# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit java-pkg-2

DESCRIPTION="Event-B tool for code generation and documentation"
HOMEPAGE="https://codeberg.org/viklauverk/EventBTool"
# Upstream development moved to Codeberg, releases are published on GitHub.
# The release artifact is a self-executable jar with a shell stub prepended;
# the JVM reads the zip archive from the end, ignoring the stub.
SRC_URI="https://github.com/viklauverk/EventBTool/releases/download/v${PV}/evbt -> ${P}.jar"
S="${WORKDIR}"

LICENSE="AGPL-3+"
SLOT="0"
KEYWORDS="~amd64"

# Java 22 or newer; satisfied by virtual/jre-25, the lowest virtual
# currently above that floor.
RDEPEND=">=virtual/jre-22:*"

src_unpack() {
	# Prebuilt jar, nothing to unpack.
	:
}

src_install() {
	java-pkg_newjar "${DISTDIR}/${P}.jar" "${PN}.jar"
	java-pkg_dolauncher "${PN}" --jar "${PN}.jar"
}
