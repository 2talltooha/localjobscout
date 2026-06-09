"""Interactive Textual TUI for LocalJobScout.

Launch:  python -m localjobscout --tui

Keys
----
s        Trigger a new scan (runs in background, UI stays responsive)
r        Rescore all jobs against current resume.txt
o        Open selected job URL in browser
m        Mark application status (opens a dialog)
c        Generate cover letter for selected job
p        Generate interview prep Q&A for selected job
h        Hide selected job
/        Focus the search/filter bar
Escape   Clear search / close dialog
q        Quit
"""
from __future__ import annotations

import webbrowser
from datetime import datetime
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from localjobscout import db as db_module
from localjobscout import matcher as matcher_module
from localjobscout import resume as resume_module
from localjobscout.config import Settings
from localjobscout.db import APPLICATION_STATUSES, Job

_STATUS_STYLE: dict[str | None, str] = {
    "seen": "dim cyan",
    "interested": "cyan",
    "applied": "green",
    "interviewed": "yellow",
    "offered": "bold green",
    "rejected": "red",
    "hidden": "dim",
    None: "",
}


def _score_text(score: float | None, threshold: float) -> Text:
    if score is None:
        return Text("—", style="dim")
    if score < 0:
        return Text("excl", style="dim")
    if score >= threshold:
        style = "bold green"
    elif score >= threshold * 0.7:
        style = "yellow"
    else:
        style = "dim"
    return Text(f"{score:.2f}", style=style)


