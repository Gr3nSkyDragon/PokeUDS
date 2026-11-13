"""The connection test and NAS login the DS games make before a Mystery Gift (docs/ds_wifi.md, "NAS").

``conntest.nintendowifi.net`` is a GET the WFC settings screen and the game use to check the connection; it is
answered ``ok``. ``nas.nintendowifi.net/ac`` is a POST with an ``action``: ``acctcreate`` makes an account, ``login``
answers a challenge and a token, and ``svcloc`` says where the download service is. ``/pr`` is the profanity filter,
which accepts everything.

Every field on the wire is Nintendo's flavour of base64: ``+`` is written ``.``, ``/`` is ``-``, and ``=`` is ``*``
(the same alphabet the DLS1 file lists use for their descriptions). The values are percent-encoded as a form. The
reply is ``returncd`` first and ``datetime`` second, then the action's fields; it is HTTP/1.1, because the DWC
service-location client refuses HTTP/1.0 as bad data (error 25101).

Values not yet seen on a DS are marked in docs/ds_wifi.md. Gen 4 uses the long-standing AltWFC answers (``returncd``
007, ``token`` for service 9000); Gen 5 uses the ones Entralinked is known to work with (``returncd`` 001,
``servicetoken``), chosen by the game code the console sends.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets
import string
import threading
import time
import urllib.parse
from dataclasses import dataclass

from .http import Request, Response

log = logging.getLogger(__name__)
ALNUM = string.ascii_letters + string.digits
NAS_HEADERS = {"NODE": "wifiappe1", "Server": "Nintendo Wii (http)", "Content-Type": "text/plain"}
CONNTEST_BODY = b"ok"


# -- Nintendo's base64 ---------------------------------------------------------------------------------------------

def _pad(text: str) -> str:
    return text + "=" * (-len(text) % 4)


def decode_value(value: str) -> str:
    """One NAS form value: percent-encoding undone, Nintendo's base64 alphabet read, Latin-1 text.

    Both the alphabet this decoder writes (``.``/``-``/``*``) and the variants older AltWFC servers accept (``>``
    for ``+``, ``?`` or ``-`` for ``/``, ``*`` for ``=``) are understood, so a capture from either works.
    """
    text = urllib.parse.unquote(value)
    text = text.replace("*", "=").replace(">", "+").replace("?", "/").replace("-", "/").replace(".", "+")
    try:
        raw = base64.b64decode(_pad(text), validate=False)
    except (binascii.Error, ValueError):
        return urllib.parse.unquote(value)
    try:
        return raw.decode("latin-1")
    except UnicodeDecodeError:                    # pragma: no cover - latin-1 decodes every byte
        return urllib.parse.unquote(value)


def encode_value(value: str) -> str:
    """``value`` in Nintendo's base64: ``+`` -> ``.``, ``/`` -> ``-``, ``=`` -> ``*``; no percent-encoding."""
    text = base64.b64encode(str(value).encode("latin-1")).decode("ascii")
    return text.replace("+", ".").replace("/", "-").replace("=", "*")


def decode_form(body: bytes) -> dict:
    """``a=...&b=...`` as decoded single values (blank values kept)."""
    out: dict[str, str] = {}
    for part in body.decode("latin-1").strip().split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        out[key] = decode_value(value)
    return out


def encode_form(values: dict) -> bytes:
    """The NAS reply body: keys as given, values in Nintendo's base64, joined by ``&``, no trailing newline."""
    return "&".join(f"{key}={encode_value(value)}" for key, value in values.items()).encode("ascii")


# -- sessions ------------------------------------------------------------------------------------------------------

@dataclass
class Session:
    userid: str
    gamecd: str
    devtime: str
    name: str
    language: str = ""


class Sessions:
    """The tokens the NAS hands out, so DLS1 can find the login's console date (which picks a Gen 4 gift)."""

    def __init__(self) -> None:
        self._by_token: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._next_userid = 2                 # AltWFC's first is 0000000000002 (13 digits, as the NAS sends)

    def next_userid(self) -> str:
        with self._lock:
            userid = f"{self._next_userid:013d}"
            self._next_userid += 1
            return userid

    def new(self, post: dict, userid: str = "") -> str:
        # The real NAS token is 83 bytes: "NDS" and 80 random characters (dwc_network_server_emulator matches it).
        token = "NDS" + "".join(secrets.choice(ALNUM) for _ in range(80))
        # svcloc only repeats the login's token, not every field; keep the date and game the login knew.
        previous = self.get(post.get("token", "")) if post.get("token") else None
        session = Session(
            userid or post.get("userid", "") or (previous.userid if previous else ""),
            post.get("gamecd", "") or post.get("gsbrcd", "") or (previous.gamecd if previous else ""),
            post.get("devtime", "") or (previous.devtime if previous else ""),
            post.get("ingamesn", "") or post.get("devname", "") or (previous.name if previous else ""),
            post.get("language", "") or (previous.language if previous else ""))
        with self._lock:
            self._by_token[token] = session
        return token

    def get(self, token: str) -> Session | None:
        with self._lock:
            return self._by_token.get(token)


