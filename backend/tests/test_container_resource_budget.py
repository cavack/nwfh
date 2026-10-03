from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_memory_budget_matches_multi_exchange_runtime_and_bounds_arenas() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    backend = compose.split("  frontend:", 1)[0]

    assert 'MALLOC_ARENA_MAX: "2"' in backend
    assert "memory: 4G" in backend
    assert "pids: 100" in backend
