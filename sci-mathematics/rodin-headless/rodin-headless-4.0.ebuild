# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DESCRIPTION="Headless toolchain to build, model-check, and prove Rodin Event-B models"
HOMEPAGE="https://github.com/eventb-rossi/rodin-headless"
SRC_URI="https://github.com/eventb-rossi/rodin-headless/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"

# The Rodin runtime requirements track sci-mathematics/rodin (>=virtual/jre-17:*
# and x11-libs/gtk+:3): here the JRE is raised to a JDK because the headless
# builder plugin is compiled on the fly with javac, and Xvfb is added because
# SWT still needs a display when Rodin runs headless. zip/unzip repackage the
# model archives while curl and tar fetch and unpack the Rodin and ProB
# runtimes, which rodin-headless-install downloads into a per-user data dir on
# first use rather than this package shipping them.
RDEPEND="
	>=virtual/jdk-17:*
	app-arch/tar
	app-arch/unzip
	app-arch/zip
	net-misc/curl
	x11-base/xorg-server[xvfb]
	x11-libs/gtk+:3
"

DOCS=( README.md )

src_compile() { :; }  # pure shell; `make all` only prints a notice

src_install() {
	# The Makefile is FHS-clean (GNU dir vars + DESTDIR) and installs the
	# wrappers, libexec engine, docker build context, and man pages itself.
	emake \
		DESTDIR="${D}" \
		prefix="${EPREFIX}/usr" \
		install

	# README.md is the only doc the Makefile does not install.
	einstalldocs
}

pkg_postinst() {
	if [[ -z ${REPLACING_VERSIONS} ]]; then
		elog "rodin-headless does not bundle Rodin or ProB. Fetch them once with:"
		elog "    rodin-headless-install"
		elog "This downloads the Rodin Platform and ProB into the per-user data dir."
		elog "Run 'rodin-headless-install --check-deps' to verify JDK/GTK3/Xvfb."
	fi
}
