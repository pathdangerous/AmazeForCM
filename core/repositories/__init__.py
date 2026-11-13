"""Repositories: the only code that reads and writes the domain tables. Each takes a database session,
flushes but never commits (the caller owns the transaction), and raises named errors (errors.py)."""
