"""Рисует иконку приложения: packaging/icon.ico (Windows), icon.icns (macOS), icon.png (Linux и окно).

Рисуем крупно (1024 px) и уменьшаем под каждый размер с качественным сглаживанием — иконка чёткая
и на 16 px в панели задач, и на 256 px в проводнике. Нужен Pillow; вызывается из build.py.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BIG = 1024
TOP, BOTTOM = (59, 130, 246), (29, 78, 216)  # синий градиент сверху вниз
FONTS = ("segoeuib.ttf", "arialbd.ttf", "Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise SystemExit("не найден жирный шрифт для иконки (Segoe UI, Arial или DejaVu Sans)")


def draw(size: int = BIG, compact: bool = False) -> Image.Image:
    """compact — для 16–32 px (трей, панель задач, заголовок окна): почти без полей и с крупными буквами,
    иначе на 16 px «hh» превращается в кашу из пары пикселей."""
    scale = size / BIG
    pad_px, radius_px, font_px = (8, 200, 640) if compact else (24, 224, 520)
    gradient = Image.new("RGBA", (size, size))
    line = ImageDraw.Draw(gradient)
    for y in range(size):
        t = y / (size - 1)
        line.line([(0, y), (size, y)], fill=tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (size, size), 0)
    pad = round(pad_px * scale)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, size - pad, size - pad], radius=round(radius_px * scale), fill=255)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(gradient, (0, 0), mask)

    canvas = ImageDraw.Draw(img)
    font = _font(round(font_px * scale))
    # центрируем по реальным границам букв, а не по строке шрифта: «hh» стоит ровно посередине
    left, top, right, bottom = canvas.textbbox((0, 0), "hh", font=font)
    x = (size - (right - left)) / 2 - left
    y = (size - (bottom - top)) / 2 - top
    canvas.text((x, y), "hh", font=font, fill="white")
    return img


def main() -> None:
    big, small = draw(), draw(compact=True)
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    # маленькие размеры — из «компактного» рисунка, большие — из обычного; уменьшение с LANCZOS
    frames = [(small if s <= 32 else big).resize((s, s), Image.Resampling.LANCZOS) for s in sizes]
    frames[-1].save(HERE / "icon.ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    big.resize((512, 512), Image.Resampling.LANCZOS).save(HERE / "icon.png")
    try:
        big.save(HERE / "icon.icns")
    except Exception as e:  # старый Pillow без записи ICNS — нужен только для сборки на macOS
        print(f"  ! icon.icns не записан: {e}")
    print(f"иконка: {HERE / 'icon.ico'}, icon.png, icon.icns")


if __name__ == "__main__":
    main()
