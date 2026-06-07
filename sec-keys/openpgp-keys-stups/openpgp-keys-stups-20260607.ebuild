# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DESCRIPTION="OpenPGP key used by the STUPS group at HHU Düsseldorf"
HOMEPAGE="https://stups.hhu-hosting.de/"
# Keyserver exports are mutable (accumulating third-party signatures),
# which would break Manifest digests on re-fetch, so the key is
# committed to the repository instead. Minimal export of fingerprint
# 6B2CBE657E0BBB9BED2CF32D891C7E1F861DA57F from keyserver.ubuntu.com.
S="${WORKDIR}"

LICENSE="public-domain"
SLOT="0"
KEYWORDS="~amd64"

src_install() {
	insinto /usr/share/openpgp-keys
	newins "${FILESDIR}"/stups-${PV}.asc stups.asc
}
