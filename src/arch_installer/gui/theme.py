"""
centralized theme configuration for cyberpunk terminal aesthetic.

all colors, fonts, and styling constants in one place for
consistent look across all GUI components.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CyberpunkTheme:
    """cyberpunk/terminal visual theme configuration.

    all styling parameters for the installer GUI in one place.
    uses only Tkinter-supported styling (no external dependencies).
    """

    # primary colors - neon cyberpunk palette
    neon_cyan: str = "#00ffff"
    neon_green: str = "#00ff88"
    neon_purple: str = "#bf00ff"
    neon_pink: str = "#ff00aa"
    neon_orange: str = "#ff6600"

    # background colors - dark panels
    bg_dark: str = "#0a0a0f"
    bg_panel: str = "#12121a"
    bg_input: str = "#1a1a24"
    bg_hover: str = "#1e1e2a"
    bg_selected: str = "#2a2a3a"

    # text colors
    text_primary: str = "#e0e0e0"
    text_secondary: str = "#808090"
    text_dim: str = "#505060"
    text_accent: str = "#00ffff"  # cyan for emphasis

    # status colors
    success: str = "#00ff88"
    warning: str = "#ffaa00"
    error: str = "#ff4444"
    info: str = "#00aaff"

    # border colors
    border_normal: str = "#303040"
    border_active: str = "#00ffff"
    border_error: str = "#ff4444"

    # fonts (using monospace for terminal feel)
    font_family: str = "monospace"
    font_size_title: int = 24
    font_size_header: int = 18
    font_size_normal: int = 12
    font_size_small: int = 10

    # layout
    window_width: int = 1280
    window_height: int = 720
    padding_large: int = 30
    padding_medium: int = 20
    padding_small: int = 10
    padding_tiny: int = 5

    # button dimensions
    button_width: int = 30
    button_height: int = 2
    button_padding_x: int = 20
    button_padding_y: int = 10

    def get_font(self, size: Optional[int] = None, bold: bool = False) -> tuple:
        """get a font tuple for Tkinter widgets."""
        size = size or self.font_size_normal
        weight = "bold" if bold else "normal"
        return (self.font_family, size, weight)

    def get_title_font(self) -> tuple:
        """get font for main titles."""
        return self.get_font(self.font_size_title, bold=True)

    def get_header_font(self) -> tuple:
        """get font for section headers."""
        return self.get_font(self.font_size_header, bold=True)

    def get_button_font(self) -> tuple:
        """get font for buttons."""
        return self.get_font(self.font_size_normal, bold=True)

    def get_label_font(self) -> tuple:
        """get font for labels."""
        return self.get_font(self.font_size_normal)

    def get_small_font(self) -> tuple:
        """get font for small text."""
        return self.get_font(self.font_size_small)


# singleton theme instance
THEME = CyberpunkTheme()
