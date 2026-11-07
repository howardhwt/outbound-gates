"""The command was called og before 0.10.0; it is gate now (gates/gate.py).

An og launcher written by an older setup still imports gates.og from the newest plugin, so this stays: it lets
`og propose refresh` and `og yes CODE` run once more, and that refresh installs the gate command and removes og.
"""
from gates.gate import main  # noqa: F401
