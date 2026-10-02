# UK Place Activities: casino + home build checklist

The activity grows from two daily puzzles into a home screen with the daily puzzles and all
13 house casino games. Mockups: Paper file "Optimistic quartz". The bot keeps every
`/blackjack`-style command; the activity is a second way in that reuses the same game logic
and money paths.

Rules that hold for every step:
- The server deals. The page only sends moves and plays back results it is given, so no
  deck order, mine layout, bridge path or spin result reaches the browser early.
- Stakes and payouts go through each game's existing functions and reason strings, so the
  bank's per-game P/L, `casino_results`, badges and the economy log all keep working.
- Card games and Roulette sit on the bot's felt. The rest keep their own scenes. Glass
  Bridge uses the bot's own board render.
- Animations play out a result the server already decided, and respect reduced motion.

## Phase 0 - bot foundations
- [x] 1. Casino adapter framework in `lib/activities/casino/`: game registry, bet limits
      (config min/max + `max_casino_bet`), balance check, stake, one in-play game per
      player per game, per-player lock, drain counter, in-play games saved to disk so a
      reopen resumes the hand.
- [x] 2. API: `GET /home` (balance, daily puzzle status, casino games ordered by last played
      from `casino_results`), `GET /casino/<game>` (resume or idle), `POST /casino/<game>/<action>`.
- [x] 3. #casino posts: one live session line per player (posted on first round, edited as
      they play, finalised when idle), plus a separate post for big wins with a Play button.
- [x] 4. Launching: `/games` on the activities bot opens Home; entry point opens Home;
      Play buttons on casino posts open that game.
- [x] 5. Tests for the framework, sessions and posts.

## Phase 1 - activity foundations
- [x] 6. Router and Home screen: daily cards with today's status, casino section in
      last-played order (jump back in, two tiles, then the full list).
- [x] 7. Casino shell: top bar (back, title, balance, rules button), rail (prompt, ledger),
      action bar, bet picker, rules sheet, toasts, error states.
- [x] 8. Animation kit: cards (deal, flip), count-up numbers, chips sliding, shake, win
      shimmer, reduced-motion fallbacks.
- [x] 9. `?dev` mock API covering home and every game, for previewing outside Discord.

## Phase 2 - games (each: adapter + tests + screen + animations + rules)
All 13 adapters are done and tested on the bot side; the boxes below track the screens.
- [x] 10. Blackjack (reference implementation)
- [x] 11. Higher or Lower
- [x] 12. Video Poker
- [x] 13. Red Dog
- [x] 14. 3-Card Poker
- [x] 15. Roulette (solo spin, same bets and payouts as the table)
- [x] 16. Fruit Machine
- [x] 17. Mines
- [x] 18. Chest Upgrade
- [x] 19. Glass Bridge
- [x] 20. Blockade Run
- [x] 21. Darts
- [x] 22. Penalties

## Phase 3 - ship
- [x] 23. Full pass in the browser preview (mock) on phone and desktop sizes.
- [x] 24. Local API smoke test against a throwaway database (scripts/dev_activity_api.py,
      then http://localhost:5174/?local=<token>): every game played against the real code.
- [ ] 25. Deploy bot + frontend (after a yes), then play every game once in Discord and
      check the #casino posts land.
