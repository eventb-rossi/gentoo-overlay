# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

RPM_COMPRESS_TYPE="zstd"

inherit desktop rpm xdg

MY_P="atelierb-free-${PV}"
QT_PV="5.15.10"
FEDORA="https://archives.fedoraproject.org/pub/archive/fedora/linux/releases/39/Everything/x86_64/os/Packages"

DESCRIPTION="IDE for formal modelling and verification with the B method"
HOMEPAGE="https://www.atelierb.eu/"
# The vendor binaries were built against Qt 5 and ICU 73, neither of
# which Gentoo still ships; the matching runtime libraries are bundled
# from the same Fedora 39 release the vendor package targets.
SRC_URI="
	https://www.atelierb.eu/wp-content/uploads/2024/10/${MY_P}-fedora-39.rpm
	${FEDORA}/q/qt5-qtbase-${QT_PV}-9.fc39.x86_64.rpm
	${FEDORA}/q/qt5-qtbase-gui-${QT_PV}-9.fc39.x86_64.rpm
	${FEDORA}/q/qt5-qtsvg-${QT_PV}-2.fc39.x86_64.rpm
	${FEDORA}/q/qt5-qtxmlpatterns-${QT_PV}-2.fc39.x86_64.rpm
	${FEDORA}/l/libicu-73.2-2.fc39.x86_64.rpm
	${FEDORA}/s/systemd-libs-254.5-2.fc39.x86_64.rpm
"
S="${WORKDIR}"

LICENSE="Atelier-B-Community"
SLOT="0"
KEYWORDS="-* ~amd64"
RESTRICT="bindist mirror strip"

# openssl and double-conversion are pinned to the subslots providing
# the sonames the bundled Qt 5 links (libssl.so.3, libdouble-conversion.so.3)
# so that an incompatible upgrade conflicts visibly instead of breaking
# the prebuilt binaries silently. The bundled Qt5Network also links
# libproxy.so.1, which current libproxy only provides as a compatibility
# symlink; re-check on bumps.
RDEPEND="
	app-arch/lz4
	app-arch/xz-utils
	app-arch/zstd
	dev-libs/double-conversion:0/3
	dev-libs/glib:2
	dev-libs/gmp:=
	dev-libs/libpcre2[pcre16]
	dev-libs/openssl:0/3
	media-libs/fontconfig
	media-libs/freetype
	media-libs/harfbuzz
	media-libs/libglvnd
	media-libs/libpng
	net-libs/libproxy
	sys-apps/dbus
	sys-libs/libcap
	virtual/krb5
	virtual/zlib
	x11-libs/libICE
	x11-libs/libSM
	x11-libs/libX11
	x11-libs/libdrm
	x11-libs/libxcb
	x11-libs/libxkbcommon[X]
	x11-libs/xcb-util-image
	x11-libs/xcb-util-keysyms
	x11-libs/xcb-util-renderutil
	x11-libs/xcb-util-wm
"

QA_PREBUILT="*"

src_install() {
	local abdir="/opt/${MY_P}"
	local qtdir="${abdir}/qt5"

	newicon -s 128 "${S}/opt/${MY_P}/share/icons/AtelierB128.png" ${PN}.png
	make_desktop_entry ${PN} "Atelier B" ${PN} "Development;IDE"

	# Move rather than copy to avoid duplicating the large vendor tree
	# in the build directory.
	dodir /opt
	mv "${S}/opt/${MY_P}" "${ED}/opt/" || die

	# Qt 5 modules used by the vendor binaries, the xcb platform
	# integration, and the exact ICU the vendor binaries link against.
	dodir "${qtdir}/lib64"
	local lib
	for lib in \
		"${S}"/usr/lib64/libQt5{Concurrent,Core,DBus,Gui,Network,PrintSupport,Svg,Widgets,XcbQpa,Xml,XmlPatterns}.so.5* \
		"${S}"/usr/lib64/libicu{data,i18n,uc}.so.73* \
		"${S}"/usr/lib64/libsystemd.so.0*
	do
		cp -pP "${lib}" "${ED}${qtdir}/lib64/" || die
	done

	dodir "${qtdir}/plugins"
	local plugin
	for plugin in platforms imageformats iconengines styles \
		platforminputcontexts xcbglintegrations generic printsupport bearer
	do
		cp -pPR "${S}/usr/lib64/qt5/plugins/${plugin}" \
			"${ED}${qtdir}/plugins/" || die
	done
	# Needs libQt5EglFSDeviceIntegration, which is not bundled; the
	# vendor binaries only ever run on the xcb platform.
	rm "${ED}${qtdir}/plugins/platforms/libqeglfs.so" || die

	cat > "${T}/${PN}" <<-EOF || die
		#!/bin/sh
		export LD_LIBRARY_PATH="${EPREFIX}${abdir}/lib64:${EPREFIX}${qtdir}/lib64\${LD_LIBRARY_PATH:+:\${LD_LIBRARY_PATH}}"
		export QT_PLUGIN_PATH="${EPREFIX}${qtdir}/plugins"
		exec "${EPREFIX}${abdir}/bin/AtelierB" "\$@"
	EOF
	dobin "${T}/${PN}"
}
