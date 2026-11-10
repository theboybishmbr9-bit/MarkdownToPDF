from __future__ import annotations

import ctypes
import os
import queue
import re
import sys
import threading
from html import escape
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# -----------------------------------------------------------------------------
# Playwright browser location
# -----------------------------------------------------------------------------
# When packaged with PyInstaller, --add-data should place the browser folder at
# <bundle> / ms-playwright. In a normal Python run, Playwright uses its default
# browser cache unless a local ms-playwright folder exists beside this script.

if getattr(sys, "frozen", False):
    APP_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    APP_DIR = Path(__file__).resolve().parent

BROWSER_DIR = APP_DIR / "ms-playwright"
if BROWSER_DIR.is_dir():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSER_DIR)

# -----------------------------------------------------------------------------
# Third-party imports
# -----------------------------------------------------------------------------
import markdown
from markdown.extensions.codehilite import CodeHiliteExtension
from playwright.sync_api import sync_playwright
from pygments.formatters import HtmlFormatter


APP_TITLE = "Markdown to PDF Converter"
APP_VERSION = "2.0.0"
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"

# -----------------------------------------------------------------------------
# PDF stylesheet
# -----------------------------------------------------------------------------
CSS = r"""
@page {
    size: A4;
    margin: 19mm 17mm 21mm 17mm;
    @top-right {
        content: string(document-title);
        color: #64748b;
        font-size: 7.5pt;
    }
    @bottom-center {
        content: counter(page) " / " counter(pages);
        color: #64748b;
        font-size: 8pt;
    }
}
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
    font-family: "Segoe UI", Arial, sans-serif;
    color: #202938;
    font-size: 10.2pt;
    line-height: 1.58;
    overflow-wrap: anywhere;
}
main { max-width: 100%; }
h1, h2, h3, h4, h5, h6 {
    color: #14243a;
    line-height: 1.25;
    margin: 1.3em 0 .55em;
    page-break-after: avoid;
    break-after: avoid;
}
h1 {
    string-set: document-title content();
    font-size: 24pt;
    border-bottom: 2px solid #d9e2ef;
    padding-bottom: 9px;
}
h2 { font-size: 17pt; border-bottom: 1px solid #e5eaf1; padding-bottom: 5px; }
h3 { font-size: 13.5pt; }
h4 { font-size: 11.5pt; }
p { margin: .62em 0; orphans: 3; widows: 3; }
a { color: #1459a6; text-decoration: underline; }
ul, ol { padding-left: 1.5em; }
li { margin: .2em 0; }
blockquote {
    margin: 1em 0;
    padding: .25em 1em;
    border-left: 4px solid #7c9cc5;
    background: #f3f6fa;
    color: #40516a;
    page-break-inside: avoid;
    break-inside: avoid;
}
hr { border: 0; border-top: 1px solid #dbe2ea; margin: 1.5em 0; }
table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 8.8pt;
    page-break-inside: auto;
}
thead { display: table-header-group; }
tfoot { display: table-footer-group; }
tr { page-break-inside: avoid; break-inside: avoid; }
th, td {
    border: 1px solid #cbd5e1;
    padding: 6px 8px;
    text-align: left;
    vertical-align: top;
    overflow-wrap: anywhere;
}
th { background: #eaf0f7; color: #172b45; font-weight: 700; }
tr:nth-child(even) td { background: #f8fafc; }
pre {
    background: #f4f6f8;
    border: 1px solid #dce3eb;
    border-radius: 5px;
    padding: 10px 12px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: 8.1pt;
    line-height: 1.42;
    page-break-inside: avoid;
    break-inside: avoid;
}
code { font-family: Consolas, "Cascadia Code", "Courier New", monospace; font-size: .9em; }
:not(pre) > code { background: #eef2f6; padding: 2px 4px; border-radius: 3px; }
img { max-width: 100%; height: auto; }
figure { margin: 1.2em 0; text-align: center; page-break-inside: avoid; }
figcaption { color: #64748b; font-size: 8.5pt; margin-top: .35em; }
.task-list { list-style: none; padding-left: 1.2em; }
.codehilite { background: #f4f6f8; border-radius: 5px; }
.codehilite pre { border: 0; margin: 0; }
.toc { background: #f7f9fc; border: 1px solid #dce3eb; padding: 12px 18px; }
.mermaid { text-align: center; margin: 1.2em 0; page-break-inside: avoid; break-inside: avoid; }
.mermaid svg { max-width: 100% !important; height: auto !important; }
.mermaid-fallback {
    text-align: left;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: #fff8e8;
    border: 1px solid #e7c979;
    padding: 10px;
    font-family: Consolas, monospace;
    font-size: 8pt;
}
.admonition { border-left: 4px solid #7c9cc5; padding: 8px 14px; background: #f5f8fc; }
@media print {
    a { color: #1459a6; }
    img, svg { page-break-inside: avoid; }
}
"""


