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
IUSE="ltsmin system-prob"

RDEPEND="
	>=virtual/jre-21:*
	ltsmin? ( sci-mathematics/ltsmin[prob] )
	system-prob? ( ~sci-mathematics/prob-bin-1.15.1[ltsmin?] )
"

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
	local java_args="-Dsun.misc.unsafe.memory.access=allow"
	use system-prob && java_args+=" -Dprob.home=/opt/prob"
	java-pkg_dolauncher "${PN}" \
		--jar "${PN}.jar" \
		--java_args "${java_args}"
}

pkg_postinst() {
	if [[ -z ${REPLACING_VERSIONS} ]] && ! use system-prob; then
		elog "eventb-animate extracts its bundled ProB kernel to a private temporary directory at runtime."
	fi
}
