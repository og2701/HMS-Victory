"""Climb HMS Victory: the activity's Doodle Jump style climb up the rigging. The money, receipts,
leaderboard and channel posts are the daily score games' (daily_score.py); this keeps the names
the rest of the bot already uses."""

from lib.activities.daily_score import CLIMB as GAME
from lib.activities.daily_score import Refuse  # noqa: F401

state, start, finish, board = GAME.state, GAME.start, GAME.finish, GAME.board
home_card, opened, post_best, seed_for, rank = GAME.home_card, GAME.opened, GAME.post_best, GAME.seed_for, GAME.rank
