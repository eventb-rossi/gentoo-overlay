# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit java-pkg-2

DESCRIPTION="JavaFX-based animator and model checker built on ProB"
HOMEPAGE="https://prob.hhu.de/"
# Multi-platform fat jar; bundles the JavaFX runtime for every platform,
# so a plain JRE is enough. The Add-Exports/Add-Opens it needs are
# declared in the jar manifest and honoured by java -jar automatically.
SRC_URI="https://stups.hhu-hosting.de/downloads/prob2/${PV}/prob2-ui-${PV}-multi.jar -> ${P}.jar"
S="${WORKDIR}"

LICENSE="EPL-2.0"
SLOT="0"
KEYWORDS="~amd64"

RDEPEND=">=virtual/jre-21:*"

src_unpack() {
	# Prebuilt jar, nothing to unpack.
	:
}

src_install() {
	java-pkg_newjar "${DISTDIR}/${P}.jar" "${PN}.jar"
	# --enable-native-access silences the warning from the bundled JavaFX
	# native loader (java.lang.System::load in an unnamed module) and
	# pre-empts the future JDK hard block on restricted native methods
	# (verified against JDK 25); supported by all JDKs >= 17.
	java-pkg_dolauncher "${PN}" \
		--jar "${PN}.jar" \
		--java_args "--enable-native-access=ALL-UNNAMED"
}

pkg_postinst() {
	if [[ -z ${REPLACING_VERSIONS} ]]; then
		elog "ProB2-UI downloads the matching ProB kernel into ~/.prob on first use."
	fi
}
