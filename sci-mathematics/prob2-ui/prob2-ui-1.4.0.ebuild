# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit java-pkg-2 unpacker

DESCRIPTION="JavaFX-based animator and model checker built on ProB"
HOMEPAGE="https://prob.hhu.de/"
# Upstream now publishes the Linux jar inside its Debian package. Use only
# that jar, not the bundled JRE or Debian launcher.
SRC_URI="https://stups.hhu-hosting.de/downloads/prob2/${PV}/prob2-ui_${PV}_amd64.deb -> ${P}.deb"
S="${WORKDIR}"

LICENSE="EPL-2.0"
SLOT="0"
KEYWORDS="~amd64"
IUSE="ltsmin system-prob"

REQUIRED_USE="ltsmin? ( system-prob )"

# The Debian archive contains data.tar.zst.
BDEPEND="app-arch/zstd"
RDEPEND="
	>=virtual/jre-21:*
	media-libs/alsa-lib
	virtual/opengl
	x11-libs/gtk+:3
	x11-libs/libX11
	x11-libs/libXtst
	x11-libs/libXxf86vm
	system-prob? (
		>=sci-mathematics/prob-bin-1.16.1-r1[ltsmin?]
		<sci-mathematics/prob-bin-1.16.2
	)
"

src_install() {
	java-pkg_newjar "${S}/opt/prob2-ui/lib/app/${PN}-${PV}-linux.jar" "${PN}.jar"
	# --enable-native-access silences the warning from the bundled JavaFX
	# native loader (java.lang.System::load in an unnamed module) and
	# pre-empts the future JDK hard block on restricted native methods
	# (verified against JDK 25); supported by all JDKs >= 17.
	local java_args="--enable-native-access=ALL-UNNAMED"
	use system-prob && java_args+=" -Dprob.home=/opt/prob"
	java-pkg_dolauncher "${PN}" \
		--jar "${PN}.jar" \
		--java_args "${java_args}"
}

pkg_postinst() {
	if [[ -z ${REPLACING_VERSIONS} ]] && ! use system-prob; then
		elog "ProB2-UI extracts its bundled ProB kernel to a private temporary directory at runtime."
	fi
}