class MarkStatusScreen(ModalScreen[str | None]):
    """Modal dialog: pick an application status for a job."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, job: Job) -> None:
        super().__init__()
        self._job = job

    def compose(self) -> ComposeResult:
        with Vertical(id="mark-dialog"):
            yield Label(
                f"[bold]Mark status[/bold]\n[dim]{self._job.title[:55]}[/dim]",
                id="mark-title",
            )
            for status in APPLICATION_STATUSES:
                variant = (
                    "success"
                    if status == self._job.application_status
                    else "default"
                )
                yield Button(
                    status.capitalize(),
                    id=f"s-{status}",
                    variant=variant,  # type: ignore[arg-type]
                )
            yield Button("Clear status", id="s-clear", variant="warning")
            yield Button("Cancel", id="s-cancel", variant="error")

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed)
    def handle_press(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "s-cancel":
            self.dismiss(None)
        else:
            self.dismiss(bid.removeprefix("s-"))


class JobDetailPane(Static):
    """Bottom pane: rich detail view for the selected job."""

    def show_job(self, job: Job | None, threshold: float) -> None:
        if job is None:
            self.update(
                "[dim]Select a job with ↑/↓ · Press [bold]o[/bold] to open "
                "URL · [bold]m[/bold] mark · [bold]c[/bold] cover · "
                "[bold]p[/bold] prep · [bold]h[/bold] hide[/dim]"
            )
            return

        if job.score is not None and job.score >= 0:
            score_str = f"{job.score:.3f}"
            score_style = (
                "green" if job.score >= threshold
                else ("yellow" if job.score >= threshold * 0.7 else "dim")
            )
        else:
            score_str = "excluded" if (job.score or 0) < 0 else "unscored"
            score_style = "dim"

        desc = job.description.replace("\n", " ").strip()
        if len(desc) > 600:
            desc = desc[:600] + "…"

        # Build via Text to avoid markup-parsing issues with URLs / special chars
        t = Text()
        t.append(job.title, style="bold")
        t.append("\n")
        t.append(job.company or "—", style="dim")
        t.append("  ·  ")
        t.append(job.location or "—")
        t.append("  ·  ")
        t.append(job.source, style="cyan")
        if job.application_status:
            color = _STATUS_STYLE.get(job.application_status, "")
            t.append("  ·  Status: ")
            t.append(job.application_status, style=color)
        t.append("\nScore: ")
        t.append(score_str, style=score_style)
        t.append("\n")
        url_text = Text(job.url[:100], style="blue underline")
        url_text.stylize(f"link {job.url}")
        t.append_text(url_text)
        t.append("\n\n")
        t.append(desc, style="dim")
        self.update(t)


class JobScoutApp(App[None]):
    TITLE = "LocalJobScout"
    CSS = """
    Screen { layout: vertical; }

    #search {
        margin: 0 1 0 1;
        border: tall $accent;
    }
    #job-table {
        height: 55%;
        border: solid $accent;
        margin: 1 1 0 1;
    }
    #detail-pane {
        height: 1fr;
        border: solid $surface-lighten-2;
        margin: 1;
        padding: 1 2;
        overflow-y: auto;
    }
    MarkStatusScreen {
        align: center middle;
    }
    #mark-dialog {
        background: $surface;
        border: solid $primary;
        padding: 2 4;
        width: 52;
        height: auto;
    }
    #mark-title {
        margin-bottom: 1;
        text-align: center;
    }
    #mark-dialog Button {
        width: 100%;
        margin-bottom: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [  # type: ignore[assignment]
        Binding("s", "scan", "Scan", priority=True),
        Binding("r", "rescore", "Rescore", priority=True),
        Binding("o", "open_url", "Open URL"),
        Binding("m", "mark", "Mark"),
        Binding("c", "cover", "Cover"),
        Binding("p", "prep", "Prep"),
        Binding("h", "hide", "Hide"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "clear_search", "Clear", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._jobs: list[Job] = []
        self._filtered: list[Job] = []
        self._selected: Job | None = None
        self._last_scan = "never"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Input(
                placeholder="Filter by title / company / source / status  (/ to focus)",
                id="search",
            )
            yield DataTable(id="job-table", cursor_type="row", zebra_stripes=True)
            yield JobDetailPane("", id="detail-pane")
        yield Footer()

    def on_mount(self) -> None:
        db_module.init_db(self._settings.db_path)
        t = self.query_one(DataTable)
        t.add_columns("Score", "✓", "Title", "Company", "Source", "Status")
        self._reload()

    # ── data loading ──────────────────────────────────────────────────────

    def _reload(self) -> None:
        self._jobs = db_module.get_recent_jobs(limit=None)
        self._apply_filter(self.query_one(Input).value)

    def _apply_filter(self, query: str = "") -> None:
        q = query.lower().strip()
        thr = self._settings.match_threshold

        if q:
            self._filtered = [
                j for j in self._jobs
                if q in j.title.lower()
                or q in (j.company or "").lower()
                or q in j.source.lower()
                or q in (j.application_status or "")
            ]
        else:
            self._filtered = [j for j in self._jobs if j.application_status != "hidden"]

        self._filtered.sort(key=lambda j: (j.score or -1), reverse=True)

        table = self.query_one(DataTable)
        table.clear()
        for job in self._filtered:
            st = job.application_status
            table.add_row(
                _score_text(job.score, thr),
                Text("✓", style="green") if (job.score or 0) >= thr else Text(""),
                (job.title[:42] + "…") if len(job.title) > 43 else job.title,
                (job.company or "")[:20],
                job.source,
                Text(st or "—", style=_STATUS_STYLE.get(st, "")),
            )

        above = sum(1 for j in self._jobs if (j.score or 0) >= thr)
        self.sub_title = (
            f"{len(self._filtered)} shown  ·  "
            f"{above}/{len(self._jobs)} matches  ·  "
            f"last scan {self._last_scan}"
        )
        self.query_one(JobDetailPane).show_job(self._selected, thr)

    # ── events ────────────────────────────────────────────────────────────

    @on(DataTable.RowHighlighted)
    def row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._filtered):
            self._selected = self._filtered[idx]
            self.query_one(JobDetailPane).show_job(
                self._selected, self._settings.match_threshold
            )

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    # ── simple actions ────────────────────────────────────────────────────

    def action_focus_search(self) -> None:
        self.query_one(Input).focus()

    def action_clear_search(self) -> None:
        self.query_one(Input).value = ""
        self.query_one(DataTable).focus()

    def action_open_url(self) -> None:
        if self._selected:
            webbrowser.open(self._selected.url)
            self.notify(f"Opening {self._selected.url[:60]}…")

    def action_hide(self) -> None:
        if not self._selected:
            return
        db_module.update_application_status(self._selected.id, "hidden")
        self.notify(f"Hidden: {self._selected.title[:45]}")
        self._selected = None
        self._reload()

    # ── async / modal actions ─────────────────────────────────────────────

    async def action_mark(self) -> None:
        if not self._selected:
            return
        result: str | None = await self.push_screen_wait(
            MarkStatusScreen(self._selected)
        )
        if result is None:
            return
        new_status: str | None = None if result == "clear" else result
        db_module.update_application_status(self._selected.id, new_status)
        self.notify(f"Marked '{self._selected.title[:35]}' → {result}")
        self._reload()

    @work(thread=True)
    def action_cover(self) -> None:
        if not self._selected:
            return
        from localjobscout import tracker as tracker_module
        try:
            path, backend = tracker_module.generate_cover_letter(
                self._selected.id, settings=self._settings
            )
            self.call_from_thread(
                self.notify, f"Cover letter saved: {path.name}  [{backend}]"
            )
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.notify, str(exc), severity="error")

    @work(thread=True)
    def action_prep(self) -> None:
        if not self._selected:
            return
        from localjobscout import prep as prep_module
        try:
            path = prep_module.generate_and_save(
                self._selected.id, settings=self._settings
            )
            self.call_from_thread(self.notify, f"Interview prep saved: {path.name}")
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.notify, str(exc), severity="error")

    @work(exclusive=True)
    async def action_scan(self) -> None:
        from localjobscout.export import write_matches_md
        from localjobscout.scheduler import run_scan
        self.notify("Scanning job boards… (takes ~1 min)", timeout=120)
        try:
            result = await run_scan(self._settings)
            self._last_scan = datetime.now().strftime("%H:%M")
            self._reload()
            try:
                write_matches_md(self._settings)
            except Exception:
                pass
            msg = (
                f"Scan done · {result.jobs_new} new  ·  "
                f"{result.jobs_notified} match(es)"
            )
            if result.errors:
                msg += f"  ·  {result.errors} error(s)"
            self.notify(msg, timeout=8)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Scan failed: {exc}", severity="error")

    @work(thread=True)
    def action_rescore(self) -> None:
        self.call_from_thread(self.notify, "Rescoring…", timeout=60)
        try:
            resume_text = resume_module.load_resume(self._settings.resume_path)
            nlp = resume_module.get_nlp()
            job_matcher = matcher_module.build_matcher(self._settings, nlp)
            jobs = db_module.get_all_for_rescore()
            scored = job_matcher.score_jobs(resume_text, jobs)
            for job, score in scored:
                db_module.update_score(job.id, score)
            self.call_from_thread(self._reload)
            self.call_from_thread(
                self.notify, f"Rescored {len(scored)} jobs", timeout=6
            )
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.notify, str(exc), severity="error")


def launch_tui(settings: Settings) -> None:
    """Launch the interactive TUI (blocks until quit)."""
    JobScoutApp(settings).run()
