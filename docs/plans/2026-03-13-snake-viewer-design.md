# Snake Viewer Design

Date: 2026-03-13

## Summary

Single-file pygame viewer for the optimize-snake agent. Lives in the experiment
project directory alongside game.py and agent.py.

## Decisions

| Parameter | Choice |
|-----------|--------|
| Framework | pygame |
| Speed | Fixed 10 FPS |
| Controls | None (view-only) |
| Restart | Auto after 1s delay, infinite loop |

## File

`crucible_projects/test-snake/view.py` — standalone script, no new dependencies
in crucible repo.

## Layout

- Window: 500×560px
- Board: 10×10 grid, each cell 50×50px (500×500px)
- Info bar: 60px below board — Score / Food / Steps

## Colors

| Element | Color |
|---------|-------|
| Background | #1e1e1e |
| Grid lines | #323232 |
| Snake head | #00c850 |
| Snake body | #009640 |
| Food | #dc3c3c |
| Text | white |

## Game Loop

1. Create `SnakeGame(seed=random)` and import `choose_move` from agent
2. Each tick: call `choose_move`, call `game.step`, redraw
3. On `game.done`: show "Game Over" overlay, wait 1s, restart
4. `pygame.QUIT` event → exit

## Usage

```bash
cd ~/Documents/Hack/crucible_projects/test-snake
pip install pygame
python3 view.py
```
