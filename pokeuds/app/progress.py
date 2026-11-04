"""Turns a run's status events into what the session panel shows: a headline, steps, notes."""

from __future__ import annotations

from dataclasses import dataclass, field

STEPS = ("radio", "console", "link", "trade")
STEP_LABELS = {"radio": "Radio ready", "console": "3DS connected", "link": "Link up", "trade": "Pokemon received"}

PHASES = {
    "room": "At the Pokemon Center: walk to the Trade Center table",
    "wait-block": "Exchanging parties",
    "block": "Exchanging parties",
    "mail-wait": "Exchanging mail",
    "mail-run": "Exchanging mail",
    "mail-body": "Exchanging mail",
    "select/pick": "Choose a Pokemon on the 3DS",
    "select/confirm": "Confirm the trade on the 3DS",
    "select/after-trade": "Trading",
}


def plain_note(text: str) -> str:
    """An engine note in the words the panel uses; empty for the ones only the log needs."""
    if text.startswith("round:"):
        return ""
    if text.startswith("partner offers slot"):
        return "The 3DS offers " + text.split(": ", 1)[-1]
    if text.startswith("partner "):
        name, _, party = text[len("partner "):].partition(": ")
        return f"{name}'s party: {party}"
    if text.startswith("traded "):
        return "Traded " + text[len("traded "):]
    return text[:1].upper() + text[1:]


@dataclass
class Progress:
    state: str = "idle"                  # idle, running, done, stopped, error
    headline: str = "Ready"
    detail: str = ""
    steps: dict[str, bool] = field(default_factory=lambda: {s: False for s in STEPS})
    notes: list[str] = field(default_factory=list)
    received: list[dict] = field(default_factory=list)
    radio: str = ""

    def start(self) -> None:
        self.__init__()
        self.state, self.headline = "running", "Starting"

    def take(self, event: dict) -> None:
        kind = event.get("event")
        if kind == "radio":
            self.steps["radio"] = True
            self.radio = event.get("description", "")
            self.headline = "Radio ready"
        elif kind == "searching":
            self.headline = "Looking for the 3DS"
            self.detail = "Choose Host on the 3DS" if event.get("game") == "vc" else "Open the trade screen on the 3DS"
        elif kind == "hosting":
            self.headline = "Waiting for the 3DS to join"
            self.detail = f"Hosting on channel {event.get('channel')}"
        elif kind in ("joined", "partner-joined"):
            self.steps["console"] = True
            self.headline, self.detail = "3DS connected", ""
        elif kind == "session":
            state = event.get("state")
            if state == "setup":
                self.headline = "Setting up the link"
            elif state in ("joined", "listening"):
                self.steps["link"] = True
                self.headline = "Link up" if state == "joined" else "Connected: recording what the 3DS sends"
            elif state == "closed":
                self.detail = "The 3DS closed the link"
        elif kind == "mode":
            self.detail = {"gen1": "Gen 1 trade", "gen2": "Gen 2 trade", "timecapsule": "Time Capsule"}.get(event.get("mode"), "")
        elif kind == "phase":
            if text := PHASES.get(event.get("phase", "")):
                self.headline = text
        elif kind == "note":
            if text := plain_note(event.get("text", "")):
                self.notes.append(text)
                del self.notes[:-50]
        elif kind == "received":
            self.steps["trade"] = True
            self.received.append(event)
            self.headline = f"Trade {event.get('trade')} complete"
            self.detail = event.get("summary", "")
        elif kind == "stopped":
            reason = event.get("reason")
            self.state = "done" if self.received else "stopped"
            self.headline = {"user": "Stopped", "timeout": "Stopped: nothing answered",
                             "session-ended": "Session ended",
                             "protocol-not-mapped": "Session recorded"}.get(reason, "Stopped")
            if event.get("message"):
                self.detail = event["message"]
        elif kind == "error":
            self.state = "error"
            self.headline = "Error"
            self.detail = event.get("message", "")

    def exited(self, code: int) -> None:
        if self.state == "running":
            self.state = "done" if code == 0 and self.received else ("stopped" if code in (0, 130) else "error")
            if self.state == "error" and not self.detail:
                self.headline, self.detail = "Error", f"the run ended with code {code}"
            elif self.state != "error" and self.headline == "Starting":
                self.headline = "Stopped"
