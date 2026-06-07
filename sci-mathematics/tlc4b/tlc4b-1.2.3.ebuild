# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit java-pkg-2 verify-sig

MAVEN="https://repo1.maven.org/maven2"
TLATOOLS_PV="1.1.0"
PARSER_PV="2.15.4"
SABLECC_PV="3.9.0"
CLI_PV="1.11.0"

DESCRIPTION="Model-check classical B specifications by translating them to TLA+"
HOMEPAGE="https://github.com/hhu-stups/tlc4b"
# Upstream publishes the (thin) release jar and its complete dependency
# closure on Maven Central, where every artifact is PGP-signed.
SRC_URI="
	${MAVEN}/de/hhu/stups/tlc4b/${PV}/${P}.jar
	${MAVEN}/de/hhu/stups/tlatools/${TLATOOLS_PV}/tlatools-${TLATOOLS_PV}.jar
	${MAVEN}/de/hhu/stups/bparser/${PARSER_PV}/bparser-${PARSER_PV}.jar
	${MAVEN}/de/hhu/stups/ltlparser/${PARSER_PV}/ltlparser-${PARSER_PV}.jar
	${MAVEN}/de/hhu/stups/prologlib/${PARSER_PV}/prologlib-${PARSER_PV}.jar
	${MAVEN}/de/hhu/stups/parserbase/${PARSER_PV}/parserbase-${PARSER_PV}.jar
	${MAVEN}/de/hhu/stups/sablecc-runtime/${SABLECC_PV}/sablecc-runtime-${SABLECC_PV}.jar
	${MAVEN}/commons-cli/commons-cli/${CLI_PV}/commons-cli-${CLI_PV}.jar
	verify-sig? (
		${MAVEN}/de/hhu/stups/tlc4b/${PV}/${P}.jar.asc
		${MAVEN}/de/hhu/stups/tlatools/${TLATOOLS_PV}/tlatools-${TLATOOLS_PV}.jar.asc
		${MAVEN}/de/hhu/stups/bparser/${PARSER_PV}/bparser-${PARSER_PV}.jar.asc
		${MAVEN}/de/hhu/stups/ltlparser/${PARSER_PV}/ltlparser-${PARSER_PV}.jar.asc
		${MAVEN}/de/hhu/stups/prologlib/${PARSER_PV}/prologlib-${PARSER_PV}.jar.asc
		${MAVEN}/de/hhu/stups/parserbase/${PARSER_PV}/parserbase-${PARSER_PV}.jar.asc
		${MAVEN}/de/hhu/stups/sablecc-runtime/${SABLECC_PV}/sablecc-runtime-${SABLECC_PV}.jar.asc
		${MAVEN}/commons-cli/commons-cli/${CLI_PV}/commons-cli-${CLI_PV}.jar.asc
	)
"
S="${WORKDIR}"

LICENSE="EPL-1.0"
SLOT="0"
KEYWORDS="~amd64"

RDEPEND=">=virtual/jre-1.8:*"
BDEPEND="
	verify-sig? (
		sec-keys/openpgp-keys-stups
		sec-keys/openpgp-keys-apache-commons
	)
"

VERIFY_SIG_OPENPGP_KEY_PATH=/usr/share/openpgp-keys/stups.asc

src_unpack() {
	# Prebuilt jars, nothing to unpack. The de.hhu.stups artifacts are
	# signed by the STUPS key, commons-cli by an Apache Commons developer.
	if use verify-sig; then
		local f
		for f in ${A}; do
			[[ ${f} == *.asc || ${f} == commons-cli-* ]] && continue
			verify-sig_verify_detached "${DISTDIR}/${f}" "${DISTDIR}/${f}.asc"
		done
		verify-sig_verify_detached \
			"${DISTDIR}/commons-cli-${CLI_PV}.jar" \
			"${DISTDIR}/commons-cli-${CLI_PV}.jar.asc" \
			"${BROOT}/usr/share/openpgp-keys/commons.apache.org.asc"
	fi
}

src_install() {
	local f
	for f in ${A}; do
		[[ ${f} == *.asc ]] && continue
		java-pkg_newjar "${DISTDIR}/${f}" "${f}"
	done
	java-pkg_dolauncher "${PN}" --main de.tlc4b.TLC4B
}