# -- the server ----------------------------------------------------------------------------------------------------

class NAS:
    def __init__(self, sessions: Sessions, dls_host: str = "dls1.nintendowifi.net") -> None:
        self.sessions = sessions
        self.dls_host = dls_host

    def handle(self, request: Request) -> Response:
        if request.method != "POST":
            # conntest: the WFC settings connection test, and the game's own check.
            return Response(200, CONNTEST_BODY, {"Content-Type": "text/html", "X-Organization": "Nintendo",
                                                 "Server": "BigIP"}, version="1.0")
        post = decode_form(request.body)
        post["ipaddr"] = request.client
        if request.path == "/pr":
            reply = self.profanity(post)
        elif request.path == "/ac":
            action = post.get("action", "").lower()
            reply = {"acctcreate": self.acctcreate, "login": self.login, "svcloc": self.svcloc}.get(action,
                                                                                                      self.unknown)(post)
        else:
            log.warning("NAS: unknown path %s", request.path)
            return Response(404, b"", NAS_HEADERS, version="1.1")
        # Gen 4 (AltWFC) uses the 14-digit datetime and a final CRLF; Gen 5 (Entralinked) uses 12 digits and none.
        game = post.get("gamecd", "") or post.get("gsbrcd", "")
        gen5 = game.upper().startswith("IRA")
        body = encode_form(self._dated(reply, gen5))
        return Response(200, body if gen5 else body + b"\r\n", NAS_HEADERS, version="1.1")

    @staticmethod
    def _dated(values: dict, gen5: bool) -> dict:
        """Gen 5: ``returncd`` first, then ``datetime``. Gen 4: the fields, then ``datetime`` (AltWFC's order)."""
        stamp = time.strftime("%y%m%d%H%M%S" if gen5 else "%Y%m%d%H%M%S")
        if not gen5:
            return {**values, "datetime": stamp}
        out = {}
        for key, value in values.items():
            out[key] = value
            if key == "returncd":
                out.setdefault("datetime", stamp)
        out.setdefault("datetime", stamp)
        return out

    def unknown(self, post: dict) -> dict:
        log.warning("NAS: unknown action %r", post.get("action"))
        return {"returncd": "109", "retry": "1"}

    def acctcreate(self, post: dict) -> dict:
        userid = self.sessions.next_userid()
        log.info("NAS account: userid %s, game %s", userid, post.get("gsbrcd", "?"))
        return {"returncd": "002", "retry": "0", "userid": userid}

    def login(self, post: dict) -> dict:
        userid = post.get("userid", "")
        challenge = "".join(secrets.choice(ALNUM) for _ in range(8))
        post["challenge"] = challenge
        token = self.sessions.new(post, userid)
        log.info("NAS login: userid %s, game %s, name %r", userid, post.get("gsbrcd", "?"),
                 post.get("ingamesn", post.get("devname", "")))
        return {"returncd": "001", "retry": "0", "locator": "gamespy.com", "challenge": challenge, "token": token}

    def svcloc(self, post: dict) -> dict:
        svc = post.get("svc", "")
        gamecd = post.get("gamecd", "") or post.get("gsbrcd", "")
        gen5 = gamecd.upper().startswith("IRA")
        token = self.sessions.new(post)
        log.info("NAS svcloc: svc=%s gamecd=%s%s -> %s", svc, gamecd, " (Gen 5)" if gen5 else "", self.dls_host)
        if svc == "0000":
            return {"returncd": "001" if gen5 else "007", "retry": "0", "statusdata": "Y", "svchost": "n/a",
                    "servicetoken": token}
        if gen5:
            # Entralinked's Gen 5 answer: no retry field, returncd 001, the credential under servicetoken.
            return {"returncd": "001", "statusdata": "Y", "svchost": self.dls_host, "servicetoken": token}
        answer = {"returncd": "007", "retry": "0", "statusdata": "Y", "svchost": self.dls_host}
        if svc == "9000":
            answer["token"] = token
        else:
            answer["servicetoken"] = token
        return answer

    def profanity(self, post: dict) -> dict:
        count = len(post.get("words", "").split("\t"))
        accepted = "0" * count                    # 0: not profane
        reply = {"returncd": "000", "prwords": accepted}
        for region in "ACEJKP":
            reply["prwords" + region] = accepted
        return reply
