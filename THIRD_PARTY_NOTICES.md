# External prerequisites and licensing

No third-party binaries, font files, model weights, or source libraries are vendored in this repository. References below describe separately installed tools; they are separate from this project’s MIT license.

- **Python**: runtime, under the [PSF licensing terms](https://docs.python.org/3/license.html).
- **Pillow**: optional Python package used for font metrics, declared in the `render` extra. Its upstream [MIT-CMU license](https://raw.githubusercontent.com/python-pillow/Pillow/main/LICENSE) applies independently. Wheels may contain additional components and notices; consult the distribution actually used.
- **FFmpeg / ffprobe**: external executables, invoked as separate local processes. FFmpeg is generally LGPL-2.1-or-later, with optional GPL components changing the applicable FFmpeg build license. The renderer uses libx264, and the tested build is GPL-enabled. This repository does not redistribute that executable. Review the exact build and [official FFmpeg legal guidance](https://ffmpeg.org/legal.html) before bundling any binary.
- **Noto Sans CJK**: external font files, under [SIL Open Font License 1.1](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE). Font files and their licenses must accompany a future bundled-font distribution as required by that license. They are not bundled here.
- **Node.js**: optional test runtime, not a product dependency. See its [upstream license and bundled notices](https://github.com/nodejs/node/blob/main/LICENSE).
- **GitHub Actions**: workflow references official checkout/setup-python actions; no action code is copied into this tree.

The source includes original synthetic subtitle fixtures and code that generates synthetic video/test tones. No third-party stream footage, music, or commercial sound-effect assets are included. Ownership of a local recording does not automatically grant rights to every embedded third-party work; users remain responsible for authorized use.

Local validation environment: Python 3.12.14, Pillow 12.3.0, FFmpeg 7.1.5, system-installed Noto Sans CJK. This is a tested combination, not a claim of exhaustive platform compatibility.
