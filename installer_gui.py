#!/usr/bin/env python3
"""
ZeroLive Interactive Installer — PyQt6 GUI
Wizard-style: Welcome → Theme → Options → Install → Done
"""

import sys, os, json, urllib.request, zipfile, io, tempfile, shutil, subprocess, threading, ctypes, math
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QProgressBar, QFileDialog, QCheckBox, QTextEdit, QMessageBox,
    QFrame, QGridLayout, QButtonGroup, QSizePolicy, QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPointF, QRect, QSize
from PyQt6.QtGui import QPen
from PyQt6.QtGui import (
    QFont, QColor, QLinearGradient, QRadialGradient, QBrush, QPainter,
    QFontDatabase, QPainterPath, QPixmap, QPalette, QAction, QIcon
)

# ═══════════════════════════════════════════════════════════════════════════
#  THEMES
# ═══════════════════════════════════════════════════════════════════════════
THEMES = {
    "dark":      {"bg":"#080b14","bg2":"#0d1225","card":"#111830","hover":"#1a2340","a1":"#ff2d55","a2":"#7936f7","text":"#eef2ff","sec":"#949dcc","tert":"#5c6692"},
    "light":     {"bg":"#f2f4f8","bg2":"#ffffff","card":"#ffffff","hover":"#eef0f4","a1":"#e53935","a2":"#7c3aed","text":"#1a1d28","sec":"#6b7280","tert":"#9ca3af"},
    "amethyst":  {"bg":"#0a0814","bg2":"#120e20","card":"#18142e","hover":"#221e3e","a1":"#a855f7","a2":"#d946ef","text":"#f0e6ff","sec":"#b8a0d8","tert":"#7868a0"},
    "emerald":   {"bg":"#060e08","bg2":"#0a1a10","card":"#0e2416","hover":"#143420","a1":"#22d65e","a2":"#10b981","text":"#e8f5e9","sec":"#81c784","tert":"#4caf50"},
    "ruby":      {"bg":"#14080a","bg2":"#1e0c0e","card":"#281016","hover":"#341a22","a1":"#ef4444","a2":"#f43f5e","text":"#ffe8e8","sec":"#d4a0a0","tert":"#a06868"},
    "ocean":     {"bg":"#080e14","bg2":"#0c1625","card":"#101e30","hover":"#182840","a1":"#06b6d4","a2":"#3b82f6","text":"#e6f4ff","sec":"#90b8d4","tert":"#5880a8"},
    "cyberpunk": {"bg":"#0a0a14","bg2":"#12101e","card":"#1a1630","hover":"#242048","a1":"#ff2d95","a2":"#00f5ff","text":"#f0e6ff","sec":"#c0a8d8","tert":"#8070a0"},
    "sunset":    {"bg":"#12080a","bg2":"#1a0c0e","card":"#241016","hover":"#341a22","a1":"#ff6b35","a2":"#f43f5e","text":"#fff0e8","sec":"#d4b0a0","tert":"#a07868"},
    "nord":      {"bg":"#0d121a","bg2":"#131a24","card":"#17222e","hover":"#202e3e","a1":"#88c0d0","a2":"#81a1c1","text":"#eef4f8","sec":"#a0b8c8","tert":"#688098"},
    "matrix":    {"bg":"#080c08","bg2":"#0c120c","card":"#101a10","hover":"#182818","a1":"#00ff41","a2":"#22d65e","text":"#d0ffd0","sec":"#70b070","tert":"#306830"},
    "midnight":  {"bg":"#080814","bg2":"#0e0e1e","card":"#14142a","hover":"#1e1e3e","a1":"#6366f1","a2":"#8b5cf6","text":"#e8e6ff","sec":"#a0a0d0","tert":"#606090"},
}

DEFAULT_THEME = "dark"

# ── Constants ────────────────────────────────────────────────────────────────
GITHUB_REPO  = "https://github.com/rafu-milonmart/my-proxy-project"
GITHUB_API   = "https://api.github.com/repos/rafu-milonmart/my-proxy-project"
PY_VER       = "3.13.5"
PY_URL       = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
PIP_URL      = "https://bootstrap.pypa.io/get-pip.py"
DEFAULT_DIR  = "C:\\Zero_live"
STEPS        = ["Welcome", "Theme", "Options", "Install", "Done"]


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def _qcolor(hex_color):
    h = hex_color.lstrip("#")
    return QColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

def _alpha(hex_color, a):
    c = _qcolor(hex_color)
    c.setAlphaF(a)
    return c

