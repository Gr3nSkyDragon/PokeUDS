"""The smallest plugin: one tool, one script. Copy this folder to start your own (docs/plugins.md)."""

from pokeuds.plugins import Field, Tool


def register(api):
    api.add_tool("rby", Tool(
        "hello-greet",                          # unique among every tool
        "Say hello",                            # its name on the Trade page
        "greet.py",                             # its script, next to this file
        "Greets your trainer in the session log.",
        ("Nothing to do on the console.", "Press Start and read the log."),
        fields=(Field("--greeting", "Greeting", default="Hello", help="The word the tool starts with."),
                Field("--times", "How many times", kind="number", default=1),
                Field("--shout", "Shout", kind="switch")),
        fixed=("--trainer", "{ot}"),            # the trainer name from Settings
    ))
