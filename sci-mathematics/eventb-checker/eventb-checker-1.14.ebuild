# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit java-pkg-2

DESCRIPTION="Standalone validator for Event-B models"
HOMEPAGE="https://github.com/eventb-rossi/eventb-checker"
SRC_URI="https://github.com/eventb-rossi/${PN}/releases/download/v${PV}/${P}-all.jar -> ${P}.jar"
S="${WORKDIR}"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"

RDEPEND=">=virtual/jre-21:*"

src_unpack() {
	# Prebuilt jar, nothing to unpack.
	:
}

src_install() {
	java-pkg_newjar "${DISTDIR}/${P}.jar" "${PN}.jar"
	# --enable-native-access silences JNA warnings from the bundled
	# Rodin AST libraries and pre-empts the future JDK hard removal.
	java-pkg_dolauncher "${PN}" \
		--jar "${PN}.jar" \
		--java_args "--enable-native-access=ALL-UNNAMED"
}
