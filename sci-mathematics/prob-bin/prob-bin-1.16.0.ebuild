# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit desktop optfeature wrapper xdg

DESCRIPTION="Animator, constraint solver and model checker for B, Event-B, CSP, TLA+, Z"
HOMEPAGE="https://prob.hhu.de/"
# The release tarball is an unversioned filename under a versioned path.
SRC_URI="https://stups.hhu-hosting.de/downloads/prob/tcltk/releases/${PV}/ProB.linux64.tar.gz -> ${P}.tar.gz"
S="${WORKDIR}/ProB"

LICENSE="EPL-1.0"
SLOT="0"
KEYWORDS="-* ~amd64"
IUSE="ltsmin"

# Prebuilt SICStus-Prolog binaries. The parser jars (lib/*.jar) need a
# JRE and the Tcl/Tk UI needs a system Tcl/Tk. Most native helpers ship
# in lib/ (z3, zmq, bliss, ...) and are resolved relative to the
# executable, but two link system libraries that QA_PREBUILT hides from
# scanning: the bundled cspmf (CSP-M support) needs libgmp and the
# bundled libczmq needs libuuid.
RDEPEND="
	dev-lang/tk
	dev-libs/gmp
	sys-apps/util-linux
	>=virtual/jre-1.8:*
	ltsmin? ( sci-mathematics/ltsmin[prob] )
"

RESTRICT="strip"
QA_PREBUILT="opt/prob/*"

src_install() {
	newicon tcl/icons/prob.xpm prob.xpm
	# probcli.sh chmods its own binary under "set -e", which fails for a
	# non-root user against the read-only install, so wrap the binary
	# directly. StartProB.sh only exports TRAILSTKSIZE and execs prob, so
	# wrap it to preserve that. Both binaries resolve their home (lib/,
	# tcl/, stdlib/) from the executable path, so cwd is irrelevant.
	make_wrapper prob /opt/prob/StartProB.sh
	make_wrapper probcli /opt/prob/probcli
	make_desktop_entry prob "ProB" prob "Development;Science"

	# Move rather than copy to avoid duplicating the large vendor tree
	# in the build directory. Step out of ${S} first so we are not
	# renaming the current working directory.
	dodir /opt
	cd "${WORKDIR}" || die
	mv "${S}" "${ED}/opt/prob" || die
	mkdir "${S}" || die # later phases expect ${S} to exist

	if use ltsmin; then
		# ProB discovers LTSmin relative to its own kernel home.  Keep these
		# links owned by ProB so LTSmin never writes into another package's
		# private tree.
		dosym -r /usr/bin/prob2lts-seq /opt/prob/lib/prob2lts-seq
		dosym -r /usr/bin/prob2lts-sym /opt/prob/lib/prob2lts-sym
		dosym -r /usr/bin/ltsmin-printtrace /opt/prob/lib/ltsmin-printtrace
	fi
}

pkg_postinst() {
	xdg_pkg_postinst
	optfeature "graph and state-space visualization" media-gfx/graphviz
}
