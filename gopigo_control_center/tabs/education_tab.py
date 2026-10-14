"""Modules tab -- read course modules (lab handouts, exercises, assignments) inside the app.

Each module document is a Markdown file in the lessons/ folder; drop in another .md file and it shows up
in the picker, in module order (module9 before module10). Only a small subset of Markdown is understood: # / ## / ### headings, "- " bullets
(indent four spaces to nest), "1. " numbered steps, ``` fenced code blocks and `inline code`.
Web addresses become clickable links.
"""

import os
import re
import tkinter as tk
import webbrowser
from tkinter import ttk

from theme import (
    BORDER, CARD_BG, FONT_BASE, FONT_BOLD, FONT_FAMILY, FONT_MONO_BASE, MUTED, PRIMARY, TEXT,
)

LESSONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lessons")

_NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$")
_URL = re.compile(r"https?://[^\s)]+")
_INDENT = 22  # px per list level


def load_lessons():
    """(title, path) for every lesson file, sorted by file name. The title is the file's first '# ' line."""
    lessons = []
    if os.path.isdir(LESSONS_DIR):
        # Natural order, so module10 comes after module9 rather than after module1.
        for name in sorted(os.listdir(LESSONS_DIR), key=lambda n: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", n)]):
            if not name.endswith(".md"):
                continue
            path = os.path.join(LESSONS_DIR, name)
            title = os.path.splitext(name)[0]
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
            lessons.append((title, path))
    return lessons


class EducationTabMixin:
    def _build_education_tab(self):
        self.lessons = load_lessons()
        self.lesson_section_marks = []

        outer = ttk.Frame(self.education_tab, style="Card.TFrame", padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Module:", style="Card.TLabel").pack(side="left", padx=(0, 6))
        self.lesson_var = tk.StringVar()
        picker = ttk.Combobox(
            top, textvariable=self.lesson_var, state="readonly", width=60,
            values=[title for title, _ in self.lessons],
        )
        picker.pack(side="left")
        picker.bind("<<ComboboxSelected>>", lambda e: self._show_lesson(picker.current()))

        body = ttk.Frame(outer, style="Card.TFrame")
        body.pack(fill="both", expand=True)

        nav = ttk.Frame(body, style="Card.TFrame")
        nav.pack(side="left", fill="y", padx=(0, 10))
        ttk.Label(nav, text="Sections", style="CardMuted.TLabel").pack(anchor="w")
        self.lesson_nav = tk.Listbox(
            nav, width=28, font=FONT_BASE, bg=CARD_BG, fg=TEXT, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, selectbackground=PRIMARY, selectforeground="#ffffff", activestyle="none",
            exportselection=False,
        )
        self.lesson_nav.pack(fill="y", expand=True)
        self.lesson_nav.bind("<<ListboxSelect>>", lambda e: self._jump_to_section())

        text_frame = ttk.Frame(body, style="Card.TFrame")
        text_frame.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.lesson_text = tk.Text(
            text_frame, wrap="word", font=FONT_BASE, bg=CARD_BG, fg=TEXT, relief="flat", padx=16, pady=12,
            highlightthickness=1, highlightbackground=BORDER, cursor="arrow", yscrollcommand=scrollbar.set,
        )
        self.lesson_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.lesson_text.yview)
        self._configure_lesson_tags()

        if self.lessons:
            picker.current(0)
            self._show_lesson(0)
        else:
            self._render_lesson(f"# No modules found\n\nAdd Markdown (.md) files to:\n\n```\n{LESSONS_DIR}\n```\n")

    def _configure_lesson_tags(self):
        t = self.lesson_text
        t.tag_configure("title", font=(FONT_FAMILY, 17, "bold"), spacing3=10)
        t.tag_configure("h2", font=(FONT_FAMILY, 14, "bold"), foreground=PRIMARY, spacing1=14, spacing3=6)
        t.tag_configure("h3", font=(FONT_FAMILY, 11, "bold"), spacing1=8, spacing3=4)
        t.tag_configure("para", spacing3=6)
        t.tag_configure("code", font=FONT_MONO_BASE, background="#eef1f5")
        t.tag_configure("codeblock", font=FONT_MONO_BASE, background="#eef1f5", lmargin1=_INDENT, lmargin2=_INDENT)
        t.tag_configure("link", foreground=PRIMARY, underline=True)
        t.tag_bind("link", "<Enter>", lambda e: t.config(cursor="hand2"))
        t.tag_bind("link", "<Leave>", lambda e: t.config(cursor="arrow"))
        t.tag_configure("muted", foreground=MUTED)
        for level in (1, 2):
            # A hanging indent, so wrapped lines line up with the item's text rather than its bullet.
            t.tag_configure(
                f"list{level}", lmargin1=_INDENT * (level - 1) + 6, lmargin2=_INDENT * level + 6, spacing3=3
            )

    def _show_lesson(self, index):
        if index < 0:
            return
        with open(self.lessons[index][1], encoding="utf-8") as f:
            self._render_lesson(f.read())

    def _render_lesson(self, markdown):
        t = self.lesson_text
        t.config(state="normal")
        t.delete("1.0", "end")
        self.lesson_nav.delete(0, "end")
        self.lesson_section_marks = []
        self._lesson_link_count = 0

        in_code = False
        for line in markdown.splitlines():
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                t.insert("end", line + "\n", "codeblock")
            elif not line.strip():
                continue  # spacing comes from the tags, so blank lines aren't needed
            elif line.startswith("# "):
                t.insert("end", line[2:].strip() + "\n", "title")
            elif line.startswith("## "):
                heading = line[3:].strip()
                mark = f"section{len(self.lesson_section_marks)}"
                t.mark_set(mark, "end-1c")
                t.mark_gravity(mark, "left")
                self.lesson_section_marks.append(mark)
                self.lesson_nav.insert("end", heading)
                t.insert("end", heading + "\n", "h2")
            elif line.startswith("### "):
                t.insert("end", line[4:].strip() + "\n", "h3")
            else:
                level = 2 if line.startswith("    ") else 1
                item = line.strip()
                numbered = _NUMBERED.match(item)
                if item.startswith("- "):
                    t.insert("end", "•  ", f"list{level}")
                    self._insert_inline(item[2:], f"list{level}")
                elif numbered:
                    t.insert("end", f"{numbered.group(1)}.  ", f"list{level}")
                    self._insert_inline(numbered.group(2), f"list{level}")
                else:
                    self._insert_inline(item, "para")
                t.insert("end", "\n")

        t.config(state="disabled")
        t.yview_moveto(0)

    def _insert_inline(self, text, block_tag):
        """Insert one line, turning `backticks` into code and web addresses into clickable links."""
        t = self.lesson_text
        for i, part in enumerate(text.split("`")):
            if i % 2:
                t.insert("end", part, (block_tag, "code"))
                continue
            pos = 0
            for match in _URL.finditer(part):
                t.insert("end", part[pos:match.start()], block_tag)
                link_tag = f"link{self._lesson_link_count}"
                self._lesson_link_count += 1
                t.insert("end", match.group(), (block_tag, "link", link_tag))
                t.tag_bind(link_tag, "<Button-1>", lambda e, url=match.group(): webbrowser.open(url))
                pos = match.end()
            t.insert("end", part[pos:], block_tag)

    def _jump_to_section(self):
        selection = self.lesson_nav.curselection()
        if selection:
            self.lesson_text.yview(self.lesson_section_marks[selection[0]])
