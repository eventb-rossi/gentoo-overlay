# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit desktop optfeature xdg

# Upstream build id embedded in the tarball name; update on version bumps.
MY_BUILD="202607010932-881664d81"
MY_SF_DIR="$(ver_cut 1-2)"

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

src_install() {
	insinto /usr/share/pixmaps
	newins icon.xpm ${PN}.xpm
	# Force a light GTK theme for the Rodin process. The e4 CSS theme pinned
	# in rodin.ini below only styles Eclipse-drawn widgets; native SWT/GTK
	# widgets (trees, editor area, menus) follow the desktop GTK theme,
	# leaving the UI partly dark on a dark desktop. GTK_THEME overrides that
	# for this process only (mirrors the upstream launcher).
	cat > "${T}"/${PN} <<-EOF || die
		#!/bin/sh
		export GTK_THEME=Adwaita:light
		exec ${EPREFIX}/opt/${PN}/${PN} "\$@"
	EOF
	dobin "${T}"/${PN}
	make_desktop_entry ${PN} "Rodin Platform" ${PN} "Development;IDE"

	# Keep the vendor tree intact: the Eclipse native launcher resolves
	# its install location from /proc/self/exe. Move rather than copy
	# to avoid duplicating the large tree in the build directory.
	dodir /opt
	cd "${WORKDIR}" || die

	# JNA bundles libjnidispatch for ~two dozen foreign OS/ABI combos as loose
	# files in the exploded plugin dir. A 64-bit JVM only ever loads the host
	# (linux-x86-64) copy; the rest are dead weight. The 32-bit linux-x86 build
	# in particular records NEEDED=libc.so.6, which makes portage preserve
	# glibc's libc.so.6 indefinitely (a prebuilt blob can never be relinked).
	# Drop every native copy but the host ABI.
	find "${S}"/plugins/com.sun.jna_*/com/sun/jna -mindepth 2 -name 'libjnidispatch.*' \
		-not -path '*/linux-x86-64/*' -delete || die

	# Eclipse 4.x auto-detects the desktop's dark appearance and applies its
	# dark CSS theme regardless of the product's light-mode intent, yielding a
	# broken mixed-mode UI. Pin the light default (mirrors the upstream cask).
	[[ -f "${S}/rodin.ini" ]] || die "rodin.ini not found at expected path"
	printf '%s\n' \
		"-Dorg.eclipse.e4.ui.css.swt.theme=org.eclipse.e4.ui.css.theme.e4_default" \
		>> "${S}/rodin.ini" || die

	mv "${S}" "${ED}/opt/${PN}" || die
	mkdir "${S}" || die # later phases expect ${S} to exist
}

pkg_postinst() {
	xdg_pkg_postinst
	optfeature "browser-backed views (help, embedded documentation)" \
		net-libs/webkit-gtk:4.1
}
