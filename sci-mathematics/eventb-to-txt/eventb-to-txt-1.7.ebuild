# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DISTUTILS_USE_PEP517=setuptools
PYTHON_COMPAT=( python3_{12..15} )

inherit distutils-r1

DESCRIPTION="Convert Rodin Event-B models to plain-text format"
HOMEPAGE="https://github.com/eventb-rossi/eventb-to-txt"
# The GitHub tarball ships the complete test suite, unlike the PyPI sdist.
SRC_URI="https://github.com/eventb-rossi/${PN}/archive/refs/tags/v${PV}.tar.gz -> ${P}.gh.tar.gz"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"

EPYTEST_PLUGINS=()
distutils_enable_tests pytest
