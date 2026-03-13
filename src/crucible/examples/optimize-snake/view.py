"""Snake game viewer — watch the AI agent play in real time.

Usage:
    python3 view.py

Controls: close window to quit.
"""

import random
import sys
import time

import pygame

from game import SnakeGame
from agent import choose_move

# ── Layout ────────────────────────────────────────────────────────────────────

CELL = 50          # pixels per cell
BOARD = 10         # cells per side
INFO_H = 60        # height of info bar below board
W = CELL * BOARD   # 500
H = CELL * BOARD + INFO_H  # 560
FPS = 10

# ── Colours ───────────────────────────────────────────────────────────────────

BG        = ( 30,  30,  30)
GRID      = ( 50,  50,  50)
HEAD      = (  0, 200,  80)
BODY      = (  0, 150,  60)
FOOD      = (220,  60,  60)
WHITE     = (255, 255, 255)
GRAY      = (180, 180, 180)


# ── Drawing ───────────────────────────────────────────────────────────────────

def cell_rect(r, c):
    return pygame.Rect(c * CELL + 1, r * CELL + 1, CELL - 2, CELL - 2)


def draw_board(surf, game):
    surf.fill(BG)
    for i in range(BOARD + 1):
        pygame.draw.line(surf, GRID, (i * CELL, 0), (i * CELL, CELL * BOARD))
        pygame.draw.line(surf, GRID, (0, i * CELL), (CELL * BOARD, i * CELL))
    if game.food:
        pygame.draw.rect(surf, FOOD, cell_rect(*game.food))
    for seg in list(game.snake)[1:]:
        pygame.draw.rect(surf, BODY, cell_rect(*seg))
    if game.snake:
        pygame.draw.rect(surf, HEAD, cell_rect(*game.snake[0]))


def draw_info(surf, font, game):
    bar = pygame.Rect(0, CELL * BOARD, W, INFO_H)
    pygame.draw.rect(surf, (20, 20, 20), bar)
    score = game.food_eaten * 10 + game.steps * 0.1
    text = f"Score: {score:.1f}   Food: {game.food_eaten}   Steps: {game.steps}"
    label = font.render(text, True, WHITE)
    surf.blit(label, label.get_rect(center=(W // 2, CELL * BOARD + INFO_H // 2)))


def draw_gameover(surf, font_big, score):
    overlay = pygame.Surface((W, CELL * BOARD), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 160))
    surf.blit(overlay, (0, 0))
    msg = font_big.render("Game Over", True, WHITE)
    sub = font_big.render(f"Score {score:.1f}", True, GRAY)
    surf.blit(msg, msg.get_rect(center=(W // 2, CELL * BOARD // 2 - 24)))
    surf.blit(sub, sub.get_rect(center=(W // 2, CELL * BOARD // 2 + 24)))


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_game(surf, clock, font, font_big):
    seed = random.randint(0, 2**31)
    game = SnakeGame(seed=seed)

    while not game.done:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False

        try:
            direction = choose_move(game.snake, game.food, game.board_size)
        except Exception:
            moves = game.legal_moves()
            direction = random.choice(moves) if moves else 'UP'

        legal = game.legal_moves()
        if direction not in legal:
            direction = random.choice(legal) if legal else direction

        game.step(direction)
        draw_board(surf, game)
        draw_info(surf, font, game)
        pygame.display.flip()
        clock.tick(FPS)

    score = game.food_eaten * 10 + game.steps * 0.1
    draw_gameover(surf, font_big, score)
    pygame.display.flip()

    deadline = time.time() + 1.0
    while time.time() < deadline:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
        clock.tick(30)

    return True


def main():
    pygame.init()
    surf = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Snake AI Viewer")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("monospace", 18)
    font_big = pygame.font.SysFont("monospace", 32, bold=True)

    while True:
        if not run_game(surf, clock, font, font_big):
            break

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
