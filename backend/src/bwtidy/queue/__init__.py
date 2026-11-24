"""The review queue: every edit as an entry, checked on arrival and approved into the write path.

ReviewQueue (review.py) is the facade the rest of the app calls, the modules beside it hold one
concern each. The package re-exports nothing, its modules are imported by name
(`from bwtidy.queue.review import ReviewQueue`).
"""
