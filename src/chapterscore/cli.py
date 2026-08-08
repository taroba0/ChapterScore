"""ChapterScore CLI — Typer + Rich."""

from __future__ import annotations

import logging
import sys
from enum import Enum
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from chapterscore import __version__
from chapterscore.config import get_settings, reload_settings
from chapterscore.exceptions import ChapterScoreError
from chapterscore.models import LyricsPreference, Mode, PersonalizationPrefs, TasteStrength

# Keep library log noise out of the polished CLI unless user opts in
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("chapterscore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = typer.Typer(
    name="chapterscore",
    help=(
        "Generate Spotify playlists that match the emotional vibe of a book.\n\n"
        "[bold]Examples:[/bold]\n"
        "  [cyan]chapterscore generate \"The Great Gatsby\"[/cyan]\n"
        "  [cyan]chapterscore generate \"Dune\" -a \"Frank Herbert\" -m chapter "
        "-l instrumental-only[/cyan]\n"
        "  [cyan]chapterscore generate \"1984\" --isbn 9780451524935 --tracks 25[/cyan]\n"
        "  [cyan]chapterscore auth[/cyan]\n"
        "  [cyan]chapterscore doctor[/cyan]"
    ),
    add_completion=True,
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console(stderr=False)
err_console = Console(stderr=True)


class ModeOpt(str, Enum):
    overall = "overall"
    chapter = "chapter"


class LyricsOpt(str, Enum):
    allow_lyrics = "allow-lyrics"
    prefer_instrumental = "prefer-instrumental"
    instrumental_only = "instrumental-only"
    # Legacy aliases
    yes = "yes"
    no = "no"


class TasteOpt(str, Enum):
    disable = "disable"
    top5 = "top5"
    top10 = "top10"
    top15 = "top15"


def _banner() -> None:
    title = Text()
    title.append("Chapter", style="bold cyan")
    title.append("Score", style="bold magenta")
    title.append(f"  v{__version__}", style="dim")
    console.print(
        Panel(
            Text.assemble(
                title,
                "\n",
                Text("Soundtracks for the books you love", style="italic dim"),
            ),
            border_style="cyan",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )


def _print_error(exc: BaseException) -> None:
    err_console.print(
        Panel(str(exc), title="[red]Error[/red]", border_style="red", box=box.ROUNDED)
    )


def _mode(m: ModeOpt) -> Mode:
    return Mode.CHAPTER if m == ModeOpt.chapter else Mode.OVERALL


def _lyrics(l: LyricsOpt) -> LyricsPreference:
    return {
        LyricsOpt.allow_lyrics: LyricsPreference.ALLOW_LYRICS,
        LyricsOpt.prefer_instrumental: LyricsPreference.PREFER_INSTRUMENTAL,
        LyricsOpt.instrumental_only: LyricsPreference.INSTRUMENTAL_ONLY,
        LyricsOpt.yes: LyricsPreference.ALLOW_LYRICS,
        LyricsOpt.no: LyricsPreference.ALLOW_LYRICS,
    }[l].normalized()


def _taste(t: TasteOpt) -> TasteStrength:
    return {
        TasteOpt.disable: TasteStrength.DISABLE,
        TasteOpt.top5: TasteStrength.TOP_5,
        TasteOpt.top10: TasteStrength.TOP_10,
        TasteOpt.top15: TasteStrength.TOP_15,
    }[t]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"ChapterScore {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Generate Spotify playlists matching a book's emotional vibe."""
    return


@app.command("generate")
def generate_cmd(
    title: str = typer.Argument(..., help="Book title."),
    author: Optional[str] = typer.Option(
        None, "--author", "-a", help="Author name (improves match)."
    ),
    isbn: Optional[str] = typer.Option(None, "--isbn", "-i", help="ISBN-10 or ISBN-13."),
    mode: ModeOpt = typer.Option(
        ModeOpt.overall,
        "--mode",
        "-m",
        help="overall = one cohesive playlist; chapter = ordered by chapter arcs.",
    ),
    lyrics: LyricsOpt = typer.Option(
        LyricsOpt.allow_lyrics,
        "--lyrics",
        "-l",
        help=(
            "allow-lyrics | prefer-instrumental | instrumental-only. "
            "Instrumental-only is a hard filter and disables Top Artists."
        ),
    ),
    tracks: Optional[int] = typer.Option(
        None,
        "--tracks",
        "-n",
        help="Track count for overall mode (default: 20).",
        min=5,
        max=100,
    ),
    tracks_per_chapter: Optional[int] = typer.Option(
        None,
        "--tracks-per-chapter",
        help="Tracks per chapter in chapter mode (default: 3).",
        min=1,
        max=15,
    ),
    min_tracks: Optional[int] = typer.Option(
        None,
        "--min-tracks",
        help="Minimum tracks to aim for (default: 12). Triggers broader fallback if short.",
        min=1,
        max=100,
    ),
    min_hours: Optional[float] = typer.Option(
        None,
        "--min-hours",
        help="Target playlist length in hours (default: 1.5 ≈ 90 min). 0 disables.",
        min=0.0,
        max=6.0,
    ),
    public: bool = typer.Option(
        False,
        "--public",
        help="Make the playlist public (default: private — safer in Spotify Development mode).",
    ),
    name: Optional[str] = typer.Option(None, "--name", help="Custom playlist name."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fetch book + analyze vibe only; do not touch Spotify.",
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass book/analysis cache."),
    taste: TasteOpt = typer.Option(
        TasteOpt.top10,
        "--taste",
        "-t",
        help=(
            "Personal taste from your Spotify Top Artists: "
            "disable | top5 | top10 | top15 (default: top10)."
        ),
    ),
    recommendations: bool = typer.Option(
        True,
        "--recommendations/--no-recommendations",
        help="Use Spotify Recommendations API seeded with your taste + book vibe (default: on).",
    ),
    exploration: int = typer.Option(
        40,
        "--exploration",
        "-e",
        help="0=max comfort (familiar artists) … 100=max exploration (new artists). Default: 40.",
        min=0,
        max=100,
    ),
) -> None:
    """Generate a Spotify playlist for a book."""
    from chapterscore.pipeline import generate_playlist

    _banner()
    settings = get_settings()

    missing = settings.missing_required(need_spotify=not dry_run, need_xai=True)
    if missing:
        _print_error(
            ChapterScoreError(
                f"Missing configuration: {', '.join(missing)}",
                hint=(
                    "Copy .env.example → .env and fill in credentials. "
                    "Run `chapterscore doctor` for help."
                ),
            )
        )
        raise typer.Exit(2)

    lyrics_pref = _lyrics(lyrics)
    taste_val = _taste(taste)
    if lyrics_pref.is_instrumental_only and taste_val != TasteStrength.DISABLE:
        console.print(
            "[yellow]Note:[/yellow] Top Artists is disabled in Instrumental only mode "
            "(most top artists contain vocals)."
        )
        taste_val = TasteStrength.DISABLE

    prefs = PersonalizationPrefs(
        taste_strength=taste_val,
        use_recommendations=recommendations,
        exploration=exploration,
    )

    console.print(
        f"[bold]Book:[/bold] {title}"
        + (f"  [dim]by {author}[/dim]" if author else "")
        + (f"  [dim]ISBN {isbn}[/dim]" if isbn else "")
    )
    console.print(
        f"[bold]Mode:[/bold] {mode.value}   "
        f"[bold]Lyrics:[/bold] {lyrics_pref.display_label}"
        + ("   [yellow]DRY RUN[/yellow]" if dry_run else "")
    )
    console.print(
        f"[bold]Taste:[/bold] {prefs.taste_strength.value}   "
        f"[bold]Recommendations:[/bold] {'on' if prefs.use_recommendations else 'off'}   "
        f"[bold]Exploration:[/bold] {prefs.exploration}"
    )
    console.print()

    def progress(msg: str) -> None:
        console.print(f"  [cyan]▸[/cyan] {msg}")

    try:
        with Progress(
            SpinnerColumn(style="magenta"),
            TextColumn("[progress.description]{task.description}"),
            console=err_console,
            transient=True,
        ) as spinner:
            task = spinner.add_task("Working…", total=None)

            def progress_spin(msg: str) -> None:
                spinner.update(task, description=msg[:80])
                progress(msg)

            result = generate_playlist(
                title,
                author=author,
                isbn=isbn,
                mode=_mode(mode),
                lyrics=lyrics_pref,
                tracks=tracks,
                tracks_per_chapter=tracks_per_chapter,
                min_tracks=min_tracks,
                min_hours=min_hours,
                public=public,
                playlist_name=name,
                dry_run=dry_run,
                use_cache=not no_cache,
                progress=progress_spin,
                personalization=prefs,
            )
    except ChapterScoreError as exc:
        _print_error(exc)
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Cancelled.[/yellow]")
        raise typer.Exit(130) from None
    except Exception as exc:
        _print_error(ChapterScoreError(f"Unexpected error: {exc}"))
        raise typer.Exit(1) from exc

    book = result.book
    analysis = result.analysis

    meta = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    meta.add_column(style="bold cyan", min_width=12)
    meta.add_column()
    meta.add_row("Title", book.title)
    meta.add_row("Author", book.author_str)
    if book.publish_year:
        meta.add_row("Year", str(book.publish_year))
    if book.subjects:
        meta.add_row("Subjects", ", ".join(book.subjects[:6]))
    meta.add_row("Sources", book.source)
    meta.add_row("Mood", analysis.overall_mood)
    meta.add_row("Energy", f"{analysis.overall_energy:.2f}")
    meta.add_row("Atmospheres", ", ".join(analysis.atmospheres[:8]) or "—")
    meta.add_row("Arc", analysis.emotional_arc or "—")
    console.print()
    console.print(Panel(meta, title="Book & Vibe", border_style="cyan", box=box.ROUNDED))

    if analysis.chapters and mode == ModeOpt.chapter:
        ch_table = Table(
            title="Chapter arcs",
            box=box.SIMPLE_HEAD,
            show_lines=False,
            header_style="bold magenta",
        )
        ch_table.add_column("#", style="dim", width=4)
        ch_table.add_column("Mood", min_width=12)
        ch_table.add_column("Energy", justify="right", width=6)
        ch_table.add_column("Vibe note")
        for ch in analysis.chapters[:20]:
            ch_table.add_row(
                str(ch.chapter_number),
                ch.mood,
                f"{ch.energy_level:.2f}",
                (ch.vibe_note or "")[:70],
            )
        console.print(ch_table)

    if dry_run:
        q_table = Table(
            title="Spotify search queries", box=box.SIMPLE_HEAD, header_style="bold"
        )
        q_table.add_column("Query")
        q_table.add_column("Energy", justify="right", width=6)
        q_table.add_column("Reason")
        queries = list(analysis.overall_search_queries)
        if not queries:
            for ch in analysis.chapters[:5]:
                queries.extend(ch.search_queries[:2])
        for sq in queries[:12]:
            q_table.add_row(
                sq.query,
                f"{sq.energy:.2f}" if sq.energy is not None else "—",
                (sq.reason or "")[:50],
            )
        console.print(q_table)
        console.print(
            Panel(
                f"[green]Dry run complete.[/green] Playlist title would be:\n"
                f"[bold]{analysis.playlist_title_suggestion}[/bold]\n\n"
                f"{analysis.playlist_description}",
                border_style="green",
                box=box.ROUNDED,
            )
        )
        return

    if result.tracks:
        t_table = Table(
            title=f"Selected tracks ({len(result.tracks)})",
            box=box.SIMPLE_HEAD,
            header_style="bold green",
        )
        t_table.add_column("#", style="dim", width=4, justify="right")
        t_table.add_column("Track")
        t_table.add_column("Artist")
        t_table.add_column("Pop", justify="right", width=4)
        t_table.add_column("Score", justify="right", width=6)
        if mode == ModeOpt.chapter:
            t_table.add_column("Ch", width=4, justify="right")
        for i, tr in enumerate(result.tracks, 1):
            row = [
                str(i),
                tr.name[:40],
                tr.artist_str[:28],
                str(tr.popularity),
                f"{tr.score:.1f}",
            ]
            if mode == ModeOpt.chapter:
                row.append(
                    str(tr.chapter_number) if tr.chapter_number is not None else "—"
                )
            t_table.add_row(*row)
        console.print(t_table)

    if result.playlist:
        console.print()
        console.print(
            Panel(
                f"[bold green]Playlist created[/bold green]\n\n"
                f"[bold]{result.playlist.name}[/bold]\n"
                f"{result.playlist.track_count} tracks\n\n"
                f"[link={result.playlist.url}]{result.playlist.url}[/link]",
                border_style="green",
                box=box.ROUNDED,
                title="Spotify",
            )
        )


@app.command("auth")
def auth_cmd(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Delete cached token and force a completely fresh browser login (re-request scopes).",
    ),
) -> None:
    """Log in to Spotify (browser OAuth) and cache the token."""
    from chapterscore.spotify.auth import (
        REQUIRED_SCOPES,
        auth_status,
        get_spotify,
        has_playlist_permission,
        missing_scopes,
        required_scope_string,
        token_scopes,
    )

    _banner()
    settings = get_settings()
    missing = settings.missing_required(need_spotify=True, need_xai=False)
    if missing:
        _print_error(
            ChapterScoreError(
                f"Missing: {', '.join(missing)}",
                hint="Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env",
            )
        )
        raise typer.Exit(2)

    if force:
        console.print("[yellow]Forcing fresh OAuth[/yellow] — old token will be deleted.")
    console.print("Opening browser for Spotify authorization…")
    console.print(f"[dim]Redirect URI:[/dim] {settings.spotify_redirect_uri}")
    console.print(f"[dim]Requesting scopes:[/dim] {required_scope_string()}")
    console.print(
        "[dim]Tip: In Development mode, your account must be on the app allowlist.[/dim]"
    )
    try:
        sp = get_spotify(force_reauth=force)
        me = sp.current_user()
        granted = token_scopes()
        missing_s = missing_scopes(granted)
        can_playlist = has_playlist_permission(granted)

        scope_lines = []
        for s in REQUIRED_SCOPES:
            mark = "[green]✓[/green]" if s in granted else "[red]✗[/red]"
            scope_lines.append(f"  {mark} {s}")
        extra = [s for s in granted if s not in REQUIRED_SCOPES]
        if extra:
            scope_lines.append(f"  [dim]+ extra: {', '.join(extra)}[/dim]")

        body = (
            f"[green]Authenticated as[/green] "
            f"[bold]{me.get('display_name') or me.get('id')}[/bold]\n"
            f"User ID: [cyan]{me.get('id')}[/cyan]\n"
            f"Email: {me.get('email') or '— (scope user-read-email may be missing)'}\n"
            f"Token cache: {auth_status()['token_path']}\n\n"
            f"[bold]Granted scopes[/bold]\n" + "\n".join(scope_lines) + "\n\n"
            f"Playlist permission: "
            + (
                "[green]YES[/green] (playlist-modify-*)"
                if can_playlist
                else "[red]NO[/red] — run auth --force again"
            )
        )
        if missing_s:
            body += f"\n[yellow]Missing scopes:[/yellow] {', '.join(missing_s)}"
            body += "\n→ Run [cyan]chapterscore auth --force[/cyan] and accept all permissions."

        console.print(Panel(body, border_style="green" if can_playlist else "yellow", box=box.ROUNDED))
        if not can_playlist:
            raise typer.Exit(1)
    except ChapterScoreError as exc:
        _print_error(exc)
        raise typer.Exit(1) from exc


@app.command("logout")
def logout_cmd() -> None:
    """Remove the cached Spotify token."""
    from chapterscore.spotify.auth import logout

    if logout():
        console.print("[green]Spotify token removed.[/green]")
        console.print("Next: [cyan]chapterscore auth --force[/cyan]")
    else:
        console.print("[dim]No cached token found.[/dim]")


@app.command("whoami")
def whoami_cmd() -> None:
    """Show the logged-in Spotify user, granted scopes, and playlist permission."""
    from chapterscore.spotify.auth import diagnose_spotify

    _banner()
    d = diagnose_spotify()
    if d.get("error") and not d.get("api_ok"):
        _print_error(ChapterScoreError(str(d["error"])))
        raise typer.Exit(1)

    granted = d.get("granted_scopes") or []
    missing_s = d.get("missing_scopes") or []
    table = Table(title="Spotify account", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Display name", str(d.get("display_name") or "—"))
    table.add_row("User ID", str(d.get("user_id") or "—"))
    table.add_row("Email", str(d.get("email") or "—"))
    table.add_row("Product", str(d.get("product") or "—"))
    table.add_row("Granted scopes", ", ".join(granted) or "—")
    table.add_row("Missing scopes", ", ".join(missing_s) or "[green]none[/green]")
    table.add_row(
        "Playlist permission",
        "[green]YES[/green]" if d.get("has_playlist_permission") else "[red]NO[/red]",
    )
    table.add_row("Token path", str(d.get("token_path") or "—"))
    console.print(table)
    if not d.get("has_playlist_permission"):
        console.print(
            "\n[yellow]Fix:[/yellow] [cyan]chapterscore logout && chapterscore auth --force[/cyan]"
        )
        raise typer.Exit(1)


@app.command("doctor")
def doctor_cmd() -> None:
    """Check configuration, credentials, Spotify scopes, and connectivity."""
    from chapterscore.spotify.auth import auth_status, diagnose_spotify, required_scope_string

    reload_settings()
    settings = get_settings()
    _banner()

    table = Table(title="Environment", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    def ok(label: str, detail: str = "") -> None:
        table.add_row(label, "[green]OK[/green]", detail)

    def bad(label: str, detail: str = "") -> None:
        table.add_row(label, "[red]MISSING[/red]", detail)

    def warn(label: str, detail: str = "") -> None:
        table.add_row(label, "[yellow]WARN[/yellow]", detail)

    ok("Python", f"{sys.version.split()[0]}")

    if settings.spotify_client_id:
        ok("SPOTIFY_CLIENT_ID", f"{settings.spotify_client_id[:8]}…")
    else:
        bad("SPOTIFY_CLIENT_ID", "https://developer.spotify.com/dashboard")

    if settings.spotify_client_secret:
        ok("SPOTIFY_CLIENT_SECRET", "••••••••")
    else:
        bad("SPOTIFY_CLIENT_SECRET", "Set in .env")

    ok("SPOTIFY_REDIRECT_URI", settings.spotify_redirect_uri)
    ok("Required scopes", required_scope_string())

    st = auth_status()
    if st["token_cached"] and not st["token_expired"]:
        ok("Spotify token", st["token_path"])
    elif st["token_cached"] and st["token_expired"]:
        warn("Spotify token", "Expired — will refresh on next use")
    else:
        warn("Spotify token", "Not cached — run `chapterscore auth --force`")

    if st.get("granted_scopes"):
        if st.get("has_playlist_permission"):
            ok("Playlist scopes", ", ".join(st["granted_scopes"]))
        else:
            bad("Playlist scopes", f"granted={st['granted_scopes']} missing={st.get('missing_scopes')}")
    else:
        warn("Playlist scopes", "Unknown until auth")

    if settings.xai_api_key:
        ok("XAI_API_KEY", f"{settings.xai_api_key[:6]}…")
        ok("XAI model", settings.xai_model)
    else:
        bad("XAI_API_KEY", "https://console.x.ai")

    if settings.google_books_api_key:
        ok("GOOGLE_BOOKS_API_KEY", "set (optional)")
    else:
        table.add_row("GOOGLE_BOOKS_API_KEY", "[dim]optional[/dim]", "Higher rate limits")

    ok("Cache dir", str(settings.cache_dir))
    ok("Data dir", str(settings.data_dir))

    console.print(table)
    console.print()

    # Live Spotify user diagnostic
    console.print("[bold]Spotify account diagnostic[/bold]")
    diag = diagnose_spotify()
    if diag.get("api_ok"):
        console.print(f"  [green]✓[/green] User: [bold]{diag.get('display_name')}[/bold]  id=[cyan]{diag.get('user_id')}[/cyan]")
        console.print(f"  Email: {diag.get('email') or '—'}")
        console.print(f"  Granted scopes: {', '.join(diag.get('granted_scopes') or []) or '—'}")
        if diag.get("has_playlist_permission"):
            console.print("  Playlist permission: [green]YES[/green]")
        else:
            console.print("  Playlist permission: [red]NO[/red]")
            console.print("  → [cyan]chapterscore logout && chapterscore auth --force[/cyan]")
        if diag.get("missing_scopes"):
            console.print(f"  [yellow]Missing:[/yellow] {', '.join(diag['missing_scopes'])}")
    else:
        console.print(f"  [yellow]![/yellow] {diag.get('error') or 'Not logged in'}")
        console.print("  → [cyan]chapterscore auth --force[/cyan]")

    console.print()
    try:
        from chapterscore.books.http import create_client

        with create_client(timeout=12) as client:
            r = client.get(
                "https://openlibrary.org/search.json",
                params={"title": "Dune", "limit": 1},
            )
            if r.status_code == 200:
                console.print("[green]✓[/green] Open Library reachable")
            else:
                console.print(f"[yellow]![/yellow] Open Library status {r.status_code}")

            r2 = client.get(
                "https://en.wikipedia.org/api/rest_v1/page/summary/Dune_(novel)",
            )
            if r2.status_code == 200:
                console.print("[green]✓[/green] Wikipedia reachable")
            else:
                console.print(f"[yellow]![/yellow] Wikipedia status {r2.status_code}")
    except Exception as exc:
        console.print(f"[red]✗[/red] Connectivity probe failed: {exc}")

    if settings.xai_api_key:
        console.print(
            f"[green]✓[/green] xAI client configured "
            f"(model [bold]{settings.xai_model}[/bold])"
        )

    console.print()
    console.print(
        Panel(
            "[bold]Setup checklist[/bold]\n\n"
            "1. Copy [cyan].env.example[/cyan] → [cyan].env[/cyan]\n"
            "2. Create a Spotify app → https://developer.spotify.com/dashboard\n"
            "   Add redirect URI: [cyan]http://127.0.0.1:8888/callback[/cyan]\n"
            "3. Get an xAI key → https://console.x.ai\n"
            "4. Run [cyan]chapterscore auth --force[/cyan] (accept all scopes)\n"
            "5. Run [cyan]chapterscore generate \"Your Book\" --dry-run[/cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )


@app.command("cache")
def cache_cmd(
    clear: bool = typer.Option(False, "--clear", help="Delete all cached entries."),
) -> None:
    """Show or clear the local analysis cache."""
    from chapterscore.cache import Cache
    from chapterscore.config import get_settings

    settings = get_settings()
    cache = Cache()
    if clear:
        removed = cache.clear()
        entries_dir = settings.cache_dir / "entries"
        if entries_dir.exists():
            for p in entries_dir.glob("*.json"):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        console.print(
            f"[green]Cleared cache[/green] (~{removed} files) at {settings.cache_dir}"
        )
    else:
        entries_dir = settings.cache_dir / "entries"
        entries = list(entries_dir.glob("*.json")) if entries_dir.exists() else []
        console.print(f"Cache directory: [cyan]{settings.cache_dir}[/cyan]")
        console.print(f"Entries: {len(entries)}")
        console.print("Use [cyan]chapterscore cache --clear[/cyan] to wipe.")


@app.command("lookup")
def lookup_cmd(
    title: str = typer.Argument(..., help="Book title."),
    author: Optional[str] = typer.Option(None, "--author", "-a"),
    isbn: Optional[str] = typer.Option(None, "--isbn", "-i"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Look up book metadata without analyzing or creating a playlist."""
    from chapterscore.books import fetch_book

    _banner()
    try:
        book = fetch_book(
            title,
            author=author,
            isbn=isbn,
            use_cache=not no_cache,
            want_chapters=True,
        )
    except ChapterScoreError as exc:
        _print_error(exc)
        raise typer.Exit(1) from exc

    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("Title", book.title)
    table.add_row("Author", book.author_str)
    table.add_row("ISBN", book.isbn or "—")
    table.add_row("Year", str(book.publish_year or "—"))
    table.add_row("Pages", str(book.page_count or "—"))
    table.add_row("Subjects", ", ".join(book.subjects[:10]) or "—")
    table.add_row("Sources", book.source)
    table.add_row("Chapters", str(len(book.chapters)) if book.chapters else "—")
    desc = book.description or ""
    table.add_row("Description", desc[:400] + ("…" if len(desc) > 400 else ""))
    console.print(Panel(table, title="Book metadata", border_style="cyan", box=box.ROUNDED))
    if book.plot_summary and book.plot_summary != book.description:
        console.print(
            Panel(
                book.plot_summary[:1500],
                title="Plot summary",
                border_style="dim",
                box=box.ROUNDED,
            )
        )


if __name__ == "__main__":
    app()
