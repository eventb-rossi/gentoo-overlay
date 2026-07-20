# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit cmake flag-o-matic

DESCRIPTION="Parallel decision diagram library"
HOMEPAGE="https://github.com/trolando/sylvan"
SRC_URI="https://github.com/trolando/sylvan/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz"

LICENSE="Apache-2.0"
SLOT="0"
KEYWORDS="~amd64"
IUSE="test"

BDEPEND="virtual/pkgconfig"
DEPEND="
	dev-libs/gmp:=
	sys-apps/hwloc:=
"
RDEPEND="${DEPEND}"

RESTRICT="!test? ( test )"

PATCHES=( "${FILESDIR}/${P}-cmake.patch" )

DOCS=( CHANGELOG.md README.md )

src_configure() {
	local mycmakeargs=(
		-DBUILD_TESTING=$(usex test)
		-DSYLVAN_BUILD_EXAMPLES=OFF
	)

	# Sylvan 1.1.1 uses pointer casts for type punning.
	append-flags -fno-strict-aliasing

	cmake_src_configure
}
