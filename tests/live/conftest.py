"""Only aggregate live statistics; never print model identifiers or response bodies."""
import sys


def pytest_terminal_summary(terminalreporter):
    module = sys.modules.get("tests.live.test_live_azure")
    if module is None:
        module = next((m for name, m in sys.modules.items() if name.endswith("test_live_azure")), None)
    if module is not None and module.calls:
        latencies = module.latencies
        mean = sum(latencies) / len(latencies) if latencies else 0
        terminalreporter.write_line(
            f"Live summary: calls={len(module.calls)}, successful={len(latencies)}, "
            f"http_requests={module.http_requests}, mean_latency_ms={mean:.1f}"
        )