# ═══════════════════════════════════════════════════════════════════════════
#  THEME SWATCH
# ═══════════════════════════════════════════════════════════════════════════
class ThemeSwatch(QPushButton):
    selected = pyqtSignal(str)

    def __init__(self, name, colors, parent=None):
        super().__init__(parent)
        self._name = name
        self._colors = colors
        self._active = False
        self.setMinimumSize(QSize(80, 52))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda: self.selected.emit(name))

    def set_active(self, active):
        self._active = active
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        bg = _qcolor(self._colors["card"])
        p.setBrush(QBrush(bg))
        pen_w = 2 if self._active else 1
        pen_c = _qcolor(self._colors["a2"]) if self._active else _qcolor(self._colors["bg2"]).lighter(130)
        p.setPen(QPen(pen_c, pen_w))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 10, 10)

        grad = QLinearGradient(2, h - 6, w - 2, h - 6)
        grad.setColorAt(0, _qcolor(self._colors["a1"]))
        grad.setColorAt(1, _qcolor(self._colors["a2"]))
        p.fillRect(QRect(2, h - 6, w - 4, 4), QBrush(grad))

        c1 = _qcolor(self._colors["a1"])
        c2 = _qcolor(self._colors["a2"])
        p.setBrush(QBrush(c1))
        p.setPen(QPen(c2.darker(120), 1))
        p.drawEllipse(10, 10, 14, 14)
        p.setBrush(QBrush(c2))
        p.drawEllipse(28, 10, 14, 14)

        p.setPen(Qt.GlobalColor.white if self._name != "light" else Qt.GlobalColor.black)
        f = QFont("Segoe UI", 8, QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(QRect(0, 32, w, 28), Qt.AlignmentFlag.AlignCenter, self._name.capitalize())
        p.end()

# ═══════════════════════════════════════════════════════════════════════════
#  ANIMATED ORB BACKGROUND
# ═══════════════════════════════════════════════════════════════════════════
class OrbWidget(QWidget):
    def __init__(self, theme_colors, parent=None):
        super().__init__(parent)
        self._colors = theme_colors
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def set_theme(self, colors):
        self._colors = colors

    def _tick(self):
        self._t += 0.02
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        t = self._t
        a1 = _qcolor(self._colors["a1"])
        a2 = _qcolor(self._colors["a2"])

        orbs = [
            (0.20, 0.10, 400, a1, 0.09, (0, 0)),
            (0.80, 0.90, 350, a2, 0.08, (-4, -4)),
            (0.50, 0.50, 300, a1, 0.05, (-2, -2)),
            (0.15, 0.80, 250, a1, 0.04, (-6, -6)),
            (0.70, 0.30, 200, a2, 0.04, (-8, -8)),
        ]

        for cx, cy, size, color, alpha, offset in orbs:
            ox, oy = offset
            x = w * cx + math.sin(t + ox) * w * 0.04
            y = h * cy + math.cos(t * 0.8 + oy) * h * 0.04
            r = size * 0.5
            grad = QRadialGradient(QPointF(x, y), r)
            c = QColor(color)
            c.setAlphaF(alpha)
            grad.setColorAt(0, c)
            c2 = QColor(color)
            c2.setAlphaF(0)
            grad.setColorAt(1, c2)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x, y), r, r)
        p.end()

