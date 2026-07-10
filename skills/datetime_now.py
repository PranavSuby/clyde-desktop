"""Current date/time — models never know this reliably on their own."""

SKILL = {
    "name": "current_datetime",
    "description": (
        "Get the current date and time on this PC. Use whenever the user "
        "asks about today's date, the current time, day of week, or "
        "anything relative like 'how many days until ...'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "Optional IANA timezone like 'America/New_York'; "
                               "defaults to the system timezone.",
            },
        },
        "required": [],
    },
}


def run(args: dict) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = args.get("timezone")
    now = datetime.now(ZoneInfo(tz)) if tz else datetime.now().astimezone()
    return now.strftime("%A, %B %d %Y, %H:%M:%S %Z (ISO: %Y-%m-%dT%H:%M:%S%z)")