MERMAID_PATTERN = re.compile(
    r"^```mermaid[ \t]*\r?\n(.*?)^```[ \t]*$",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def clean_chatgpt_mermaid_css(source: str) -> tuple[str, int]:
    """Remove copied ChatGPT Mermaid theme <style> blocks from Markdown.

    These blocks are presentation CSS, not Mermaid diagram source. The function
    removes only style blocks containing ChatGPT's generated Mermaid selector.
    """
    style_pattern = re.compile(
        r"<style\b[^>]*>.*?#chatgpt-mermaid-.*?</style\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    removed_lines = 0

    def remove_style(match: re.Match[str]) -> str:
        nonlocal removed_lines
        removed_lines += match.group(0).count("\n") + 1
        return ""

    cleaned = style_pattern.sub(remove_style, source)
    return cleaned, removed_lines


def markdown_to_html(
    source: str,
    title: str,
    enable_mermaid: bool,
    clean_artifacts: bool = True,
    base_uri: str | None = None,
) -> tuple[str, int, int]:
    removed_lines = 0
    if clean_artifacts:
        source, removed_lines = clean_chatgpt_mermaid_css(source)

    mermaid_count = 0
    if enable_mermaid:
        def replace_mermaid(match: re.Match[str]) -> str:
            nonlocal mermaid_count
            mermaid_count += 1
            # HTML-escape the source so Markdown/HTML parsing cannot reinterpret it.
            diagram_source = escape(match.group(1).strip())
            return f'\n<div class="mermaid">{diagram_source}</div>\n'

        source = MERMAID_PATTERN.sub(replace_mermaid, source)

    extensions = [
        "extra",
        "sane_lists",
        "toc",
        "footnotes",
        "attr_list",
        "md_in_html",
        "tables",
        "fenced_code",
        CodeHiliteExtension(
            css_class="codehilite",
            guess_lang=False,
            noclasses=False,
        ),
    ]
    md = markdown.Markdown(extensions=extensions, output_format="html5")
    body = md.convert(source)

    pygments_css = HtmlFormatter(style="friendly").get_style_defs(".codehilite")
    base_tag = f'<base href="{escape(base_uri, quote=True)}">' if base_uri else ""

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
{base_tag}
<style>{CSS}\n{pygments_css}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>"""
    return html_doc, removed_lines, mermaid_count


def convert_markdown_to_pdf(
    source: str,
    output_path: Path,
    title: str,
    enable_mermaid: bool,
    clean_artifacts: bool,
    base_uri: str | None,
    status_callback,
) -> str:
    """Convert Markdown text to an A4 PDF using Playwright's Chromium browser."""
    html_doc, removed_lines, mermaid_count = markdown_to_html(
        source,
        title,
        enable_mermaid,
        clean_artifacts=clean_artifacts,
        base_uri=base_uri,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_default_timeout(25_000)
            page.set_content(html_doc, wait_until="domcontentloaded", timeout=60_000)

            if removed_lines:
                status_callback(
                    f"Removed approximately {removed_lines} lines of copied Mermaid CSS."
                )

            if enable_mermaid and mermaid_count:
                try:
                    status_callback(
                        f"Loading Mermaid.js for {mermaid_count} diagram(s)..."
                    )
                    page.add_script_tag(url=MERMAID_CDN, timeout=25_000)
                    page.evaluate(
                        """async () => {
                            if (!window.mermaid) {
                                throw new Error("Mermaid.js did not load.");
                            }
                            window.mermaid.initialize({
                                startOnLoad: false,
                                theme: "neutral",
                                securityLevel: "strict",
                                flowchart: {
                                    htmlLabels: true,
                                    useMaxWidth: true
                                }
                            });
                            await window.mermaid.run({
                                querySelector: ".mermaid",
                                suppressErrors: false
                            });
                        }"""
                    )
                    page.wait_for_function(
                        """() => Array.from(document.querySelectorAll('.mermaid'))
                            .every(element => element.querySelector('svg'))""",
                        timeout=25_000,
                    )
                    status_callback("Mermaid diagrams rendered successfully.")
                except Exception:
                    # Keep the source visible in the PDF if the CDN is unavailable
                    # or the supplied Mermaid syntax is invalid.
                    page.evaluate(
                        """() => {
                            document.querySelectorAll('.mermaid').forEach(element => {
                                if (!element.querySelector('svg')) {
                                    element.classList.add('mermaid-fallback');
                                }
                            });
                        }"""
                    )
                    status_callback(
                        "Mermaid could not render (network or syntax issue). "
                        "The diagram source will appear as text in the PDF."
                    )

            page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=False,
                tagged=True,
            )
        finally:
            browser.close()

    notes: list[str] = []
    if removed_lines:
        notes.append(f"cleaned approximately {removed_lines} copied CSS lines")
    if enable_mermaid and mermaid_count == 0:
        notes.append("no Mermaid source blocks found")
    return "; ".join(notes)


# -----------------------------------------------------------------------------
# Windows display scaling
# -----------------------------------------------------------------------------
def enable_windows_dpi_awareness() -> None:
    """Ask Windows to render the app at the monitor's native DPI scale."""
    if sys.platform != "win32":
        return

    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2 (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        # Tkinter will use the operating system's default scaling behavior.
        pass


# -----------------------------------------------------------------------------
# Desktop application
# -----------------------------------------------------------------------------
class ConverterApp:
    BG = "#f3f6fb"
    CARD = "#ffffff"
    TEXT = "#172033"
    MUTED = "#65738a"
    ACCENT = "#245fe5"
    BORDER = "#dce3ee"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("960x760")
        self.root.minsize(800, 650)
        self.root.configure(bg=self.BG)

        self.events: queue.Queue[tuple] = queue.Queue()
        self.is_busy = False

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.mermaid_var = tk.BooleanVar(value=True)
        self.clean_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Choose a Markdown file or write content manually.")

        self._configure_style()
        self._build_ui()
        self.root.after(100, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure(
            "Header.TLabel",
            background=self.BG,
            foreground=self.TEXT,
            font=("Segoe UI", 22, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=self.BG,
            foreground=self.MUTED,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Section.TLabel",
            background=self.CARD,
            foreground=self.TEXT,
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Muted.Card.TLabel",
            background=self.CARD,
            foreground=self.MUTED,
            font=("Segoe UI", 9),
        )
        style.configure("TLabel", background=self.BG, foreground=self.TEXT)
        style.configure(
            "TNotebook",
            background=self.BG,
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            padding=(18, 10),
            background="#e8edf5",
            foreground="#43516a",
            borderwidth=0,
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.CARD), ("active", "#f8faff")],
            foreground=[("selected", self.ACCENT), ("active", self.TEXT)],
        )
        style.configure(
            "TButton",
            padding=(13, 8),
            background="#e8edf5",
            foreground=self.TEXT,
            borderwidth=0,
            focusthickness=0,
            font=("Segoe UI", 10),
        )
        style.map(
            "TButton",
            background=[("disabled", "#e7ebf2"), ("active", "#dce5f4")],
            foreground=[("disabled", "#9aa4b2")],
        )
        style.configure(
            "Primary.TButton",
            padding=(20, 11),
            background=self.ACCENT,
            foreground="#ffffff",
            borderwidth=0,
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", "#9eb5ef"), ("active", "#174cc7")],
            foreground=[("disabled", "#ffffff")],
        )
        style.configure(
            "TEntry",
            padding=(9, 8),
            fieldbackground="#ffffff",
            foreground=self.TEXT,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
        )
        style.configure(
            "TCheckbutton",
            background=self.CARD,
            foreground=self.TEXT,
            padding=(2, 4),
            font=("Segoe UI", 9),
        )
        style.map("TCheckbutton", background=[("active", self.CARD)])
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#e6ebf3",
            background=self.ACCENT,
            bordercolor="#e6ebf3",
            lightcolor=self.ACCENT,
            darkcolor=self.ACCENT,
            thickness=7,
        )

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=(26, 22, 26, 20))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_TITLE, style="Header.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Turn Markdown into clean, print-ready A4 PDF documents.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            header,
            text=f"VERSION {APP_VERSION}",
            style="Subtitle.TLabel",
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))

        source_heading = ttk.Frame(shell)
        source_heading.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        ttk.Label(
            source_heading,
            text="1. Choose your content",
            font=("Segoe UI", 12, "bold"),
            foreground=self.TEXT,
        ).pack(side="left")
        ttk.Label(
            source_heading,
            text="Open a .md file or type/paste Markdown directly.",
            style="Subtitle.TLabel",
        ).pack(side="right")

        self.notebook = ttk.Notebook(shell)
        self.notebook.grid(row=2, column=0, sticky="nsew")

        self.file_tab = ttk.Frame(self.notebook, padding=20, style="Card.TFrame")
        self.manual_tab = ttk.Frame(self.notebook, padding=18, style="Card.TFrame")
        self.notebook.add(self.file_tab, text="  Markdown file  ")
        self.notebook.add(self.manual_tab, text="  Write / paste text  ")
        self._build_file_tab()
        self._build_manual_tab()

        output_card = ttk.Frame(shell, padding=(17, 14), style="Card.TFrame")
        output_card.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        output_card.columnconfigure(0, weight=1)
        ttk.Label(
            output_card,
            text="2. Save PDF to",
            style="Section.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        output_row = ttk.Frame(output_card, style="Card.TFrame")
        output_row.grid(row=1, column=0, sticky="ew")
        output_row.columnconfigure(0, weight=1)
        ttk.Entry(output_row, textvariable=self.output_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 9)
        )
        ttk.Button(
            output_row,
            text="Browse…",
            command=self.choose_output,
        ).grid(row=0, column=1)

        options_card = ttk.Frame(shell, padding=(17, 10), style="Card.TFrame")
        options_card.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(options_card, text="PDF options", style="Section.TLabel").pack(
            anchor="w", pady=(0, 4)
        )
        ttk.Checkbutton(
            options_card,
            text="Render Mermaid diagrams (requires internet access)",
            variable=self.mermaid_var,
        ).pack(anchor="w")
        ttk.Checkbutton(
            options_card,
            text="Remove copied ChatGPT Mermaid CSS artifacts",
            variable=self.clean_var,
        ).pack(anchor="w")

        bottom = ttk.Frame(shell)
        bottom.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        bottom.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(bottom, mode="indeterminate")
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 9))
        self.status_label = ttk.Label(
            bottom,
            textvariable=self.status_var,
            style="Subtitle.TLabel",
            wraplength=650,
        )
        self.status_label.grid(row=1, column=0, sticky="w", padx=(0, 12))
        self.convert_button = ttk.Button(
            bottom,
            text="Create PDF  →",
            style="Primary.TButton",
            command=self.start_conversion,
        )
        self.convert_button.grid(row=1, column=1, sticky="e")

    def _build_file_tab(self) -> None:
        self.file_tab.columnconfigure(0, weight=1)
        self.file_tab.rowconfigure(2, weight=1)

        ttk.Label(
            self.file_tab,
            text="Convert an existing Markdown document",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.file_tab,
            text="Select a .md or .markdown file. Its folder is used to resolve relative image paths.",
            style="Muted.Card.TLabel",
            wraplength=760,
        ).grid(row=1, column=0, sticky="w", pady=(5, 17))

        file_row = ttk.Frame(self.file_tab, style="Card.TFrame")
        file_row.grid(row=2, column=0, sticky="new")
        file_row.columnconfigure(0, weight=1)
        ttk.Entry(file_row, textvariable=self.input_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 9)
        )
        ttk.Button(file_row, text="Choose file…", command=self.choose_input).grid(
            row=0, column=1
        )

    def _build_manual_tab(self) -> None:
        self.manual_tab.columnconfigure(0, weight=1)
        self.manual_tab.rowconfigure(2, weight=1)

        ttk.Label(
            self.manual_tab,
            text="Write or paste your Markdown content",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        toolbar = ttk.Frame(self.manual_tab, style="Card.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(7, 8))
        ttk.Label(
            toolbar,
            text="Supports headings, lists, tables, links, images and code blocks.",
            style="Muted.Card.TLabel",
        ).pack(side="left")
        ttk.Button(toolbar, text="Clear editor", command=self.clear_editor).pack(
            side="right"
        )

        editor_frame = ttk.Frame(self.manual_tab, style="Card.TFrame")
        editor_frame.grid(row=2, column=0, sticky="nsew")
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)

        self.editor = tk.Text(
            editor_frame,
            wrap="word",
            undo=True,
            maxundo=-1,
            font=("Consolas", 10),
            background="#fbfcff",
            foreground="#202938",
            insertbackground=self.ACCENT,
            selectbackground="#cfe0ff",
            selectforeground="#172033",
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            highlightcolor=self.ACCENT,
            padx=13,
            pady=12,
            spacing1=2,
            spacing3=2,
        )
        self.editor.grid(row=0, column=0, sticky="nsew")
        editor_scroll = ttk.Scrollbar(
            editor_frame,
            orient="vertical",
            command=self.editor.yview,
        )
        editor_scroll.grid(row=0, column=1, sticky="ns")
        self.editor.configure(yscrollcommand=editor_scroll.set)

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------
    def choose_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Markdown file",
            filetypes=[("Markdown files", "*.md *.markdown"), ("All files", "*.*")],
        )
        if path:
            selected = Path(path)
            self.input_var.set(str(selected))
            self.output_var.set(str(selected.with_suffix(".pdf")))

    def choose_output(self) -> None:
        initial_name = "document.pdf"
        if self.input_var.get().strip():
            initial_name = Path(self.input_var.get().strip()).with_suffix(".pdf").name

        path = filedialog.asksaveasfilename(
            title="Save PDF",
            defaultextension=".pdf",
            initialfile=initial_name,
            filetypes=[("PDF files", "*.pdf")],
        )
        if path:
            self.output_var.set(path)

    def clear_editor(self) -> None:
        self.editor.delete("1.0", "end")
        self.editor.focus_set()

    def _current_source(self) -> tuple[str, str, str | None]:
        """Return Markdown text, document title, and optional base URI."""
        manual_mode = self.notebook.index("current") == 1

        if manual_mode:
            source = self.editor.get("1.0", "end-1c")
            if not source.strip():
                raise ValueError("The Markdown editor is empty. Enter or paste some text first.")
            output_name = Path(self.output_var.get().strip()).stem or "document"
            return source, output_name, None

        input_text = self.input_var.get().strip()
        input_path = Path(input_text) if input_text else None
        if input_path is None or not input_path.is_file():
            raise ValueError("Please choose an existing Markdown file first.")

        source = input_path.read_text(encoding="utf-8-sig")
        base_uri = input_path.parent.resolve().as_uri() + "/"
        return source, input_path.stem, base_uri

    def start_conversion(self) -> None:
        if self.is_busy:
            return

        try:
            source, title, base_uri = self._current_source()
        except (OSError, UnicodeError, ValueError) as exc:
            messagebox.showerror("Cannot start conversion", str(exc), parent=self.root)
            return

        output_text = self.output_var.get().strip()
        if not output_text:
            suggested_name = f"{title or 'document'}.pdf"
            output_text = filedialog.asksaveasfilename(
                title="Save PDF",
                defaultextension=".pdf",
                initialfile=suggested_name,
                filetypes=[("PDF files", "*.pdf")],
            )
            if not output_text:
                return
            self.output_var.set(output_text)

        output_path = Path(output_text).expanduser()
        if output_path.suffix.lower() != ".pdf":
            output_path = output_path.with_suffix(".pdf")
            self.output_var.set(str(output_path))

        enable_mermaid = self.mermaid_var.get()
        clean_artifacts = self.clean_var.get()

        self.is_busy = True
        self.convert_button.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("Preparing your PDF…")

        def worker() -> None:
            try:
                notes = convert_markdown_to_pdf(
                    source=source,
                    output_path=output_path,
                    title=title,
                    enable_mermaid=enable_mermaid,
                    clean_artifacts=clean_artifacts,
                    base_uri=base_uri,
                    status_callback=lambda text: self.events.put(("status", text)),
                )
                self.events.put(("success", str(output_path), notes))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, name="PDF-Conversion", daemon=True).start()

    def _process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                event_type = event[0]

                if event_type == "status":
                    self.status_var.set(event[1])
                elif event_type == "success":
                    self._finish_success(event[1], event[2])
                elif event_type == "error":
                    self._finish_error(event[1])
        except queue.Empty:
            pass

        if self.root.winfo_exists():
            self.root.after(100, self._process_events)

    def _finish_success(self, output_path: str, notes: str = "") -> None:
        self.is_busy = False
        self.progress.stop()
        self.convert_button.configure(state="normal")
        suffix = f" ({notes})" if notes else ""
        self.status_var.set(f"PDF created successfully: {output_path}{suffix}")
        messagebox.showinfo(
            "Conversion complete",
            f"Your PDF was created successfully:\n\n{output_path}"
            + (f"\n\nNotes: {notes}" if notes else ""),
            parent=self.root,
        )

    def _finish_error(self, error: str) -> None:
        self.is_busy = False
        self.progress.stop()
        self.convert_button.configure(state="normal")
        self.status_var.set("Conversion failed. See the error message for details.")
        messagebox.showerror(
            "Conversion failed",
            f"{error}\n\nCheck that the Python dependencies and Playwright Chromium are installed.\n"
            "For a packaged application, make sure the ms-playwright folder is bundled.",
            parent=self.root,
        )

    def _on_close(self) -> None:
        if self.is_busy:
            close_anyway = messagebox.askyesno(
                "Conversion in progress",
                "A PDF is currently being created. Close the application anyway?",
                parent=self.root,
            )
            if not close_anyway:
                return
        self.root.destroy()


def main() -> None:
    enable_windows_dpi_awareness()
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
