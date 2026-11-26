"""The opt-in usage stats (docs/spec.md, Usage stats): what the gateway records, its file and the
summary of it.

Only the gateway writes the file, the worker only reports its names and an exception's type and
frames to it (report.py). The package re-exports nothing, its modules are imported by name
(`from bwtidy.stats.recorder import Recorder`).
"""
