"""Fixed visual vocabulary: the 3.2.2 figure palette, group styles, legend and caption texts.
Every string here is a fixed template (acceptance 8)."""

from __future__ import annotations

FONT_FAMILY = "DejaVu Sans, Arial, Helvetica, sans-serif"
TEXT_COLOR = "#232F3E"
SECONDARY_COLOR = "#5F6B7A"
EDGE_COLOR = "#232F3E"
DENIED_COLOR = "#C7243A"
DASH = "6 4"
OPTIONAL_COLOR = "#5A6B86"

#: C29: the fixed caption on every response and artefact.
CAPTION = "Drawn from knowledge-base diagram facts, not from live deployment state."
LEGEND_TITLE = "Legend"
LEGEND_SOLID = "solid: always on"
LEGEND_DASHED = "dashed: optional, fork-added, or configuration-dependent"
LEGEND_DENIED = "red dashed: denied"
LEGEND_OUTLINE = "dashed outline: opt-in or configuration-dependent resource"

GROUP_TYPES = ("aws-cloud", "account", "region", "vpc", "private-subnet", "public-subnet",
               "corporate-dc", "generic")

#: Nesting rank: aws-cloud > account > region > vpc > *-subnet. corporate-dc and generic go anywhere.
GROUP_RANK = {"aws-cloud": 0, "account": 1, "region": 2, "vpc": 3, "private-subnet": 4,
              "public-subnet": 4}

#: (stroke colour, dashed) per group type, from the committed 3.2.2 figures. public-subnet and
#: account use the AWS group-icon colours read from the catalog when present (fallbacks here).
GROUP_STYLE = {
    "aws-cloud": ("#242F3E", False),
    "region": ("#00A4A6", True),
    "vpc": ("#8C4FFF", False),
    "private-subnet": ("#00A4A6", False),
    "public-subnet": ("#7AA116", False),
    "corporate-dc": ("#7D8998", False),
    "account": ("#E7157B", False),
    "generic": ("#5A6B86", False),   # a grouping: solid, so dashed always means opt-in
}

#: catalog keys of the group icons (first available wins); generic has none.
GROUP_ICON_KEYS = {
    "aws-cloud": ("AWSCloudLogo", "AWSCloud"),
    "region": ("Region",),
    "vpc": ("VirtualPrivateCloudVPC",),
    "private-subnet": ("PrivateSubnet",),
    "public-subnet": ("PublicSubnet",),
    "corporate-dc": ("CorporateDataCenter",),
    "account": ("AWSAccount",),
    "generic": (),
}

#: The kit-authored generic icon (no AWS artwork): a rounded square outline.
GENERIC_ICON_SVG = (
    '<rect x="4" y="4" width="40" height="40" rx="6" ry="6" fill="none" '
    'stroke="#5A6B86" stroke-width="2"/>'
)
GENERIC_ICON_VIEWBOX = "0 0 48 48"
