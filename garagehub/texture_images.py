"""Shared image bounds for Blender preparation and native material sampling."""
import io
from PIL import Image

from .errors import GarageError

MAX_SOURCE_PIXELS = 4096 ** 2  # Full-resolution RGBA working image.
MAX_INPUT_PIXELS = 8192 ** 2   # The largest source we will ask a decoder to open.
NATIVE_COLOUR_EDGE = 512


def open_texture(data, name):
    advice = 'Resize a copy to 8192 × 8192 or smaller in Blender and export again.'
    try:
        image = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as exc:
        raise GarageError(f'“{name}” exceeds the supported texture size. {advice}', 'texture_size') from exc
    except (OSError, ValueError) as exc:
        raise GarageError(f'“{name}” could not be opened as a readable image. Export a single PNG or JPEG.', 'unsupported_texture') from exc
    width, height = image.size
    if width * height > MAX_INPUT_PIXELS:
        image.close()
        raise GarageError(f'“{name}” is {width} × {height} pixels. {advice}', 'texture_size')
    return image


def native_thumbnail(image):
    """Produce the native colour input while bounding extra decode/conversion buffers."""
    target = (NATIVE_COLOUR_EDGE, NATIVE_COLOUR_EDGE)
    if image.width * image.height > MAX_SOURCE_PIXELS and image.format == 'JPEG':
        image.draft(None, target)
    if image.width * image.height <= MAX_SOURCE_PIXELS:
        rgba = image.convert('RGBA')
    else:
        # PNG and WebP can require a full decoder raster. Convert small tiles
        # so a palette/colour key resolves to alpha before filtering without
        # allocating a second full-resolution RGBA image. Aligned box groups
        # have exactly the same samples on either side of every tile boundary.
        tile_edge = 1024
        factor = min(tile_edge, max(2, max(image.size) // (2 * NATIVE_COLOUR_EDGE)))
        step = tile_edge // factor * factor
        rgba = Image.new('RGBA', ((image.width + factor - 1) // factor,
                                  (image.height + factor - 1) // factor))
        for y in range(0, image.height, step):
            for x in range(0, image.width, step):
                box = (x, y, min(x + step, image.width), min(y + step, image.height))
                with image.crop(box) as tile, tile.convert('RGBA') as colours, colours.reduce(factor) as reduced:
                    rgba.paste(reduced, (x // factor, y // factor))
    rgba.thumbnail(target, Image.Resampling.LANCZOS)
    return rgba
