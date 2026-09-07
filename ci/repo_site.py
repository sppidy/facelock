"""Assemble the static pacman repo site for GitHub Pages.

Reads dist/*.pkg.tar.zst + facelock.db* + SHA256SUMS and writes site/ with:
  site/aarch64/<pkg>, site/aarch64/facelock.db*, site/aarch64/*.sig? (no),
  site/index.html, site/aarch64/index.html (human index + pacman Server dir).

Unsigned repo: users set SigLevel = Optional TrustAll (documented in README).
"""
import hashlib
from pathlib import Path
import shutil

REPO = "facelock"
ARCH = "aarch64"
SERVER_HINT = "https://facelock-repo.sppidy.in"


def main():
    dist = Path("dist")
    site = Path("site")
    adir = site / ARCH
    if adir.exists():
        shutil.rmtree(adir)
    adir.mkdir(parents=True)
    pkgs = sorted(dist.glob("*.pkg.tar.zst"))
    assert pkgs, "no pacman package in dist/"
    for p in pkgs:
        shutil.copy2(p, adir / p.name)
    for db in sorted(dist.glob(f"{REPO}.db*")) + sorted(dist.glob(f"{REPO}.files*")):
        shutil.copy2(db, adir / db.name)
    sums = (dist / "SHA256SUMS").read_text().splitlines()
    lines = []
    for p in pkgs:
        h = hashlib.sha256((adir / p.name).read_bytes()).hexdigest()
        assert any(h in s and p.name in s for s in sums), p.name
        lines.append(f"{h}  {p.name}")
    (adir / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    listing = "\n".join(f"<li><a href='{p.name}'>{p.name}</a></li>"
                        for p in sorted(adir.iterdir()) if p.is_file())
    (adir / "index.html").write_text(
        f"<html><head><title>facelock {ARCH} repo</title></head><body>"
        f"<h1>facelock pacman repo ({ARCH})</h1>"
        f"<p>Add to pacman.conf:</p><pre>[facelock]\n"
        f"Server = {SERVER_HINT}/$arch</pre><ul>{listing}</ul></body></html>")
    site.joinpath("index.html").write_text(
        "<html><head><title>facelock repo</title></head><body>"
        f"<h1>facelock pacman repo</h1><p><a href='{ARCH}/'>{ARCH}</a></p>"
        "</body></html>")
    print(f"site ready: {len(pkgs)} package(s), db present=" +
          str((adir / f"{REPO}.db.tar.zst").exists()))


if __name__ == "__main__":
    main()
