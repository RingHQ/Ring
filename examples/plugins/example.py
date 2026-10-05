"""Example plugin: a "small and centered" action and a log of what Ring does.

To try it, copy this file to ~/.config/looplinux/plugins/, enable "example"
in the settings window (Plugins), and put the action "example.small" on a key
or a direction.
"""

import logging

from looplinux.actions import Rect

log = logging.getLogger("looplinux.example")


def small(context):
    """Make the window 60% of the screen, in the middle of it."""
    area = context.area
    width, height = round(area.width * 0.6), round(area.height * 0.6)
    return Rect(
        area.x + (area.width - width) // 2,
        area.y + (area.height - height) // 2,
        width,
        height,
    )


def applied(action, window, rect):
    log.info("%s was applied to %s", action, window.app_id if window else "?")


def register(api):
    api.add_action("small", small, label="Small and centered")
    api.on("action_applied", applied)
