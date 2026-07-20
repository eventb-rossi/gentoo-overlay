# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

JAVA_PKG_WANT_BUILD_VM="
	openjdk-11 openjdk-bin-11
	openjdk-17 openjdk-bin-17
	openjdk-21 openjdk-bin-21
	openjdk-25 openjdk-bin-25
"
JAVA_PKG_WANT_SOURCE="1.8"
JAVA_PKG_WANT_TARGET="1.8"
JAVA_RM_FILES=( "spins/lib/javacc.jar" )

inherit autotools flag-o-matic java-pkg-2 optfeature shell-completion

SPINS_SHA="bfca30be3ce81fd77b31b9d47043145ae66ddc96"

DESCRIPTION="High-performance language-independent model checking tools"
HOMEPAGE="https://github.com/utwente-fmt/ltsmin"
SRC_URI="
	https://github.com/utwente-fmt/ltsmin/releases/download/v${PV}/ltsmin-v${PV}-source.tgz -> ${P}.tgz
	buddy? (
		https://github.com/utwente-fmt/buddy/archive/refs/tags/v2.4.tar.gz
			-> ${PN}-buddy-2.4.tar.gz
	)
	spins? (
		https://github.com/utwente-fmt/spins/archive/${SPINS_SHA}.tar.gz
			-> ${PN}-spins-${SPINS_SHA}.tar.gz
	)
"
S="${WORKDIR}/${P}"

LICENSE="BSD GPL-2+ buddy? ( buddy ) spins? ( Apache-2.0 )"
SLOT="0"
KEYWORDS="~amd64"
IUSE="+gmp +prob +sylvan boost buddy mpi pnml profiler spins test zip"

BDEPEND="
	>=sys-devel/bison-3.0.2
	sys-devel/flex
	virtual/pkgconfig
	spins? (
		>=dev-java/ant-1.9.8:0
		>=virtual/jdk-11:*
	)
"
COMMON_DEPEND="
	dev-libs/popt
	virtual/zlib
	gmp? ( dev-libs/gmp:= )
	mpi? ( virtual/mpi )
	pnml? ( dev-libs/libxml2:2= )
	prob? ( net-libs/czmq:= )
	profiler? ( dev-util/google-perftools:=[-minimal] )
	sylvan? (
		>=sci-mathematics/sylvan-1.1
		<sci-mathematics/sylvan-1.2
	)
	zip? ( dev-libs/libzip:= )
"
DEPEND="
	${COMMON_DEPEND}
	boost? ( dev-libs/boost )
"
RDEPEND="
	${COMMON_DEPEND}
	spins? (
		sys-devel/gcc
		>=virtual/jre-1.8:*
	)
"

RESTRICT="!test? ( test )"

PATCHES=(
	"${FILESDIR}/${P}-configure-bignum-test.patch"
	"${FILESDIR}/${P}-configure-cache-line-test.patch"
	"${FILESDIR}/${P}-czmq-zsock.patch"
	"${FILESDIR}/${P}-default-listdd64.patch"
	"${FILESDIR}/${P}-mpi3.patch"
	"${FILESDIR}/${P}-prob-lace-options.patch"
	"${FILESDIR}/${P}-ltl2ba-fno-common.patch"
)

DOCS=( AUTHORS CODING-STANDARDS README.md )

pkg_setup() {
	use spins && java-pkg-2_pkg_setup
}

src_prepare() {
	rm "${S}/spins/spins.jar" || die
	rmdir "${S}/spins" || die

	if use spins; then
		mv "${WORKDIR}/spins-${SPINS_SHA}" "${S}/spins" || die
		sed -i -e 's/\r$//' "${S}/spins/src/build.xml" || die
		eapply "${FILESDIR}/${P}-spins-build.patch"
	fi

	if use buddy; then
		pushd "${WORKDIR}/buddy-2.4" >/dev/null || die
		eapply "${FILESDIR}/${PN}-buddy-2.4-bddproduced-type.patch"
		AT_M4DIR=m4 eautoreconf
		popd >/dev/null || die
	fi

	default
	use spins && java-pkg-2_src_prepare

	# Keep the generated Automake 1.14 files from being regenerated after
	# patching their source files and the generated configure script.
	touch -r aclocal.m4 configure.ac m4/acx_cache_line.m4 || die
}

src_configure() {
	local buddy_prefix=${T}/buddy

	# This release and its BuDDy fork contain incompatible declarations
	# across translation units.  GCC LTO diagnoses these and may miscompile
	# the code even where the linker accepts it.
	filter-lto

	if use buddy; then
		# LTSmin needs its own BuDDy fork.  The stock implementation has the
		# same API but aborts when LTSmin creates an FDD domain, and both
		# projects install libbdd.so.  Keep the compatible fork private and
		# link it statically instead of colliding with sci-libs/buddy.
		mkdir "${WORKDIR}/buddy-build" || die
		pushd "${WORKDIR}/buddy-build" >/dev/null || die
		(
			append-flags -fPIC
			ECONF_SOURCE="${WORKDIR}/buddy-2.4" econf \
				--prefix="${buddy_prefix}" \
				--libdir="${buddy_prefix}/lib" \
				--disable-shared \
				--enable-static
			emake
			emake install
		)
		popd >/dev/null || die
	fi

	# The bundled Lemon generator uses pre-C23 K&R function declarations.
	append-cflags -std=gnu17

	local myeconfargs=(
		--disable-doxygen-doc
		--disable-opaal
		--disable-scoop
		--disable-werror
		--with-mcrl=no
		--with-mcrl2=no
		--with-viennacl="${T}/disabled"
		--without-spot
		$(use_enable mpi dist)
		$(use_enable pnml)
		$(use_enable prob)
		$(use_with boost)
		$(use_with spins)
		$(use_with sylvan)
		--with-bignum=$(usex gmp gmp no)
	)

	if use buddy; then
		export PKG_CONFIG_PATH="${buddy_prefix}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
		myeconfargs+=( --with-buddy )
	else
		myeconfargs+=( --without-buddy )
	fi

	# AC_ARG_WITH(profiler) treats --without-profiler as enabled, so omit
	# the switch entirely for the default disabled case.
	use profiler && myeconfargs+=( --with-profiler )

	# libzip has no configure switch in this release.
	use zip || export ac_cv_lib_zip_zip_open=no

	econf "${myeconfargs[@]}"
}

