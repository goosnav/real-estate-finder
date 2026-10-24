# Native development-build checks

Recorded October 24, 2026 for the initial `0.1.0` implementation. These results distinguish build and Linux headless integration evidence from native desktop acceptance.

## Build evidence

- Built five product-specific images: universal macOS, Windows x64, Windows ARM64, Linux x86_64, Linux ARM64.
- Used Go `1.27.3`, `uv 0.13.5`, managed Python `3.12.12`, and the committed lock/tool pins.
- Passed **11 Go supervisor contract tests**, covering ownership, manifest/readiness checks, retry capability, redaction, runtime retention and source-independent fingerprints.
- Passed **7 packaging-boundary tests**, covering executable modes, exact tool/checksum sets, forbidden paths, placeholders and incomplete payloads.
- New house icon, Real Estate Finder identity and white/ink/blue setup page are embedded in the stable images. Mutable application source remains adjacent in `app/`.

## Linux x86_64 AppImage integration

The actual AppImage ran from a path containing spaces and a Unicode dash, with a new isolated application-data directory. FUSE was bypassed using the AppImage's supported extract-and-run mode. A controlled `xdg-open` test adapter opened an actual isolated headless Chromium browser; this was not an interactive desktop/file-manager trust test.

| Scenario | Observed result |
|---|---|
| First launch | Browser setup page rendered before runtime bootstrap. |
| Managed runtime | Empty app-data downloaded Python 3.12.12 and 26 locked production packages into the external runtime. |
| GUI handoff | Setup redirected to the ready Real Estate Finder browser workspace. |
| Repeat launch | Another launcher reopened the same healthy server PID. |
| Primary workflow | Commercial-buy demo completed with five fictional properties and three draft sequences. |
| Review and export | A property was shortlisted and the GUI downloaded a 40,741-byte CSV. |
| Quit | GUI Quit stopped the app and the owning supervisor exited with code 0. |
| Restart | Five properties and the shortlist persisted. |
| Source-only update | Changing only the test payload's HTML title appeared on restart; launcher hashes and ready-runtime marker were unchanged. |
| Runtime reuse | The source-only restart did not rerun dependency sync. |
| Browser failure recovery | An initially misconfigured test opener triggered `LAUNCH-BROWSER` and wrote the fallback URL; the harness was corrected before the successful clean run. |

No live paid model call or real listing inquiry was made during this smoke test. Test-only changes were confined to a separate staging directory, and all native test instances were shut down.

## Still unverified

The following are not established by these results: clean interactive macOS Intel/Apple Silicon, Windows x64/ARM64 or Linux ARM64 launches; every OS trust prompt; signing/notarization; every disk, permission, TLS/proxy, lockfile and runtime-update failure on each target; clean desktop default-browser behavior; and the complete six-target acceptance matrix.

The package is an **unsigned development build**, and the productization milestone remains active. Source CI and cross-compilation do not promote it to an accepted native release. See [Install](INSTALL.md) for build and launch instructions.
