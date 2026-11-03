"""Disruption brief: a one-page institutional note written from a stored run.

Every number in a brief is read from the engine's own output (the run, the
lane planner, a Monte Carlo job, a shipment exposure summary, the feed
board) and registered in a facts table (F1..Fn). The writer (Meta AI, or the
deterministic recorded writer) produces prose around placeholders such as
{F3}; the server fills them from the table, and a verifier rejects any
literal number, place or source that is not in the table, and any causal,
accuracy, prediction, forecast or likelihood wording.
"""