src_compile() {
	use spins && eant -f spins/src/build.xml jar

	# lemon/Makefile replaces rather than augments CFLAGS.
	emake CFLAGS="${CFLAGS}"
}

src_test() {
	# The top-level DejaGNU suite unconditionally exercises optional external
	# compilers such as DiVinE and SpinS.  Run the self-contained unit tests
	# here, then cover the enabled backends explicitly below.
	emake -C src CFLAGS="${CFLAGS}" check

	local testdir=${T}/functional
	mkdir "${testdir}" || die
	cat > "${testdir}/cycle.etf" <<-EOF || die
		begin state
		x:int
		end state
		begin edge
		end edge
		begin init
		0
		end init
		begin trans
		0/1
		1/0
		end trans
	EOF

	local lace_args=()
	use sylvan && lace_args=( --lace-workers=1 )

	local output
	output=$("${S}/src/pins2lts-seq/etf2lts-seq" "${testdir}/cycle.etf" 2>&1) || die
	[[ ${output} == *"2 states 2 transitions"* ]] || die "sequential ETF test failed"
	output=$("${S}/src/pins2lts-sym/etf2lts-sym" "${testdir}/cycle.etf" 2>&1) || die
	[[ ${output} == *"state space has 2 states"* ]] || die "symbolic ETF test failed"

	if use sylvan; then
		[[ ${output} == *"Creating a multi-core ListDD domain"* ]] ||
			die "default symbolic backend is not Sylvan LDDmc"
		output=$("${S}/src/pins2lts-sym/etf2lts-sym" "${lace_args[@]}" \
			--vset=lddmc "${testdir}/cycle.etf" 2>&1) || die
		[[ ${output} == *"Creating a multi-core ListDD domain"* ]] ||
			die "Sylvan LDDmc ETF test failed"
		[[ ${output} == *"state space has 2 states"* ]] ||
			die "Sylvan LDDmc ETF state count failed"
		output=$("${S}/src/pins2lts-sym/etf2lts-sym" "${lace_args[@]}" \
			--vset=sylvan "${testdir}/cycle.etf" 2>&1) || die
		[[ ${output} == *"Creating a Sylvan domain"* ]] ||
			die "Sylvan BDD ETF test failed"
		[[ ${output} == *"state space has 2 states"* ]] ||
			die "Sylvan BDD ETF state count failed"
	fi

	output=$("${S}/src/pins2lts-mc/etf2lts-mc" "${testdir}/cycle.etf" 2>&1) || die
	[[ ${output} == *"2 states 2 transitions"* ]] || die "multicore ETF test failed"

	if use buddy; then
		output=$("${S}/src/pins2lts-sym/etf2lts-sym" "${lace_args[@]}" --vset=fdd \
			"${testdir}/cycle.etf" 2>&1) || die
		[[ ${output} == *"Creating a BuDDy fdd domain"* ]] || die "BuDDy ETF test failed"
	fi

	if use prob; then
		output=$(nonfatal "${S}/src/pins2lts-sym/prob2lts-sym" --help 2>&1)
		[[ ${output} == *"--lace-workers"* ]] || die "ProB Lace options are unavailable"
	fi

	if use spins; then
		pushd "${testdir}" >/dev/null || die
		output=$("${S}/src/scripts/spins" -o "${S}/examples/p117.pml" 2>&1) ||
			die "SpinS failed to compile Promela: ${output}"
		output=$("${S}/src/pins2lts-seq/prom2lts-seq" p117.pml.spins 2>&1) ||
			die "LTSmin failed to explore the SpinS model: ${output}"
		[[ ${output} == *"354 states 828 transitions"* ]] || die "SpinS Promela test failed"
		popd >/dev/null || die
	fi
}

src_install() {
	default

	if ! use mpi; then
		rm -f "${ED}"/usr/bin/*-dist \
			"${ED}"/usr/bin/ce-mpi \
			"${ED}"/usr/bin/ltsmin-reduce-dist || die
	fi
	if ! use spins; then
		rm -f "${ED}"/usr/bin/spins "${ED}"/usr/share/ltsmin/spins.jar || die
	fi

	# Upstream only installs generated manual pages when documentation build
	# tools are detected.  Install the release-matched pages for commands that
	# survived USE filtering, plus the shared format documentation.
	local command page
	for command in "${ED}"/usr/bin/*; do
		[[ -e ${command} ]] || continue
		page="${S}/doc/${command##*/}.1"
		[[ -f ${page} ]] && doman "${page}"
	done
	doman "${S}"/doc/*.5 "${S}/doc/ltsmin.7"
	newbashcomp contrib/bash-completion/ltsmin ltsmin

	find "${ED}" -type f -name '*.la' -delete || die
	rm -f "${ED}/usr/share/doc/${PF}/COPYING" || die
}

pkg_postinst() {
	use prob && optfeature "ProB model checking" sci-mathematics/prob-bin
}
