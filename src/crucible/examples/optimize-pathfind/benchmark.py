import random
from pathfind import find_path


def generate_maze(rows, cols, seed):
    random.seed(seed)
    grid = [[0] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            if random.random() < 0.15 and (r, c) not in ((0, 0), (rows - 1, cols - 1)):
                grid[r][c] = 1
    return grid


SIZE = 20
total_nodes = 0
mazes_solved = 0

for seed in range(100):
    grid = generate_maze(SIZE, SIZE, seed)
    path, nodes = find_path(grid, (0, 0), (SIZE - 1, SIZE - 1))
    if path is not None:
        total_nodes += nodes
        mazes_solved += 1

if mazes_solved < 80:
    # Penalise if algorithm fails to solve most mazes
    total_nodes = 999_999

print(f"nodes_explored: {total_nodes}")
