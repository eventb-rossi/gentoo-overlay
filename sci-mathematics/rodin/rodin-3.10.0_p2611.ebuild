# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit desktop optfeature xdg

# Build qualifier (commit date + hash) embedded in the release asset name. It
# cannot be derived from ${PV}; .github/scripts/version_check.py rewrites it
# on version bumps.
MY_BUILD="202611160826-a263a14"
# PV -> Rodin-Bundle release tag: 3.10.0_p2611 -> 3.10.0-2611,
# 3.10.0_p2609_p2 -> 3.10.0-2609.2, 3.10.0_p2611_rc1 -> 3.10.0-2611-rc1.
MY_PV="${PV/_p/-}"
MY_PV="${MY_PV/_p/.}"
MY_PV="${MY_PV/_rc/-rc}"

DESCRIPTION="IDE for formal modelling and verification with Event-B"
HOMEPAGE="https://github.com/eventb-rossi/Rodin-Bundle"
SRC_URI="https://github.com/eventb-rossi/Rodin-Bundle/releases/download/v${MY_PV}/${PN}-$(ver_cut 1-3).${MY_BUILD}-rossi-linux.gtk.x86_64.tar.gz"
S="${WORKDIR}/${PN}"

# EPL: Rodin, Eclipse, ProB, PO Cleaner, Rossi bridge; BSD: CVC3, CVC4,
# veriT; MIT: Z3 (the bundled SMT solvers).
LICENSE="BSD EPL-1.0 EPL-2.0 MIT"
SLOT="0"
KEYWORDS="-* ~amd64"

# Prebuilt Eclipse RCP product without a bundled JRE. The bundled ProB
# binaries link against libgmp (cspmf) and libuuid (czmq).
RDEPEND="
	dev-libs/gmp
	sys-apps/util-linux
	>=virtual/jre-21:*
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

	# p2 drops the executable bits of ProB's native tools (probcli, cspmf,
	# ...). The ProB plug-in restores them at startup, but only in a
	# user-writable install: under a root-owned /opt it cannot, and the
	# `chmod a+x` in probcli.sh (run under `set -e`) then aborts ProB for
	# every user. Set the bits here and make that chmod best-effort.
	local prob f
	prob=( "${S}"/plugins/de.prob.core_*/prob/linux64 )
	[[ -f ${prob[0]}/probcli.sh ]] || die "ProB linux64 tree not found"
	for f in "${prob[0]}"/* "${prob[0]}"/lib/*; do
		[[ -f ${f} && ${f##*/} != *.so* ]] || continue
		# ELF executables and #! scripts; shared objects need no +x.
		case $(head -c 4 "${f}" | od -An -tx1 | tr -d ' \n') in
			7f454c46|2321*) chmod 0755 "${f}" || die ;;
		esac
	done
	sed -i -e '/^[[:space:]]*chmod a+x /s|$| 2>/dev/null \|\| :|' \
		"${prob[0]}"/probcli.sh || die
	grep -q 'chmod a+x .* || :$' "${prob[0]}"/probcli.sh \
		|| die "probcli.sh chmod not patched"

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
