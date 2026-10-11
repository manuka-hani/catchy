"""日本語フォントの探索。

PDF の文字化けを防ぐため、日本語 TrueType フォントを明示的に埋め込む。
PDF_FONT_PATH / PDF_BOLD_FONT_PATH が指定されていればそれを優先する。
"""

from dataclasses import dataclass
from pathlib import Path

from src.errors import PipelineError

# (通常, 太字) の候補。太字が無い環境では通常フォントで代用する
_CANDIDATES: list[tuple[str, str | None]] = [
    # Windows
    ("C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/YuGothB.ttc"),
    ("C:/Windows/Fonts/BIZ-UDGothicR.ttc", "C:/Windows/Fonts/BIZ-UDGothicB.ttc"),
    ("C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/meiryob.ttc"),
    ("C:/Windows/Fonts/msgothic.ttc", None),
    # Linux (fonts-noto-cjk / fonts-ipafont)
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", None),
    ("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf", None),
    # macOS
    ("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"),
]


@dataclass(frozen=True)
class FontPair:
    regular: Path
    bold: Path


def find_japanese_font(regular: str = "", bold: str = "") -> FontPair:
    if regular:
        path = Path(regular)
        if not path.is_file():
            raise PipelineError("pdf", f"PDF_FONT_PATH のフォントが見つかりません: {regular}")
        bold_path = Path(bold) if bold and Path(bold).is_file() else path
        return FontPair(path, bold_path)

    for reg, bld in _CANDIDATES:
        if Path(reg).is_file():
            bold_path = Path(bld) if bld and Path(bld).is_file() else Path(reg)
            return FontPair(Path(reg), bold_path)

    raise PipelineError(
        "pdf",
        "日本語フォントが見つかりません",
        hint="PDF_FONT_PATH に日本語 TrueType フォント (.ttf/.ttc) のパスを設定してください "
        "(Linux なら `apt install fonts-noto-cjk`)",
    )