# ═══════════════════════════════════════════════════════════════════════════
#  GLASS CARD
# ═══════════════════════════════════════════════════════════════════════════
class GlassCard(QFrame):
    def __init__(self, theme_colors, parent=None):
        super().__init__(parent)
        self._colors = theme_colors
        self.setObjectName("glassCard")

    def set_theme(self, colors):
        self._colors = colors
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        R = 14

        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, R, R)
        p.setClipPath(path)

        bg = _qcolor(self._colors["bg2"])
        bg.setAlphaF(0.85)
        p.fillRect(self.rect(), QBrush(bg))

        line = _alpha(self._colors["a2"], 0.12)
        p.setPen(QPen(line, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(0, 0, w - 1, h - 1, R, R)

        grad = QLinearGradient(0, 0, 0, h)
        c1 = QColor(255, 255, 255, 18)
        c2 = QColor(255, 255, 255, 0)
        c3 = QColor(255, 255, 255, 6)
        grad.setColorAt(0, c1)
        grad.setColorAt(0.5, c2)
        grad.setColorAt(1, c3)
        p.fillRect(self.rect(), QBrush(grad))

        glow = QLinearGradient(0, h - 2, w, h - 2)
        a1 = _qcolor(self._colors["a1"])
        a2 = _qcolor(self._colors["a2"])
        c0 = QColor(a1)
        c0.setAlphaF(0)
        c1 = QColor(a1)
        c1.setAlphaF(0.25)
        c2 = QColor(a2)
        c2.setAlphaF(0)
        glow.setColorAt(0, c0)
        glow.setColorAt(0.5, c1)
        glow.setColorAt(1, c2)
        p.fillRect(0, h - 1, w, 1, QBrush(glow))
        p.end()

# ═══════════════════════════════════════════════════════════════════════════
#  STYLED WIDGETS
# ═══════════════════════════════════════════════════════════════════════════
def _css_button(colors, primary=True):
    a1, a2 = colors["a1"], colors["a2"]
    if primary:
        return f"""
            QPushButton {{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {a1}, stop:1 {a2});
                color:#fff; border:none; border-radius:8px;
                padding:10px 28px; font-size:13px; font-weight:700;
            }}
            QPushButton:hover {{
                border:1px solid rgba(255,255,255,.2);
            }}
            QPushButton:disabled {{
                background:{colors["card"]}; color:{colors["tert"]};
            }}
        """
    else:
        return f"""
            QPushButton {{
                background:transparent; color:{colors["sec"]};
                border:1px solid {_alpha(colors["a2"], 0.12).name()}; border-radius:8px;
                padding:10px 24px; font-size:13px; font-weight:600;
            }}
            QPushButton:hover {{
                background:{colors["hover"]}; color:{colors["text"]};
                border-color:{a2};
            }}
        """

class AccentBtn(QPushButton):
    def __init__(self, text, colors, primary=True, parent=None):
        super().__init__(text, parent)
        self._colors = colors
        self._primary = primary
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self._apply()

    def set_theme(self, colors):
        self._colors = colors
        self._apply()

    def _apply(self):
        self.setStyleSheet(_css_button(self._colors, self._primary))

class GhostBtn(QPushButton):
    def __init__(self, text, colors, parent=None):
        super().__init__(text, parent)
        self._colors = colors
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self._apply()

    def set_theme(self, colors):
        self._colors = colors
        self._apply()

    def _apply(self):
        self.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{self._colors["tert"]};
                border:none; border-radius:6px; padding:6px 16px;
                font-size:12px; font-weight:600;
            }}
            QPushButton:hover {{
                background:{self._colors["hover"]}; color:{self._colors["text"]};
            }}
            QPushButton:disabled {{ color:{self._colors["tert"]}; }}
        """)

class TitleLbl(QLabel):
    def __init__(self, text, colors, size=22, parent=None):
        super().__init__(text, parent)
        self._colors = colors
        self._size = size
        self.setWordWrap(True)
        self._apply()

    def set_theme(self, colors):
        self._colors = colors
        self._apply()

    def _apply(self):
        self.setStyleSheet(f"font-size:{self._size}px; font-weight:900; color:{self._colors['text']};")

class BodyLbl(QLabel):
    def __init__(self, text, colors, color_key="sec", parent=None):
        super().__init__(text, parent)
        self._colors = colors
        self._ckey = color_key
        self.setWordWrap(True)
        self._apply()

    def set_theme(self, colors):
        self._colors = colors
        self._apply()

    def _apply(self):
        c = self._colors.get(self._ckey, self._colors["sec"])
        self.setStyleSheet(f"color:{c}; font-size:13px; line-height:1.5;")

class StepLbl(QLabel):
    def __init__(self, text, colors, parent=None):
        super().__init__(text, parent)
        self._colors = colors
        self._apply()

    def set_theme(self, colors):
        self._colors = colors
        self._apply()

    def _apply(self):
        self.setStyleSheet(f"""
            color:{self._colors["a2"]}; font-size:10px; font-weight:800;
            letter-spacing:2px; text-transform:uppercase;
        """)

class StyledLineEdit(QLineEdit):
    def __init__(self, text, colors, parent=None):
        super().__init__(text, parent)
        self._colors = colors
        self._apply()

    def set_theme(self, colors):
        self._colors = colors
        self._apply()

    def _apply(self):
        self.setStyleSheet(f"""
            QLineEdit {{
                background:{self._colors["card"]}; color:{self._colors["text"]};
                border:1px solid {_alpha(self._colors["a2"], 0.12).name()}; border-radius:8px;
                padding:10px 14px; font-size:13px;
            }}
            QLineEdit:focus {{ border-color:{self._colors["a2"]}; }}
        """)

class StyledCheckBox(QCheckBox):
    def __init__(self, text, colors, parent=None):
        super().__init__(text, parent)
        self._colors = colors
        self._apply()

    def set_theme(self, colors):
        self._colors = colors
        self._apply()

    def _apply(self):
        line = _alpha(self._colors["a2"], 0.12).name()
        self.setStyleSheet(f"""
            QCheckBox {{ color:{self._colors["sec"]}; font-size:13px; spacing:10px; }}
            QCheckBox::indicator {{
                width:18px; height:18px; border-radius:4px;
                border:2px solid {line}; background:{self._colors["card"]};
            }}
            QCheckBox::indicator:checked {{
                background:{self._colors["a2"]}; border-color:{self._colors["a2"]};
            }}
        """)

# ═══════════════════════════════════════════════════════════════════════════
#  PATH PICKER
# ═══════════════════════════════════════════════════════════════════════════
class PathPicker(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, initial, colors, parent=None):
        super().__init__(parent)
        self._colors = colors
        self._path = initial
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._edit = StyledLineEdit(initial, colors, self)
        self._edit.textChanged.connect(self._on_edit)
        layout.addWidget(self._edit, 1)

        self._btn = QPushButton("Browse")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setFixedHeight(40)
        self._btn.clicked.connect(self._browse)
        self._update_btn_style()
        layout.addWidget(self._btn)

    def set_theme(self, colors):
        self._colors = colors
        self._edit.set_theme(colors)
        self._update_btn_style()

    def _update_btn_style(self):
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background:{self._colors["card"]}; color:{self._colors["sec"]};
                border:1px solid {_alpha(self._colors["a2"], 0.12).name()}; border-radius:8px;
                padding:10px 18px; font-size:12px; font-weight:600;
            }}
            QPushButton:hover {{
                background:{self._colors["a2"]}; color:#fff; border-color:{self._colors["a2"]};
            }}
        """)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose Install Directory", self._path)
        if d:
            self._edit.setText(d)

    def _on_edit(self, t):
        self._path = t
        self.changed.emit(t)

    @property
    def path(self):
        return self._edit.text().strip()

