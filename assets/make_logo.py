"""Build the site's logo files from the master image.

The master (`kalys_logo.png`) is white marks on solid black. The web UI needs
other versions of it, so they are derived here instead of by hand: run
`python assets/make_logo.py` after changing the master.

Chainlit picks these up by filename: it serves `public/logo_<theme>.*` for the
header, `public/favicon.*` for the browser tab, and `public/avatars/kalys.*`
for the icon on each answer (that name comes from `name = "Kalys"` in
.chainlit/config.toml, lowercased).
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "kalys_logo.png"
OUT = ROOT / "public"

# Boxes measured in the master image. There is a blank column at x=595 that
# separates the magnifying glass from the letters "alys".
LOCKUP = (252, 432, 998, 766)
# Just the lens: at 32 pixels wide the handle only steals room from the "K".
LENS = (302, 428, 594, 720)


def recolour(box, colour):
    """Crop to box and rebuild it as `colour` on a transparent background.

    The master is white on black, so how bright a pixel is already says how
    opaque it should be. Using the grey level as the alpha channel keeps the
    smooth edges; picking out "every black pixel" instead would leave a dark
    fringe around every curve.
    """
    alpha = Image.open(SRC).convert("L").crop(box)
    image = Image.new("RGBA", alpha.size, colour + (0,))
    image.putalpha(alpha)
    return image


def padded(image, ratio):
    """Add transparent padding around the image, as a share of its height."""
    pad = round(image.height * ratio)
    out = Image.new("RGBA", (image.width + pad * 2, image.height + pad * 2), (0, 0, 0, 0))
    out.paste(image, (pad, pad))
    return out


def tile(size):
    """A square black tile with the lens centred, for the tab and the avatar.

    These two sit on backgrounds we do not control, so the tile keeps a black
    background of its own. A transparent white mark would disappear against a
    light browser tab bar.
    """
    mark = recolour(LENS, (255, 255, 255))
    mark.thumbnail((round(size * 0.78), round(size * 0.78)), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    out.paste(mark, ((size - mark.width) // 2, (size - mark.height) // 2), mark)
    return out


def main():
    (OUT / "avatars").mkdir(parents=True, exist_ok=True)
    # White for the black theme, black for light mode.
    padded(recolour(LOCKUP, (255, 255, 255)), 0.12).save(OUT / "logo_dark.png")
    padded(recolour(LOCKUP, (0, 0, 0)), 0.12).save(OUT / "logo_light.png")
    tile(512).save(OUT / "favicon.png")
    tile(512).save(OUT / "avatars" / "kalys.png")
    print(f"wrote logo, favicon and avatar files to {OUT}")


if __name__ == "__main__":
    main()
