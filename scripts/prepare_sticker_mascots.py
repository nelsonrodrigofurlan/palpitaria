"""
Gera versões dos mascotes prontas para figurinha (iPhone / WhatsApp).

- Remove o fundo
- Adiciona contorno branco controlado (o iOS também adiciona um ao salvar;
  com fundo transparente + outline prévio o resultado fica mais limpo)
- Padding extra para o recorte não cortar o contorno

Uso:
  .venv\\Scripts\\python.exe scripts/prepare_sticker_mascots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "palpitaria" / "static" / "assets" / "mascots"
OUT = SRC / "stickers"

OUTLINE_PX = 12
PADDING_PX = 28
ALPHA_CUTOFF = 40


def remove_background(img: Image.Image) -> Image.Image:
    try:
        from rembg import remove
    except ImportError as exc:
        raise SystemExit(
            "Instale dependências: .venv\\Scripts\\pip.exe install pillow rembg"
        ) from exc
    return remove(img.convert("RGBA"))


def dilate_alpha(alpha: Image.Image, radius: int) -> Image.Image:
    mask = alpha.point(lambda a: 255 if a > ALPHA_CUTOFF else 0)
    for _ in range(radius):
        mask = mask.filter(ImageFilter.MaxFilter(3))
    return mask


def to_sticker(img: Image.Image, outline_px: int = OUTLINE_PX, padding_px: int = PADDING_PX) -> Image.Image:
    img = img.convert("RGBA")
    alpha = img.split()[3]
    outer = dilate_alpha(alpha, outline_px)

    w, h = img.size
    pad = padding_px + outline_px
    canvas = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))

    white_ring = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    white_ring.putalpha(outer)
    canvas.paste(white_ring, (pad, pad), white_ring)
    canvas.paste(img, (pad, pad), img)
    return canvas


def process_one(path: Path) -> Path:
    print(f"  {path.name} ...", flush=True)
    raw = Image.open(path)
    cutout = remove_background(raw)
    sticker = to_sticker(cutout)
    out_path = OUT / path.name.replace(".png", "_sticker.png")
    sticker.save(out_path, "PNG", optimize=True)
    return out_path


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    sources = sorted(SRC.glob("mascot_*.png"))
    if not sources:
        print(f"Nenhum mascote em {SRC}", file=sys.stderr)
        return 1

    print(f"Gerando figurinhas em {OUT}")
    for path in sources:
        out = process_one(path)
        print(f"    -> {out.name}")

    print("Pronto. Envie os *_sticker.png para o iPhone (AirDrop / iCloud / WhatsApp).")
    print("No iPhone: abra a imagem > toque e segure > Adicionar figurinha (iOS 17+).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