# ═══════════════════════════════════════════════════════════════════════════
#  INSTALL WORKER
# ═══════════════════════════════════════════════════════════════════════════
class InstallWorker(QThread):
    progress = pyqtSignal(int, str)
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, dest: Path, shortcuts: bool, firewall: bool, theme: str = "dark"):
        super().__init__()
        self.dest = dest
        self.shortcuts = shortcuts
        self.firewall = firewall
        self._theme = theme
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _check(self):
        if self._cancel:
            raise RuntimeError("Cancelled")

    def _dl(self, url, path, desc=""):
        lbl = desc or url.split('/')[-1]
        self.log.emit(f"[DL] {lbl}")
        last = -1
        def reporthook(c, b, t):
            nonlocal last
            if self._cancel:
                raise RuntimeError("Cancelled")
            if t > 0:
                pct = c * b * 100.0 / t
                pct = min(pct, 100.0)
                ip = int(pct // 5)
                if ip != last:
                    last = ip
                    self.log.emit(f"     {pct:.0f}%")
        urllib.request.urlretrieve(url, path, reporthook=reporthook)
        self.log.emit(f"[OK] {lbl}")

    def run(self):
        try:
            base = self.dest
            pdir = base / "python"

            # ── 1  Python ──
            self.progress.emit(4, "Downloading Python…")
            py_zip = Path(tempfile.mktemp(suffix=".zip"))
            self._dl(PY_URL, str(py_zip), f"Python {PY_VER} (embed-amd64)")

            self._check()
            self.progress.emit(16, "Extracting Python…")
            pdir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(py_zip) as z:
                z.extractall(pdir)
            py_zip.unlink(missing_ok=True)

            for pth in pdir.glob("python*._pth"):
                pth.write_text(pth.read_text().replace("#import site", "import site"))
            self.log.emit("[OK]  Python extracted, site-packages enabled")

            # ── 2  pip ──
            self.progress.emit(26, "Setting up pip…")
            pip_py = Path(tempfile.mktemp(suffix=".py"))
            self._dl(PIP_URL, str(pip_py), "get-pip.py")
            py_exe = pdir / "python.exe"
            r = subprocess.run(
                [str(py_exe), str(pip_py), "--no-setuptools", "--no-wheel"],
                capture_output=True, timeout=120,
            )
            pip_py.unlink(missing_ok=True)
            if r.returncode != 0:
                self.log.emit(f"[WARN]  get-pip.py exit {r.returncode}, retrying with --trusted-host")
                subprocess.run(
                    [str(py_exe), str(pip_py), "--no-setuptools", "--no-wheel",
                     "--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org"],
                    capture_output=True, timeout=120,
                )

            pip_exe = pdir / "Scripts" / "pip.exe"
            self.log.emit("[OK]  pip ready")

            # ── 3  App files ──
            self._check()
            self.progress.emit(38, "Downloading app files…")
            zip_path = Path(tempfile.mktemp(suffix=".zip"))
            self._dl(f"{GITHUB_REPO}/archive/master.zip", str(zip_path), "ZeroLive source")

            self.progress.emit(52, "Extracting app files…")
            extract_dir = Path(tempfile.mkdtemp())
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(extract_dir)
            zip_path.unlink(missing_ok=True)

            repo_root = next((c for c in extract_dir.iterdir() if c.is_dir()), None)
            if repo_root is None:
                raise RuntimeError("Could not find repo root in zip")

            skip = {"python", "Zero_live.bat", "version.txt", "installer_gui.py", "build_installer.py"}
            for item in repo_root.iterdir():
                if item.name in skip:
                    continue
                dst = base / item.name
                if item.is_dir():
                    if dst.exists():
                        shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)
            shutil.rmtree(extract_dir, ignore_errors=True)
            self.log.emit("[OK]  App files copied")

            # ── 4  Dependencies ──
            self._check()
            self.progress.emit(66, "Installing dependencies…")
            req = base / "requirements.txt"
            if req.exists():
                cmd = [str(pip_exe)] if pip_exe.exists() else [str(py_exe), "-m", "pip"]
                subprocess.run(cmd + ["install", "-r", str(req), "--quiet"], timeout=180)
                self.log.emit("[OK]  Dependencies installed")
            else:
                self.log.emit("[WARN]  No requirements.txt found")

            # ── 5  Version ──
            self._check()
            self.progress.emit(76, "Writing version…")
            try:
                r = urllib.request.Request(f"{GITHUB_API}/commits/master",
                                           headers={"User-Agent": "ZeroLive"})
                with urllib.request.urlopen(r, timeout=10) as f:
                    sha = json.loads(f.read())["sha"]
                (base / "version.txt").write_text(sha)
                self.log.emit(f"[OK]  Version {sha[:12]}")
            except Exception:
                (base / "version.txt").write_text("1.0.0")

            # ── 6  Shortcuts ──
            self._check()
            self.progress.emit(84, "Creating shortcuts…")
            if self.shortcuts:
                ps = '\n'.join([
                    '$ws = New-Object -ComObject WScript.Shell',
                    f'$s = $ws.CreateShortcut([Environment]::GetFolderPath("Desktop") + "\\ZeroLive.lnk")',
                    f'$s.TargetPath = "{base}\\Zero_live.bat"',
                    f'$s.WorkingDirectory = "{base}"',
                    f'$s.Description = "ZeroLive – Free Sports Streaming"',
                    f'$s.Save()',
                    f'$s2 = $ws.CreateShortcut([Environment]::GetFolderPath("Desktop") + "\\ZeroLive Uninstall.lnk")',
                    f'$s2.TargetPath = "{base}\\uninstall.bat"',
                    f'$s2.WorkingDirectory = "{base}"',
                    f'$s2.Description = "Remove ZeroLive"',
                    f'$s2.Save()',
                ])
                subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, timeout=30)
                self.log.emit("[OK]  Desktop shortcuts created")

            # ── 7  Firewall ──
            self._check()
            self.progress.emit(92, "Firewall…")
            if self.firewall and is_admin():
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "add", "rule",
                     "name=ZeroLive", "dir=in", "action=allow",
                     f"program={pdir / 'python.exe'}", "profile=private", "enable=yes"],
                    capture_output=True, timeout=15,
                )
                self.log.emit("[OK]  Firewall rule added")

            # ── 8  README ──
            readme = base / "readme.txt"
            if not readme.exists():
                readme.write_text(
                    "╔══════════════════════════════════════╗\n"
                    "║    ZeroLive – Free Sports Streaming  ║\n"
                    "╚══════════════════════════════════════╝\n\n"
                    "🔸 Double-click 'ZeroLive' on desktop to start\n"
                    "🔸 Opens at http://127.0.0.1:9090\n\n"
                    "CONTROLS:\n"
                    "  Space  – Play/Pause        F  – Fullscreen\n"
                    "  M      – Mute/Unmute        I  – Stream Info\n"
                    "  S      – Speed               ←→ – Seek\n"
                    "  ↑↓     – Volume\n\n"
                    "🔸 Double-click 'ZeroLive Uninstall' to remove\n"
                )
                self.log.emit("[OK]  README created")

            # ── 9  Zero_live.bat ──
            if repo_root:
                src_bat = repo_root / "Zero_live.bat"
                if src_bat.exists():
                    shutil.copy2(src_bat, base / "Zero_live.bat")

            # ── 10  Default theme ──
            (base / "default_theme.txt").write_text(self._theme)

            self.progress.emit(100, "Complete!")
            self.done.emit(True, "ZeroLive has been installed successfully!")

        except RuntimeError:
            self.done.emit(False, "Installation cancelled.")
        except Exception as e:
            self.done.emit(False, f"Error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════════════
class InstallerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._theme_name = DEFAULT_THEME
        self._colors = dict(THEMES[DEFAULT_THEME])
        self._worker = None
        self._page = 0  # 0=welcome, 1=theme, 2=options, 3=install, 4=done
        self._build_ui()
        self._apply_root_style()

    def _apply_root_style(self):
        self.setStyleSheet(f"""
            QWidget {{ background:transparent; color:{self._colors["text"]};
                font-family:'Segoe UI','Inter',sans-serif; }}
            QProgressBar {{
                background:{self._colors["card"]};
                border:1px solid {_alpha(self._colors["a2"], 0.12).name()};
                border-radius:6px; height:6px; text-align:center; font-size:0px;
            }}
            QProgressBar::chunk {{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {self._colors["a1"]}, stop:1 {self._colors["a2"]});
                border-radius:6px;
            }}
            QTextEdit {{
                background:{self._colors["bg"]}; color:{self._colors["sec"]};
                border:1px solid {_alpha(self._colors["a2"], 0.12).name()}; border-radius:8px;
                padding:8px; font-size:12px;
                font-family:'Consolas','Courier New',monospace;
            }}
        """)

    def _build_ui(self):
        self.setWindowTitle("ZeroLive Installer")
        self.setMinimumSize(600, 520)
        self.resize(760, 660)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # animated background
        self._orb_bg = OrbWidget(self._colors, self)
        self._orb_bg.lower()

        # ── Header ──
        header = QWidget()
        header.setFixedHeight(100)
        hl = QVBoxLayout(header)
        hl.setContentsMargins(36, 24, 36, 8)
        hl.setSpacing(4)

        logo_row = QHBoxLayout()
        logo_row.setSpacing(10)
        self._logo_badge = QLabel("Z")
        self._logo_badge.setFixedSize(30, 30)
        self._logo_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_row.addWidget(self._logo_badge)
        self._title = QLabel("ZeroLive")
        logo_row.addWidget(self._title)
        logo_row.addStretch()
        hl.addLayout(logo_row)

        self._header_sub = QLabel("Interactive Installer")
        self._header_sub.setContentsMargins(40, 0, 0, 0)
        hl.addWidget(self._header_sub)
        root.addWidget(header)

        # ── Step indicator ──
        self._step_lbl = StepLbl("", self._colors)
        self._step_lbl.setContentsMargins(36, 0, 36, 0)
        root.addWidget(self._step_lbl)

        # steps bar
        steps_bar_w = QWidget()
        self._steps_bar = QHBoxLayout(steps_bar_w)
        self._steps_bar.setContentsMargins(36, 4, 36, 8)
        self._steps_bar.setSpacing(6)
        self._step_dots = []
        self._step_seps = []
        for name in STEPS:
            dot = QLabel(f"● {name}")
            dot.setStyleSheet(f"color:{self._colors['tert']}; font-size:10px; font-weight:600;")
            self._step_dots.append(dot)
            self._steps_bar.addWidget(dot)
            if name != STEPS[-1]:
                sep = QLabel("─")
                self._step_seps.append(sep)
                self._steps_bar.addWidget(sep)
            self._steps_bar.addStretch(0)
        self._steps_bar.addStretch()
        root.addWidget(steps_bar_w)

        # ── Card ──
        self._card = GlassCard(self._colors)
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setContentsMargins(28, 24, 28, 24)
        self._card_layout.setSpacing(12)
        root.addWidget(self._card, 1)

        # progress + log (hidden by default)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setFixedHeight(6)
        self._card_layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setVisible(False)
        self._log.setFixedHeight(120)
        self._card_layout.addWidget(self._log)

        self._card_layout.addStretch()

        # ── Footer buttons ──
        footer = QWidget()
        footer.setFixedHeight(76)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(28, 12, 28, 20)

        self._cancel_btn = GhostBtn("Cancel", self._colors)
        self._cancel_btn.clicked.connect(self._on_cancel)

        self._back_btn = GhostBtn("←  Back", self._colors)
        self._back_btn.clicked.connect(self._on_back)

        self._nav_btn = AccentBtn("Next  →", self._colors, primary=True)
        self._nav_btn.clicked.connect(self._on_nav)

        fl.addWidget(self._cancel_btn)
        fl.addStretch()
        fl.addWidget(self._back_btn)
        fl.addSpacing(8)
        fl.addWidget(self._nav_btn)
        root.addWidget(footer)

        # Brand
        self._brand = QLabel("MADE BY RAFIUL HASAN RAFI")
        self._brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._brand.setStyleSheet(f"color:{self._colors['tert']}; font-size:9px; font-weight:700; letter-spacing:2px; padding:6px 0;")
        root.addWidget(self._brand)

        self._sync_header_theme()
        self._show_page(0)
        self._update_steps(0)

    def _update_steps(self, page):
        for i, dot in enumerate(self._step_dots):
            if i < page:
                dot.setStyleSheet(f"color:{self._colors['sec']}; font-size:10px; font-weight:600;")
            elif i == page:
                grad = f"qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {self._colors['a1']},stop:1 {self._colors['a2']})"
                dot.setStyleSheet(f"""
                    font-size:10px; font-weight:800;
                    background:{grad}; -webkit-background-clip:text;
                    background-clip:text; color:transparent;
                """)
            else:
                dot.setStyleSheet(f"color:{self._colors['tert']}; font-size:10px; font-weight:600;")

    # ── Page Router ─────────────────────────────────────────────────────────
    def _show_page(self, page):
        self._page = page
        self._clear_card()
        self._progress.setVisible(False)
        self._log.setVisible(False)
        self._update_steps(page)

        # default button state
        self._cancel_btn.setVisible(True)
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("Cancel")
        self._back_btn.setVisible(True)
        self._nav_btn.setVisible(True)
        self._nav_btn.setEnabled(True)

        if page == 0:
            self._show_welcome_page()
        elif page == 1:
            self._show_theme_page()
        elif page == 2:
            self._show_options_page()
        elif page == 3:
            self._show_install_page()
        elif page == 4:
            self._show_done_page()

    def _clear_card(self):
        for i in reversed(range(self._card_layout.count())):
            w = self._card_layout.itemAt(i).widget()
            if w and w not in (self._progress, self._log):
                w.deleteLater()

    # ── Page 0: Welcome ───────────────────────────────────────────────────
    def _show_welcome_page(self):
        self._step_lbl.setText("WELCOME")
        self._back_btn.setVisible(False)
        self._nav_btn.setText("Next  →")
        self._cancel_btn.setVisible(True)

        self._card_layout.addStretch()
        icon = QLabel("⚡")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size:48px;")
        self._card_layout.addWidget(icon)
        self._card_layout.addWidget(TitleLbl("Welcome to ZeroLive", self._colors, 26))
        self._card_layout.addWidget(BodyLbl(
            "This will install ZeroLive — a free live sports streaming player — "
            "on your PC. Everything is self-contained in one folder. "
            "No registry changes, no PATH modifications.",
            self._colors
        ))
        self._card_layout.addSpacing(8)
        features = QLabel(
            "• Live football, cricket, basketball, tennis & more\n"
            "• Auto-retry with fallback servers\n"
            "• 11 beautiful themes\n"
            "• VLC / IPTV app support via M3U"
        )
        features.setStyleSheet(f"color:{self._colors['sec']}; font-size:12px; line-height:1.8; padding-left:4px;")
        self._card_layout.addWidget(features)
        ver = self._get_current_version()
        info = QLabel(f"ZeroLive {ver}  ·  Python {PY_VER}")
        info.setStyleSheet(f"color:{self._colors['tert']}; font-size:10px;")
        info.setWordWrap(True)
        self._card_layout.addWidget(info)
        self._card_layout.addStretch()

    # ── Page 1: Theme ─────────────────────────────────────────────────────
    def _show_theme_page(self):
        self._step_lbl.setText("CHOOSE THEME")
        self._back_btn.setVisible(True)
        self._nav_btn.setText("Next  →")
        self._cancel_btn.setVisible(True)

        self._card_layout.addWidget(TitleLbl("Choose Your Theme", self._colors, 22))
        self._card_layout.addWidget(BodyLbl("Pick a look for ZeroLive. You can change it anytime from the settings.", self._colors))

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; } QScrollBar:vertical { width:6px; }")

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background:transparent;")
        scroll_grid = QGridLayout(scroll_content)
        scroll_grid.setSpacing(8)
        scroll_grid.setContentsMargins(2, 2, 2, 2)

        self._theme_swatches = {}
        names = list(THEMES.keys())
        row, col = 0, 0
        for i, name in enumerate(names):
            swatch = ThemeSwatch(name, THEMES[name])
            swatch.selected.connect(self._on_theme_selected)
            if name == self._theme_name:
                swatch.set_active(True)
            self._theme_swatches[name] = swatch
            scroll_grid.addWidget(swatch, row, col)
            col += 1
            if col > 5:
                col = 0
                row += 1
        scroll_grid.setRowStretch(row + 1, 1)

        scroll_area.setWidget(scroll_content)
        self._card_layout.addWidget(scroll_area, 1)

    def _sync_header_theme(self):
        a1, a2 = self._colors["a1"], self._colors["a2"]
        self._logo_badge.setStyleSheet(f"""
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 {a1}, stop:1 {a2});
            color:#fff; font-size:15px; font-weight:800; border-radius:8px;
        """)
        self._title.setStyleSheet(f"font-size:24px; font-weight:900; letter-spacing:1px; color:#fff;")
        self._header_sub.setStyleSheet(f"color:{self._colors['tert']}; font-size:12px; font-weight:500; letter-spacing:1px;")
        self._brand.setStyleSheet(f"color:{self._colors['tert']}; font-size:9px; font-weight:700; letter-spacing:2px; padding:6px 0;")
        for s in self._step_seps:
            s.setStyleSheet(f"color:{_alpha(self._colors['a2'], 0.12).name()}; font-size:10px;")

    def _on_theme_selected(self, name):
        for n, s in self._theme_swatches.items():
            s.set_active(n == name)
        self._theme_name = name
        self._colors = dict(THEMES[name])
        self._sync_header_theme()
        self._orb_bg.set_theme(self._colors)
        self._card.set_theme(self._colors)
        self._apply_root_style()
        self._step_lbl.set_theme(self._colors)
        self._nav_btn.set_theme(self._colors)
        self._back_btn.set_theme(self._colors)
        self._cancel_btn.set_theme(self._colors)
        self._update_steps(self._page)

    # ── Page 2: Options ───────────────────────────────────────────────────
    def _show_options_page(self):
        self._step_lbl.setText("INSTALL OPTIONS")
        self._back_btn.setVisible(True)
        self._nav_btn.setText("Install")
        self._cancel_btn.setVisible(True)

        self._card_layout.addWidget(TitleLbl("Install Options", self._colors, 22))
        self._card_layout.addWidget(BodyLbl(
            f"Theme: <b style='color:{self._colors['a1']}'>{self._theme_name.capitalize()}</b>  ·  "
            "You can change this later.",
            self._colors
        ))

        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background:{_alpha(self._colors['a2'], 0.12).name()};")
        self._card_layout.addWidget(div)

        path_lbl = QLabel("Install Destination")
        path_lbl.setStyleSheet(f"color:{self._colors['text']}; font-size:13px; font-weight:600;")
        self._card_layout.addWidget(path_lbl)

        self._path_picker = PathPicker(DEFAULT_DIR, self._colors)
        self._card_layout.addWidget(self._path_picker)

        self._card_layout.addSpacing(4)
        self._shortcuts_cb = StyledCheckBox("Create desktop shortcuts", self._colors)
        self._shortcuts_cb.setChecked(True)
        self._card_layout.addWidget(self._shortcuts_cb)

        self._firewall_cb = StyledCheckBox("Add Windows Firewall rule (requires admin)", self._colors)
        self._firewall_cb.setChecked(is_admin())
        self._card_layout.addWidget(self._firewall_cb)

        self._card_layout.addStretch()

        ver = self._get_current_version()
        info = QLabel(f"ZeroLive {ver}  ·  Python {PY_VER}")
        info.setStyleSheet(f"color:{self._colors['tert']}; font-size:10px;")
        info.setWordWrap(True)
        self._card_layout.addWidget(info)

    def _get_current_version(self):
        try:
            vf = Path(__file__).parent / "version.txt"
            if vf.exists():
                return vf.read_text().strip()[:12]
        except Exception:
            pass
        return "latest"

    # ── Page 3: Installing ────────────────────────────────────────────────
    def _show_install_page(self):
        self._step_lbl.setText("INSTALLING")
        self._back_btn.setVisible(False)
        self._nav_btn.setText("Cancel")
        self._cancel_btn.setVisible(True)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._log.setVisible(True)
        self._log.clear()

        self._card_layout.addWidget(TitleLbl("Installing…", self._colors, 20))
        self._card_layout.addWidget(BodyLbl("Downloading and configuring ZeroLive. This may take a few minutes.", self._colors))
        self._card_layout.addStretch()

    # ── Page 4: Done ──────────────────────────────────────────────────────
    def _show_done_page(self, success=True, msg="ZeroLive has been installed successfully!"):
        self._step_lbl.setText("COMPLETE" if success else "FAILED")
        self._back_btn.setVisible(False)
        self._nav_btn.setVisible(True)
        self._cancel_btn.setVisible(False)
        self._progress.setVisible(False)
        self._log.setVisible(False)

        self._nav_btn.setText("Close")

        if success:
            self._card_layout.addStretch()
            icon = QLabel("✓")
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setStyleSheet(f"""
                font-size:48px; font-weight:900;
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {self._colors['a1']}, stop:1 {self._colors['a2']});
                -webkit-background-clip:text; background-clip:text; color:transparent;
                padding:8px;
            """)
            self._card_layout.addWidget(icon)
            self._card_layout.addWidget(TitleLbl("Installation Complete", self._colors, 22))
            self._card_layout.addWidget(BodyLbl(msg, self._colors))
            self._card_layout.addSpacing(8)

            btn_row = QHBoxLayout()
            launch_btn = AccentBtn("Launch ZeroLive", self._colors)
            launch_btn.clicked.connect(self._launch)
            btn_row.addWidget(launch_btn)
            open_btn = AccentBtn("Open Folder", self._colors, primary=False)
            open_btn.clicked.connect(self._open_folder)
            btn_row.addWidget(open_btn)
            btn_row.addStretch()
            self._card_layout.addLayout(btn_row)

            self._card_layout.addStretch()
            self._card_layout.addWidget(BodyLbl(
                'Or double-click "ZeroLive" on your desktop to start anytime.',
                self._colors, "tert"
            ))
        else:
            self._card_layout.addStretch()
            icon2 = QLabel("✕")
            icon2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon2.setStyleSheet(f"font-size:48px; font-weight:900; color:{self._colors['a1']}; padding:8px;")
            self._card_layout.addWidget(icon2)
            self._card_layout.addWidget(TitleLbl("Installation Failed", self._colors, 22))
            self._card_layout.addWidget(BodyLbl(msg, self._colors, "a1"))
            self._card_layout.addStretch()

    # ── Navigation ────────────────────────────────────────────────────────
    def _on_nav(self):
        t = self._nav_btn.text()
        if t == "Next  →":
            self._show_page(self._page + 1)
        elif t == "Install":
            self._start_install()
        elif t == "Cancel":
            if self._worker and self._worker.isRunning():
                self._worker.cancel()
                self._nav_btn.setEnabled(False)
                self._nav_btn.setText("Cancelling…")
                self._cancel_btn.setEnabled(False)
            else:
                self.close()
        elif t == "Close":
            self._quit_app()

    def _on_back(self):
        if self._page > 0:
            self._show_page(self._page - 1)

    def _on_cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._cancel_btn.setEnabled(False)
            self._cancel_btn.setText("Cancelling…")
            self._nav_btn.setEnabled(False)
        else:
            self._quit_app()

    def _start_install(self):
        dest = Path(self._path_picker.path)
        if not dest:
            QMessageBox.warning(self, "Path Required", "Please select an install directory.")
            return
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Path Error", f"Cannot create directory:\n{e}")
            return

        self._show_page(3)
        self._worker = InstallWorker(
            dest, self._shortcuts_cb.isChecked(), self._firewall_cb.isChecked(),
            theme=self._theme_name
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self._on_log)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, pct, text):
        self._progress.setValue(pct)
        self._step_lbl.setText(f"INSTALLING  —  {pct}%")

    def _on_log(self, text):
        self._log.append(text)

    def _on_done(self, success, msg):
        self._show_done_page(success, msg)

    def _launch(self):
        bat = Path(self._path_picker.path) / "Zero_live.bat"
        if bat.exists():
            subprocess.Popen(
                ['cmd.exe', '/c', 'start', '', str(bat)],
                cwd=str(bat.parent), shell=False,
                creationflags=subprocess.DETACHED_PROCESS,
                stdin=None, stdout=None, stderr=None
            )
        self._quit_app()

    def _quit_app(self):
        self.close()
        QApplication.quit()

    def _open_folder(self):
        os.startfile(self._path_picker.path)


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY
# ═══════════════════════════════════════════════════════════════════════════
def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    QFontDatabase.addApplicationFont("Inter.ttf")
    w = InstallerWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
