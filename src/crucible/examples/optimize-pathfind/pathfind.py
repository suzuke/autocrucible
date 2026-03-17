from collections import deque


def find_path(grid, start, end):
    """BFS pathfinding. Returns (path, nodes_explored)."""
    rows, cols = len(grid), len(grid[0])
    queue = deque([(start, [start])])
    visited = {start}
    nodes = 0

    while queue:
        (r, c), path = queue.popleft()
        nodes += 1
        if (r, c) == end:
            return path, nodes
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = r + dr, c + dc
            if (0 <= nr < rows and 0 <= nc < cols
                    and grid[nr][nc] == 0
                    and (nr, nc) not in visited):
                visited.add((nr, nc))
                queue.append(((nr, nc), path + [(nr, nc)]))

    return None, nodes
