# eventb-rossi Gentoo overlay

Gentoo ebuild repository with tools for formal modelling and verification
with [Event-B](https://www.event-b.org/) and the B method.

## Installation

The overlay is listed in Gentoo's [official repository list][repos], so with
`app-eselect/eselect-repository` you can enable it by name:

```sh
eselect repository enable eventb-rossi
emaint sync -r eventb-rossi
```

If your `eselect-repository` is too old to know about the overlay, add it
explicitly instead:

```sh
eselect repository add eventb-rossi git https://github.com/eventb-rossi/gentoo-overlay.git
emaint sync -r eventb-rossi
```

Or add `/etc/portage/repos.conf/eventb-rossi.conf` manually:

```ini
[eventb-rossi]
location = /var/db/repos/eventb-rossi
sync-type = git
sync-uri = https://github.com/eventb-rossi/gentoo-overlay.git
auto-sync = yes
```

## Packages

| Package | Description |
|---|---|
| `sci-mathematics/rossi` | Rust toolchain for Event-B: parser, static checker, CLI, and language server |
| `sci-mathematics/rodin` | Rodin Platform — IDE for formal modelling and verification with Event-B |
| `sci-mathematics/rodin-headless` | Headless toolchain to build, model-check, and prove Rodin Event-B models |
| `sci-mathematics/atelier-b` | Atelier B Community Edition — IDE for the B method |
| `sci-mathematics/eventb-checker` | Standalone validator for Event-B models |
| `sci-mathematics/eventb-animate` | Animate Event-B models with the ProB model checker |
| `sci-mathematics/prob2-ui` | JavaFX-based animator and model checker built on ProB |
| `sci-mathematics/prob-bin` | Animator, constraint solver and model checker for B, Event-B, CSP, TLA+, Z |
| `sci-mathematics/eventb-to-txt` | Convert Rodin Event-B models to plain-text format |
| `sci-mathematics/evbt` | Event-B tool for code generation and documentation |
| `sci-mathematics/tlc4b` | Model-check classical B specifications via TLA+/TLC |
| `sci-mathematics/b2program` | Code generator from B to Java, C++, Python, Rust, TypeScript |
| `sec-keys/openpgp-keys-stups` | OpenPGP key used to verify the tlc4b Maven artifacts |

## Notes

### Rodin release candidates

Pre-release versions of Rodin are masked by default. To test a release
candidate, unmask it:

```sh
echo "~sci-mathematics/rodin-3.10.0_rc2" >> /etc/portage/package.unmask
```

### b2program

Upstream publishes no release artifacts, so the package builds from a
pinned snapshot with Gradle, which resolves dependencies from the network
at build time. The ebuild declares `RESTRICT="network-sandbox"`, so
Portage automatically lifts the network isolation for this build; the
downloaded dependencies are not covered by Manifest verification.

### Licenses

`sci-mathematics/atelier-b` is freeware distributed under the Atelier B
Community Edition license. Accept it with:

```sh
echo "sci-mathematics/atelier-b Atelier-B-Community" >> /etc/portage/package.license
```

[repos]: https://repository.gentoo.org/
