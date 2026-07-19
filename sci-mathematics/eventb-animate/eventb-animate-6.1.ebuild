# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit java-pkg-2

DESCRIPTION="Animate Event-B models with the ProB model checker"
HOMEPAGE="https://github.com/eventb-rossi/eventb-animate"
SRC_URI="https://github.com/eventb-rossi/${PN}/releases/download/v${PV}/${PN}.jar -> ${P}.jar"
S="${WORKDIR}"

LICENSE="Apache-2.0"
SLOT="0"
KEYWORDS="~amd64"

RDEPEND=">=virtual/jre-21:*"

src_unpack() {
	# Prebuilt jar, nothing to unpack.
	:
}

src_install() {
	java-pkg_newjar "${DISTDIR}/${P}.jar" "${PN}.jar"
	# Silence sun.misc.Unsafe deprecation warnings triggered by the
	# bundled Guice on JDK 23+. The system property is the
	# implementation detail behind --sun-misc-unsafe-memory-access
	# (verified against JDK 25) and is ignored gracefully by older
	# JVMs where that option does not exist; re-check on JDK bumps.
	java-pkg_dolauncher "${PN}" \
		--jar "${PN}.jar" \
		--java_args "-Dsun.misc.unsafe.memory.access=allow"
}

pkg_postinst() {
	if [[ -z ${REPLACING_VERSIONS} ]]; then
		elog "eventb-animate downloads the ProB kernel into ~/.prob on first use."
	fi
}
