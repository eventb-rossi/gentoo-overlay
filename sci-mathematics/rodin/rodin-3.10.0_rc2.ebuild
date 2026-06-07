# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit desktop optfeature wrapper xdg

# Upstream build id embedded in the tarball name; update on version bumps.
MY_BUILD="202605210654-RC2-881664d81"
MY_SF_DIR="$(ver_cut 1-2)-RC$(ver_cut 5)"

DESCRIPTION="IDE for formal modelling and verification with Event-B"
HOMEPAGE="https://www.event-b.org/"
SRC_URI="https://downloads.sourceforge.net/rodin-b-sharp/Core_Rodin_Platform/${MY_SF_DIR}/${PN}-$(ver_cut 1-3).${MY_BUILD}-linux.gtk.x86_64.tar.gz"
S="${WORKDIR}/${PN}"

LICENSE="EPL-1.0 EPL-2.0"
SLOT="0"
KEYWORDS="-* ~amd64"

# Prebuilt Eclipse RCP product without a bundled JRE.
RDEPEND="
	>=virtual/jre-17:*
	x11-libs/gtk+:3
"

RESTRICT="strip"
QA_PREBUILT="opt/${PN}/*"
# The bundled JNA plugin ships libjnidispatch.so for foreign arches and
# libcs; they are loaded from the jar at runtime, if ever.
REQUIRES_EXCLUDE="/opt/${PN}/plugins/com.sun.jna_*"

src_install() {
	insinto /usr/share/pixmaps
	newins icon.xpm ${PN}.xpm
	make_wrapper ${PN} /opt/${PN}/${PN}
	make_desktop_entry ${PN} "Rodin Platform" ${PN} "Development;IDE"

	# Keep the vendor tree intact: the Eclipse native launcher resolves
	# its install location from /proc/self/exe. Move rather than copy
	# to avoid duplicating the large tree in the build directory.
	dodir /opt
	cd "${WORKDIR}" || die
	mv "${S}" "${ED}/opt/${PN}" || die
	mkdir "${S}" || die # later phases expect ${S} to exist
}

pkg_postinst() {
	xdg_pkg_postinst
	optfeature "browser-backed views (help, embedded documentation)" \
		net-libs/webkit-gtk:4.1
}
