from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

project = "pynixd"
copyright = "2025, Carl Andersson"  # noqa: A001 - required Sphinx config name
author = "Carl Andersson"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {
    "member-order": "bysource",
}

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_rtype = False

smartquotes = False

intersphinx_mapping = {} if os.environ.get("PYNIXD_DOCS_OFFLINE") else {"python": ("https://docs.python.org/3", None)}
