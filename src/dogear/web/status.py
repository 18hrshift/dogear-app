"""A book's state in words: the page title, the heading, and the status sentence that a
screen reader announces when it changes. Pages and the progress updates share these."""

from __future__ import annotations

from dataclasses import dataclass

from dogear.web.books import BookRecord


@dataclass(frozen=True)
class Status:
    title: str  # the start of the page title, such as "Making"
    heading: str
    sentence: str  # for the role="status" region


def describe(record: BookRecord, ahead: int | None, latest: str = "") -> Status:
    """`ahead`: how many books will be made before this one, if it is waiting. `latest`:
    the latest progress line of the book being read or made (progress.Progress.latest),
    such as how far reading a scanned PDF with OCR has got."""
    match record.state:
        case "reading":
            return Status(
                "Reading",
                f"Reading {record.title}",
                latest
                or "Reading the book to find its sections. This usually takes less than a minute.",
            )
        case "unreadable":
            return Status(
                "Couldn't read",
                "Dogear couldn't read this book",
                "Dogear couldn't read this book.",
            )
        case "read_stopped":
            return Status(
                "Stopped",
                "Reading this book stopped",
                "Stopped. Choose Read again to carry on.",
            )
        case "ready":
            return Status(
                "Choose what to make",
                "Choose what to make",
                "Dogear has read the book. Next, choose what to make.",
            )
        case "waiting":
            if not ahead:
                sentence = "Waiting: it starts in a moment."
            else:
                books = "1 book" if ahead == 1 else f"{ahead} books"
                sentence = f"Waiting: {books} ahead of this one."
            return Status("Waiting", record.title, sentence)
        case "making":
            return Status("Making", record.title, f"Making: {latest or 'Starting.'}")
        case "done":
            return Status("Ready", "Your talking book is ready", "Your talking book is ready.")
        case "failed":
            return Status(
                "Couldn't make",
                "Dogear couldn't make this book",
                "Dogear couldn't make this book.",
            )
        case "stopped":
            return Status(
                "Stopped",
                "Making this book stopped",
                "Stopped. Choose Start again to carry on. What was already spoken is kept.",
            )
    raise ValueError(f"unknown state: {record.state}")
